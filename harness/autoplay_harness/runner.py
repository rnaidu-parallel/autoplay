from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .bridge import BridgeError, NamedPipeBridge
from .capture import CaptureError, Frame, ScreenCapture
from .farming import nearest_empty_tiles, plant_seeds
from .objectives import ObjectiveError, ObjectiveLedger, evaluate_state_condition
from .openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from .prompts import ACTOR_SYSTEM_PROMPT, DIRECTOR_SYSTEM_PROMPT
from .recording import GameplayRecorder
from .supervisor import GameSupervisor
from .state_actor import STATE_ACTOR_PROMPT, STATE_ACTOR_TOOLS, can_use_state_actor, compact_state, prompt_ledger
from .telemetry import Telemetry
from .tools import ACTOR_TOOLS, DIRECTOR_TOOLS
from .wiki import StardewWiki


class HarnessError(RuntimeError):
    pass


class AutoplayHarness:
    def __init__(
        self,
        repository_root: Path,
        objective: str,
        success_condition: str,
        model: str,
        api_key: str,
        max_actions: int = 10,
        max_decisions: int = 15,
        director_interval: int = 12,
        launch_game: bool = True,
        save_frames: bool = False,
        keep_game_open: bool = False,
        continuous: bool = False,
        record_video: bool = False,
        video_segment_minutes: int = 5,
        video_retention_segments: int = 6,
        reasoning_effort: str | None = "low",
        isolated_state: bool = False,
        actor_mode: str = "state-first",
    ) -> None:
        if max_actions < 1 or max_decisions < 1 or director_interval < 1:
            raise HarnessError("max_actions, max_decisions, and director_interval must be positive.")
        self.repository_root = repository_root
        self.run_id = str(uuid.uuid4())
        self.run_directory = repository_root / "harness" / "runs" / self.run_id
        self.state_directory = self.run_directory / "state" if isolated_state else repository_root / "harness" / "state"
        self.max_actions = max_actions
        self.max_decisions = max_decisions
        self.director_interval = director_interval
        self.game_actions = 0
        self.last_director_action_count = 0
        self.actor_mode = actor_mode
        self.inspect_next_scene = False
        self.decisions = 0
        self.stop_reason: str | None = None
        self.keep_game_open = keep_game_open
        self.continuous = continuous
        self.title_screen_ready = False
        self.latest_wiki_results: list[dict[str, Any]] = []
        self.blocked_movements: set[tuple[str | None, int | None, int | None, tuple[str, ...]]] = set()
        self.last_action_fingerprint: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.director_feedback: str | None = None
        self.director_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="director")
        self.director_future: Future | None = None
        self.director_snapshot: str | None = None
        self.recorder = (
            GameplayRecorder(
                self.run_directory / "video",
                video_segment_minutes,
                video_retention_segments,
            )
            if record_video
            else None
        )

        game_directory = Path(
            os.environ.get(
                "AUTOPLAY_GAME_DIRECTORY",
                r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley",
            )
        )
        self.supervisor = GameSupervisor(game_directory, launch_if_needed=launch_game)
        self.bridge: NamedPipeBridge | None = None
        self.capture = ScreenCapture(
            save_directory=(self.run_directory / "frames") if save_frames else None
        )
        self.telemetry = Telemetry(self.run_directory)
        self.ledger = ObjectiveLedger(
            self.state_directory / "objectives.json",
            initial_goal=objective,
            initial_success_condition=success_condition,
        )
        self.wiki = StardewWiki()
        self.client = OpenRouterClient(
            api_key=api_key,
            model=model,
            run_id=self.run_id,
            reasoning_effort=reasoning_effort,
        )

    def run(self) -> str:
        self.bridge = self.supervisor.connect_bridge()
        self.telemetry.record(
            "session_started",
            {
                "run_id": self.run_id,
                "model": self.client.model,
                "reasoning_effort": self.client.reasoning_effort,
                "actor_mode": self.actor_mode,
                "provider_policy": self.client.PROVIDER_PREFERENCES[self.client.model],
                "initial_objective": self.ledger.snapshot().get("active"),
                "isolated_state": self.state_directory == self.run_directory / "state",
            },
        )
        try:
            self._observe()
            display_response = self.bridge.request("set_display_mode", mode="fullscreen")
            if display_response.get("status") != "completed":
                raise HarnessError(f"Could not enable full-screen mode: {display_response}")
            time.sleep(3)
            self._ensure_world_loaded()
            consecutive_errors = 0
            while not self.stop_reason and (self.continuous or (self.game_actions < self.max_actions and self.decisions < self.max_decisions)):
                try:
                    self._finish_director_review()
                    if self.ledger.snapshot().get("active") is None:
                        # New goal selection needs a result; routine reviews never block the actor.
                        if self.director_future is not None:
                            self._finish_director_review(wait=True)
                        self._director_review()
                    elif self.director_future is None and self.game_actions - self.last_director_action_count >= self.director_interval:
                        self._start_director_review()
                    if self.ledger.snapshot().get("active") is None:
                        continue
                    self._actor_step()
                    consecutive_errors = 0
                except (BridgeError, CaptureError, OpenRouterError, ObjectiveError, KeyError, TypeError, ValueError) as error:
                    consecutive_errors += 1
                    self.telemetry.record(
                        "recoverable_error",
                        {
                            "error_type": type(error).__name__,
                            "message": str(error),
                            "consecutive": consecutive_errors,
                            "traceback": traceback.format_exc()[-2000:],
                        },
                        include_recent=False,
                    )
                    if consecutive_errors >= 5:
                        self.stop_reason = f"recovery_exhausted:{type(error).__name__}"
                        break
                    self._recover(error)
                    time.sleep(min(2 ** (consecutive_errors - 1), 30))
            if not self.stop_reason and not self.continuous:
                self.stop_reason = "max_decisions_reached" if self.decisions >= self.max_decisions else "max_actions_reached"
            self.telemetry.record("session_stopped", {"reason": self.stop_reason})
            self.telemetry.save_summary()
            return self.stop_reason
        finally:
            if self.recorder is not None:
                self.recorder.stop()
            if self.bridge is not None:
                if self.keep_game_open:
                    try:
                        self.bridge.observe()
                    except Exception:
                        pass
                else:
                    try:
                        self.bridge.request("set_display_mode", mode="windowed")
                        time.sleep(1)
                    except Exception:
                        pass
                    try:
                        self.bridge.request("stop")
                    except Exception:
                        pass
                self.bridge.close()
            self.capture.release()
            if self.keep_game_open:
                self.supervisor.wait_for_started_game()
            else:
                self.supervisor.stop_started_game()
            self.client.cancelled.set()
            self.director_executor.shutdown(wait=True, cancel_futures=True)
            self._finish_director_review(apply=False)
            self.telemetry.save_summary()

    def _ensure_world_loaded(self) -> None:
        assert self.bridge is not None
        for _ in range(12):
            response = self.bridge.observe()
            state = response.get("state") or {}
            if state.get("worldReady"):
                return
            menu = state.get("menu", "")
            width = state.get("viewportWidth") or 1920
            height = state.get("viewportHeight") or 1080

            if menu == "TitleMenu":
                action = self.bridge.request(
                    "click",
                    x=round(width * 0.435),
                    y=round(height * 0.90),
                    button="left",
                )
                self.telemetry.record("bootstrap_action", {"action": "open_load_menu", "result": action})
                time.sleep(3)
                continue
            if menu == "TitleMenu:LoadGameMenu":
                time.sleep(2)
                action = self.bridge.request(
                    "click",
                    x=round(width * 0.50),
                    y=round(height * 0.28),
                    button="left",
                )
                self.telemetry.record("bootstrap_action", {"action": "load_first_save", "result": action})
                loaded = self.bridge.request("wait", field="world_ready", value="true", ticks=600)
                if (loaded.get("state") or {}).get("worldReady"):
                    return
                time.sleep(2)
                continue

            action = self.bridge.request("press", buttons=["Escape"])
            self.telemetry.record(
                "bootstrap_action",
                {"action": "escape_wrong_title_submenu", "menu": menu, "result": action},
            )
            time.sleep(2)
        raise HarnessError("Could not load the existing save from the title screen after recovery attempts.")

    def _observe(self) -> tuple[dict[str, Any], Frame]:
        assert self.bridge is not None
        observe_started = time.perf_counter()
        for attempt in range(21):
            response = self.bridge.observe()
            if response.get("status") != "completed":
                raise HarnessError(f"Observation failed: {response}")
            try:
                capture_started = time.perf_counter()
                frame = self.capture.capture()
                capture_ms = (time.perf_counter() - capture_started) * 1000
            except CaptureError:
                if attempt == 20:
                    raise
                time.sleep(0.25)
                continue
            state = response.get("state") or {}
            title_splash = state.get("menu") == "TitleMenu" and (
                frame.visual_variance < 5 or frame.visual_brightness > 215
            )
            if not title_splash:
                if state.get("menu") == "TitleMenu" and not self.title_screen_ready:
                    time.sleep(22)
                    response = self.bridge.observe()
                    if response.get("status") != "completed":
                        raise HarnessError(f"Observation failed: {response}")
                    frame = self._capture_focused_frame()
                    state = response.get("state") or {}
                    self.title_screen_ready = True
                break
            if attempt == 2:
                self.bridge.request("press", buttons=["Escape"])
                time.sleep(1)
            if attempt == 20:
                raise HarnessError(
                    "The title-screen splash did not finish rendering within 20 seconds: "
                    f"variance={frame.visual_variance:.2f}, brightness={frame.visual_brightness:.2f}, "
                    f"state={state}."
                )
            time.sleep(1)
        self.telemetry.record(
            "observation",
            {
                "frame_id": frame.frame_id,
                "width": frame.width,
                "height": frame.height,
                "visual_variance": frame.visual_variance,
                "visual_brightness": frame.visual_brightness,
                "observe_ms": round((time.perf_counter() - observe_started) * 1000),
                "capture_ms": round(capture_ms),
                "state": state,
            },
            include_recent=False,
        )
        if self.recorder is not None and self.recorder.process is None and state.get("worldReady"):
            self.recorder.start()
            self.telemetry.record("recording_started", {"directory": str(self.recorder.directory)})
        return state, frame

    def _capture_focused_frame(self) -> Frame:
        assert self.bridge is not None
        last_error: CaptureError | None = None
        for _ in range(6):
            response = self.bridge.observe()
            if response.get("status") != "completed":
                raise HarnessError(f"Observation failed: {response}")
            try:
                return self.capture.capture()
            except CaptureError as error:
                last_error = error
                time.sleep(0.25)
        assert last_error is not None
        raise last_error

    def _actor_step(self) -> None:
        cycle_started = time.perf_counter()
        self.telemetry.step += 1
        state, frame = self._observe()
        if self._complete_verified_objective(state):
            return
        state_only = (getattr(self, "actor_mode", "visual") == "state-first"
                      and not self.inspect_next_scene and can_use_state_actor(state))
        self.inspect_next_scene = False
        context = self._context("actor", state, frame, state_only=state_only)
        self.decisions += 1
        decision, model_ms = self._decide(STATE_ACTOR_PROMPT if state_only else ACTOR_SYSTEM_PROMPT,
                                         context, frame, STATE_ACTOR_TOOLS if state_only else ACTOR_TOOLS, "actor")
        fingerprint = self._action_fingerprint(decision, state)
        execute_started = time.perf_counter()
        if fingerprint == self.last_action_fingerprint:
            result = {
                "status": "rejected",
                "error": "The identical action was already attempted with unchanged observable state; choose a different action.",
            }
        else:
            self.last_action_fingerprint = fingerprint
            result = self._execute_actor_tool(decision, frame, state)
        bridge_ms = round((time.perf_counter() - execute_started) * 1000)
        self.last_result = {"tool": decision.name, "status": result.get("status")}
        for key in ("error", "warning", "reason"):
            if key in result:
                self.last_result[key] = result[key]
        self.telemetry.record("tool_result", {"tool": decision.name, "bridge_ms": bridge_ms, "result": result,
                                             "actor_cycle_ms": round((time.perf_counter() - cycle_started) * 1000)})
        self._print_step("actor", decision, result, (result.get("state") or state), model_ms, bridge_ms)
        if result.get("state"):
            self._complete_verified_objective(result["state"])
        if not self.continuous and self.decisions >= self.max_decisions and not self.stop_reason:
            self.stop_reason = "max_decisions_reached"

    def _complete_verified_objective(self, state: dict[str, Any]) -> bool:
        active = self.ledger.snapshot().get("active")
        evaluation = evaluate_state_condition(active["success_condition"], state) if active else None
        if evaluation is not None and evaluation[0]:
            evidence = (
                f"Harness verified `{active['success_condition']}` against structured state: "
                + ", ".join(f"{key}={state.get(key)!r}" for key in ("location", "time", "day", "menu", "playerFree"))
            )
            self.ledger.complete_objective(evidence)
            self.telemetry.record(
                "objective_completed",
                {"objective_id": active["id"], "evidence": evidence},
            )
            print(f"[{self.telemetry.step}] objective {active['id']} completed by harness", flush=True)
            return True
        return False

    def _director_review(self) -> None:
        self.telemetry.step += 1
        state, frame = self._observe()
        context = self._context("director", state, frame)
        decision, model_ms = self._decide(DIRECTOR_SYSTEM_PROMPT, context, frame, DIRECTOR_TOOLS, "director")
        self._apply_director_decision(decision, state)
        self.last_director_action_count = self.game_actions
        self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)

    def _director_state_key(self, state: dict[str, Any]) -> str:
        # A routine review recommends outcomes, not cursor coordinates or a movement sequence.
        # Invalidate it on objective, resource, crop, menu, location, or day changes.
        fields = ("worldReady", "location", "day", "season", "year", "menu", "eventUp",
                  "inventory", "plantedCrops", "wateredCrops", "harvestableCrops", "money", "health")
        return json.dumps({"active": self.ledger.snapshot().get("active"),
                           "state": {key: state.get(key) for key in fields},
                           "stamina_low": (state.get("stamina") or 0) < 30}, sort_keys=True)

    def _start_director_review(self) -> None:
        state, frame = self._observe()
        self.director_snapshot = self._director_state_key(state)
        context = self._context("director", state, frame)
        self.last_director_action_count = self.game_actions
        self.telemetry.record("director_review_started", {"game_actions": self.game_actions}, include_recent=False)
        # The worker receives immutable strings. Only the main thread accesses the game and ledger.
        self.director_future = self.director_executor.submit(
            self._choose, DIRECTOR_SYSTEM_PROMPT, context, frame, DIRECTOR_TOOLS, "director"
        )

    def _finish_director_review(self, wait: bool = False, apply: bool = True) -> None:
        future = self.director_future
        if future is None or (not wait and not future.done()):
            return
        self.director_future = None
        if future.cancelled():
            return
        try:
            decision, model_ms = future.result()
        except OpenRouterError as error:
            self._record_model_error("director", error)
            return
        state = self._observe()[0] if apply else {}
        active = self.ledger.snapshot().get("active")
        evaluation = evaluate_state_condition(active["success_condition"], state) if active and apply else None
        compatible = apply and active is not None and self.director_snapshot == self._director_state_key(state)
        # Blocking is a synchronous decision: old evidence must never stop current work.
        accepted = compatible and not (evaluation and evaluation[0]) and decision.name == "continue_objective"
        self._record_decision("director", decision, model_ms, applied=bool(accepted))
        if accepted:
            self._apply_director_decision(decision, state)
        self.telemetry.record("director_review_finished", {
            "status": "applied" if accepted else "discarded", "game_actions": self.game_actions,
            "reason": None if accepted else "stale_snapshot_or_non_routine_decision",
        }, include_recent=False)
        self._print_step("director", decision, {"status": "applied" if accepted else "discarded"}, state, model_ms, 0)

    def _decide(
        self,
        system_prompt: str,
        context: str,
        frame: Frame,
        tools: list[dict[str, Any]],
        role: str,
    ) -> tuple[ToolDecision, int]:
        self.telemetry.record("model_request", {"role": role, "input_image": tools is not STATE_ACTOR_TOOLS,
                                               "context_chars": len(context), "tools_count": len(tools)}, include_recent=False)
        try:
            decision, model_ms = self._choose(system_prompt, context, frame, tools, role)
        except OpenRouterError as error:
            self._record_model_error(role, error)
            raise
        self._record_decision(role, decision, model_ms)
        return decision, model_ms

    def _choose(self, system_prompt: str, context: str, frame: Frame,
                tools: list[dict[str, Any]], role: str) -> tuple[ToolDecision, int]:
        started = time.perf_counter()
        stable_context = None
        if tools is STATE_ACTOR_TOOLS:
            packet = json.loads(context)
            stable_context = json.dumps({"objective_ledger": packet.pop("objective_ledger")}, separators=(",", ":"))
            context = json.dumps(packet, separators=(",", ":"))
        decision = self.client.choose_tool(system_prompt, context, None if tools is STATE_ACTOR_TOOLS else frame.data_url,
                                           tools, cache_namespace=role, stable_context=stable_context)
        model_ms = round((time.perf_counter() - started) * 1000)
        return decision, model_ms

    def _record_model_error(self, role: str, error: OpenRouterError) -> None:
        self.telemetry.record("model_error", {"role": role, "error": str(error),
                                              "provider": next((a["provider"] for a in reversed(error.attempts) if a.get("provider")), None),
                                              "usage": error.usage, "attempts": error.attempts}, include_recent=False)

    def _record_decision(self, role: str, decision: ToolDecision, model_ms: int, applied: bool = True) -> None:
        self.telemetry.record(
            f"{role}_decision",
            {
                "tool": decision.name,
                "arguments": decision.arguments,
                "usage": decision.usage,
                "model": decision.model,
                "provider": decision.provider,
                "attempts": decision.attempts,
                "applied": applied,
                "model_ms": model_ms,
                "content": (decision.content or "")[:2000] or None,
                "reasoning": (decision.reasoning or "")[:2000] or None,
            },
        )

    def _print_step(
        self,
        role: str,
        decision: ToolDecision,
        result: dict[str, Any],
        state: dict[str, Any],
        model_ms: int,
        bridge_ms: int,
    ) -> None:
        arguments = json.dumps(decision.arguments, separators=(",", ":"))
        if len(arguments) > 90:
            arguments = arguments[:87] + "..."
        detail = result.get("error") or result.get("reason") or result.get("warning") or ""
        print(
            f"[{self.telemetry.step}] {role} {decision.name} {arguments} -> {result.get('status')} {detail[:60]} "
            f"| model {model_ms}ms bridge {bridge_ms}ms "
            f"| day {state.get('day')} {state.get('time')} {state.get('location')} stamina {state.get('stamina')}",
            flush=True,
        )

    def _apply_director_decision(self, decision: ToolDecision, state: dict[str, Any]) -> None:
        arguments = decision.arguments
        self.director_feedback = None
        if decision.name == "continue_objective":
            self.ledger.continue_objective(arguments["milestone"], arguments["reason"])
        elif decision.name == "block_objective":
            if self.continuous:
                active = self.ledger.snapshot().get("active")
                if active is not None:
                    reason = "Block rejected in continuous mode; adapt the milestone or control tactic instead."
                    self.ledger.continue_objective(active["milestone"], reason)
                    self.director_feedback = reason
                    self.telemetry.record(
                        "objective_block_rejected",
                        {"reason": reason, "evidence": arguments["evidence"]},
                    )
            else:
                self.ledger.block_objective(arguments["evidence"])
        elif decision.name == "set_objective":
            try:
                evaluation = evaluate_state_condition(arguments["success_condition"], state)
                if evaluation is not None and evaluation[0]:
                    raise ObjectiveError("The proposed success condition is already satisfied.")
                if evaluation is not None and any(" is unavailable" in item for item in evaluation[1]):
                    raise ObjectiveError("The proposed success condition uses an unavailable state field.")
                self.ledger.set_objective(
                    arguments["goal"],
                    arguments["success_condition"],
                    arguments["milestone"],
                )
            except ObjectiveError as error:
                self.director_feedback = (
                    f"Previous set_objective was rejected: {error} "
                    f"Rejected condition: {arguments['success_condition']!r}. Propose a different, currently false condition."
                )
                self.telemetry.record(
                    "objective_update_rejected",
                    {"reason": str(error), "arguments": arguments},
                )
        else:
            raise HarnessError(f"Unknown director tool: {decision.name}")

    def _execute_actor_tool(
        self,
        decision: ToolDecision,
        frame: Frame,
        before_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert self.bridge is not None
        name = decision.name
        arguments = decision.arguments
        if name == "inspect_scene":
            self.inspect_next_scene = True
            return {"status": "visual_review_requested"}
        if name in {"plant_seeds", "plant_nearest_seeds"}:
            tiles = (nearest_empty_tiles(before_state or {}, arguments["count"]) if name == "plant_nearest_seeds"
                     else arguments["tiles"])
            response = plant_seeds(self.bridge, before_state or {}, arguments["seed_slot"], tiles,
                                   len(tiles) * 3 if self.continuous else self.max_actions - self.game_actions)
            self.game_actions += response["controls_executed"]
            return response
        movement_key = self._movement_key(name, arguments, before_state)
        if movement_key is not None and movement_key in self.blocked_movements:
            return {
                "status": "rejected",
                "error": "That movement direction was already blocked at the current tile; choose a different direction.",
            }
        if name == "control_sequence":
            return self._execute_control_sequence(arguments["steps"], before_state)
        if name == "navigate_to":
            response = self.bridge.request("navigate", x=arguments["tile_x"], y=arguments["tile_y"], ticks=600)
        elif name == "go_to_location":
            response = self.bridge.request("go_to_location", location=arguments["location"], ticks=600)
        elif name == "press":
            response = self.bridge.request("press", buttons=arguments["buttons"])
        elif name == "hold":
            response = self.bridge.request("hold", buttons=arguments["buttons"], ticks=arguments["ticks"])
        elif name == "move_cursor":
            self._require_current_frame(arguments, frame)
            x, y = self._pixels(arguments["x"], arguments["y"], frame)
            response = self.bridge.request("move_cursor", x=x, y=y)
        elif name == "click":
            self._require_current_frame(arguments, frame)
            x, y = self._pixels(arguments["x"], arguments["y"], frame)
            response = self.bridge.request("click", x=x, y=y, button=arguments["button"])
        elif name == "drag":
            self._require_current_frame(arguments, frame)
            start_x, start_y = self._pixels(arguments["start_x"], arguments["start_y"], frame)
            end_x, end_y = self._pixels(arguments["end_x"], arguments["end_y"], frame)
            response = self.bridge.request(
                "drag",
                startX=start_x,
                startY=start_y,
                endX=end_x,
                endY=end_y,
                button=arguments["button"],
                ticks=arguments["ticks"],
            )
        elif name == "scroll":
            response = self.bridge.request("scroll", direction=arguments["direction"], steps=arguments["steps"])
        elif name == "wait":
            value = str(arguments["value"]).lower() if isinstance(arguments["value"], bool) else str(arguments["value"])
            response = self.bridge.request(
                "wait",
                field=arguments["field"],
                value=value,
                ticks=arguments["timeout_ticks"],
            )
        elif name == "idle":
            response = self.bridge.request("idle", ticks=arguments["ticks"])
        elif name == "choose_dialogue_response":
            response = self.bridge.request("choose_dialogue_response", index=arguments["index"])
        elif name == "objective_progress":
            self.ledger.record_progress(arguments["note"], arguments["evidence"])
            return {"status": "recorded"}
        elif name == "record_opportunity":
            self.ledger.record_opportunity(arguments["note"], arguments["reason"])
            return {"status": "recorded"}
        elif name == "wiki_search":
            self.latest_wiki_results = self.wiki.search(arguments["query"])
            return {"status": "completed", "results": self.latest_wiki_results}
        elif name == "stop_session":
            if self.continuous:
                self.last_director_action_count = self.game_actions - self.director_interval
                return {"status": "review_requested", "reason": arguments["reason"]}
            self.stop_reason = arguments["reason"]
            return {"status": "stopped", "reason": self.stop_reason}
        else:
            raise HarnessError(f"Unknown actor tool: {name}")

        self.game_actions += 1
        if name != "wait":
            response = self._settle_transition(response)
        if movement_key is not None and self._same_position(before_state, response.get("state")):
            self.blocked_movements.add(movement_key)
            response = {
                **response,
                "status": "blocked",
                "movement_changed": False,
                "warning": "Movement did not change location or position; choose a different direction.",
            }
        return response

    def _execute_control_sequence(
        self,
        steps: list[dict[str, Any]],
        before_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        assert self.bridge is not None
        current_state = before_state or {}
        origin_location = current_state.get("location")
        origin_menu = current_state.get("menu")
        results: list[dict[str, Any]] = []

        for step in steps:
            movement_key = self._movement_key("hold", step, current_state)
            if movement_key is not None and movement_key in self.blocked_movements:
                results.append({"status": "rejected", "error": "movement_previously_blocked"})
                break

            response = self.bridge.request("hold", buttons=step["buttons"], ticks=step["ticks"])
            self.game_actions += 1
            response = self._settle_transition(response)
            next_state = response.get("state") or current_state
            if movement_key is not None and self._same_position(current_state, next_state):
                self.blocked_movements.add(movement_key)
                response = {
                    **response,
                    "status": "blocked",
                    "movement_changed": False,
                    "warning": "Sequence stopped because movement did not change position.",
                }
            results.append(response)
            current_state = next_state

            if response.get("status") != "completed":
                break
            if current_state.get("location") != origin_location or current_state.get("menu") != origin_menu:
                break
            if not self.continuous and self.game_actions >= self.max_actions:
                break

        final = results[-1] if results else {"status": "rejected", "error": "empty_sequence"}
        return {
            **final,
            "sequence_steps_requested": len(steps),
            "sequence_steps_executed": len(results),
        }

    def _settle_transition(self, response: dict[str, Any]) -> dict[str, Any]:
        assert self.bridge is not None
        state = response.get("state") or {}
        if not state.get("worldReady") or state.get("canMove") is not False or state.get("menu") != "none":
            return response
        settled = self.bridge.request("wait", field="can_move", value="true", ticks=120)
        if settled.get("status") == "completed":
            return settled
        return {**response, "settle": settled}

    def _recover(self, error: Exception) -> None:
        if not isinstance(error, BridgeError):
            return
        if self.bridge is not None:
            self.bridge.close()
        self.bridge = self.supervisor.connect_bridge()
        display_response = self.bridge.request("set_display_mode", mode="fullscreen")
        if display_response.get("status") != "completed":
            raise HarnessError(f"Could not restore full-screen mode after reconnect: {display_response}")

    @staticmethod
    def _movement_key(
        name: str,
        arguments: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> tuple[str | None, int | None, int | None, tuple[str, ...]] | None:
        if name != "hold" or state is None or arguments.get("ticks", 0) < 8:
            return None
        buttons = tuple(
            sorted(button for button in arguments.get("buttons", []) if button in {"W", "A", "S", "D"})
        )
        if not buttons:
            return None
        return state.get("location"), state.get("pixelX"), state.get("pixelY"), buttons

    @staticmethod
    def _same_position(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
        if before is None or after is None:
            return False
        return all(before.get(field) == after.get(field) for field in ("location", "pixelX", "pixelY"))

    @staticmethod
    def _action_fingerprint(decision: ToolDecision, state: dict[str, Any]) -> str:
        inventory = [
            (item.get("slot"), item.get("qualifiedId"), item.get("stack"), item.get("quality"))
            for item in state.get("inventory", [])
        ]
        observable = {
            "location": state.get("location"),
            "pixelX": state.get("pixelX"),
            "pixelY": state.get("pixelY"),
            "menu": state.get("menu"),
            "dialogueText": state.get("dialogueText"),
            "tool": state.get("tool"),
            "stamina": state.get("stamina"),
            "health": state.get("health"),
            "time": state.get("time"),
            "day": state.get("day"),
            "inventory": inventory,
        }
        return json.dumps(
            {"tool": decision.name, "arguments": decision.arguments, "state": observable},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _context(self, role: str, state: dict[str, Any], frame: Frame, state_only: bool = False) -> str:
        blocked_here = sorted(
            "+".join(buttons)
            for location, pixel_x, pixel_y, buttons in self.blocked_movements
            if (location, pixel_x, pixel_y) == (state.get("location"), state.get("pixelX"), state.get("pixelY"))
        )
        harness_state = {
            **state,
            "harnessBlockedDirectionsHere": blocked_here,
            "harnessLastResult": self.last_result,
            "harnessStaminaLow": (state.get("stamina") or 0) < 30 if state.get("worldReady") else False,
        }
        # Slow-changing fields first so a caching provider can reuse the longest possible prefix.
        packet = {
            "role": role,
            "objective_ledger": prompt_ledger(self.ledger.snapshot()),
            "memory": self.telemetry.context(),
            "wiki_results": self.latest_wiki_results,
            "director_feedback": self.director_feedback if role == "director" else None,
            "game_state": compact_state(harness_state) if state_only else harness_state,
            "frame": None if state_only else {
                "frame_id": frame.frame_id,
                "width": frame.width,
                "height": frame.height,
                "visual_variance": frame.visual_variance,
                "visual_brightness": frame.visual_brightness,
            },
            "counters": (
                {
                    "mode": "continuous",
                    "game_actions": self.game_actions,
                    "decisions": self.decisions,
                    "director_interval": self.director_interval,
                }
                if self.continuous
                else {
                    "mode": "bounded",
                    "game_actions": self.game_actions,
                    "max_actions": self.max_actions,
                    "decisions": self.decisions,
                    "max_decisions": self.max_decisions,
                }
            ),
        }
        return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _require_current_frame(arguments: dict[str, Any], frame: Frame) -> None:
        if arguments.get("frame_id") != frame.frame_id:
            raise HarnessError("The model attempted a pointer action against a stale frame.")

    @staticmethod
    def _pixels(normalized_x: float, normalized_y: float, frame: Frame) -> tuple[int, int]:
        x = round(float(normalized_x) * (frame.width - 1))
        y = round(float(normalized_y) * (frame.height - 1))
        return x, y
