from __future__ import annotations

import json
import os
import re
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .bridge import BridgeError, NamedPipeBridge
from .capture import CaptureError, Frame, ScreenCapture
from .farming import clear_debris, go_home_and_sleep, nearest_empty_tiles, plant_seeds, till_tiles, water_crops
from .notebook import Notebook
from .objectives import ObjectiveError, ObjectiveLedger, evaluate_state_condition
from .openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from .prompts import ACTOR_SYSTEM_PROMPT, DIRECTOR_SYSTEM_PROMPT
from .recording import GameplayRecorder, RecordingError
from .supervisor import GameSupervisor
from .state_actor import STATE_ACTOR_PROMPT, STATE_ACTOR_TOOLS, can_use_state_actor, compact_state, prompt_ledger
from .telemetry import Telemetry
from .tools import ACTOR_TOOLS, DIRECTOR_TOOLS, PLAN_DAY_TOOL, REFLECT_TOOL, UPDATE_FARM_PLAN_TOOL
from .wiki import StardewWiki
from .world import WorldMap, travel_to


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
        budget_usd: float | None = None,
    ) -> None:
        if max_actions < 1 or max_decisions < 1 or director_interval < 1:
            raise HarnessError("max_actions, max_decisions, and director_interval must be positive.")
        self.repository_root = repository_root
        self.run_id = str(uuid.uuid4())
        self.run_directory = repository_root / "harness" / "runs" / self.run_id
        self.state_directory = self.run_directory / "state" if isolated_state else repository_root / "harness" / "state"
        self.world = WorldMap(self.state_directory)
        self.notebook = Notebook(self.state_directory)
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
        self.last_progress_fingerprint: tuple[Any, ...] | None = None
        self.stalled_decisions = 0
        self.recent_actor_tools: list[str] = []
        self.stall_review_requested = False
        self.last_result: dict[str, Any] | None = None
        self.director_feedback: str | None = None
        self.budget_usd = budget_usd
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
            display_response = self.bridge.request("set_display_mode", mode="borderless")
            if display_response.get("status") != "completed":
                raise HarnessError(f"Could not enable borderless full-screen mode: {display_response}")
            time.sleep(3)
            self._ensure_world_loaded()
            consecutive_errors = 0
            while not self.stop_reason and (self.continuous or (self.game_actions < self.max_actions and self.decisions < self.max_decisions)):
                try:
                    state, frame = self._observe()
                    self._plan_day_if_needed(state, frame)
                    self._refill_agenda_if_needed(state, frame)
                    if self.stall_review_requested and self.director_future is not None:
                        self._finish_director_review(wait=True, apply=False)
                    else:
                        self._finish_director_review()
                    if self.stop_reason:
                        continue
                    if self.stall_review_requested:
                        self._director_review()
                        self.stall_review_requested = False
                    elif self.ledger.snapshot().get("active") is None:
                        # New goal selection needs a result; routine reviews never block the actor.
                        if self.director_future is not None:
                            self._finish_director_review(wait=True)
                        self._agenda_feedback(state)
                        self._director_review()
                    elif self.director_future is None and self.game_actions - self.last_director_action_count >= self.director_interval:
                        self._start_director_review()
                    if self.stop_reason:
                        continue
                    if self.ledger.snapshot().get("active") is None:
                        continue
                    self._restart_recorder_if_needed()
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
            state = response.get("state") or {}
            if state.get("worldReady") and state.get("gameActive") is False:
                for focus_attempt in range(1, 3):
                    self.bridge.request("focus")
                    self.bridge.request("wait", field="game_active", value="true", ticks=120)
                    response = self.bridge.observe()
                    if response.get("status") != "completed":
                        raise HarnessError(f"Observation failed: {response}")
                    state = response.get("state") or {}
                    self.telemetry.record(
                        "window_refocused",
                        {"attempt": focus_attempt, "game_active_after": state.get("gameActive")},
                    )
                    if state.get("gameActive"):
                        break
                if state.get("gameActive") is False:
                    raise BridgeError("game_window_inactive")
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
            if state.get("worldReady"):
                self.world.load(self.bridge, state.get("worldMapVersion"))
            self.world.observe(state)
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

    def _restart_recorder_if_needed(self) -> None:
        if self.recorder is None or self.recorder.process is None or self.recorder.is_alive():
            return
        segment_count = len(list(self.recorder.directory.glob("gameplay-*.mp4")))
        self.telemetry.record("recording_restarted", {"segment_count": segment_count})
        try:
            self.recorder.start()
        except RecordingError as error:
            self.telemetry.record("recording_failed", {"error": str(error)})
            self.recorder = None

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
        if getattr(self, "last_progress_fingerprint", None) is None:
            self.last_progress_fingerprint = self._progress_fingerprint(state)
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
        result_state = result.get("state") or state
        self._update_stall_watchdog(decision.name, result_state)
        self._print_step("actor", decision, result, result_state, model_ms, bridge_ms)
        if result.get("state"):
            self._complete_verified_objective(result["state"])
        if not self.continuous and self.decisions >= self.max_decisions and not self.stop_reason:
            self.stop_reason = "max_decisions_reached"

    def _plan_day_if_needed(self, state: dict[str, Any], frame: Frame) -> None:
        day = state.get("day")
        if not isinstance(day, int):
            return
        existing = self.notebook.data["days"].get(str(day))
        if self.notebook.current_day == day and existing is not None and existing["theme"]:
            return
        self.notebook.start_day(day)
        self.director_feedback = None
        if self.notebook.farm_plan is None:
            self.telemetry.step += 1
            decision, model_ms = self._decide(
                DIRECTOR_SYSTEM_PROMPT, self._context("director", state, frame), frame,
                [UPDATE_FARM_PLAN_TOOL], "director",
            )
            try:
                self._apply_farm_plan(decision.arguments, state)
            except ValueError as error:
                self.director_feedback = f"Farm plan was rejected: {error}"
                self.telemetry.record("farm_plan_rejected", {"reason": str(error)})
            else:
                self.telemetry.record("farm_plan_set", {"day": day})
            self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)

        self._request_agenda(state, frame, "morning")
        agenda = self.notebook.data["days"][str(day)]["agenda"]
        self.telemetry.record("day_planned", {"day": day, "agenda_size": len(agenda)})

    def _refill_agenda_if_needed(self, state: dict[str, Any], frame: Frame) -> None:
        day = state.get("day")
        if not isinstance(day, int) or self.notebook.current_day != day:
            return
        if self._bedtime_allowed(state) or self.ledger.snapshot().get("active") is not None:
            return
        entry = self.notebook.data["days"].get(str(day))
        if not entry or not entry["agenda"] or self.notebook.remaining(day):
            return
        minutes = max(0, 20 * 60 - self._time_minutes(int(state.get("time") or 0)))
        hours = round(minutes / 60, 1)
        self.director_feedback = (
            f"Agenda finished with {hours:g} in-game hours before bedtime; add 2-4 more goals"
        )
        self._request_agenda(state, frame, "refill")
        self.telemetry.record(
            "agenda_refilled",
            {"day": day, "agenda_size": len(self.notebook.data["days"][str(day)]["agenda"])},
        )

    def _request_agenda(self, state: dict[str, Any], frame: Frame, mode: str) -> None:
        last_decision: ToolDecision | None = None
        last_errors: list[str] = []
        for attempt in range(1, 3):
            self.telemetry.step += 1
            decision, model_ms = self._decide(
                DIRECTOR_SYSTEM_PROMPT, self._context("director", state, frame), frame,
                [PLAN_DAY_TOOL], "director",
            )
            last_decision = decision
            ignored_carried_ids = self._strip_invalid_carried_ids(decision.arguments, state)
            if ignored_carried_ids:
                self.telemetry.record("ignored_carried_ids", {"ids": ignored_carried_ids})
            last_errors = self._agenda_errors(decision.arguments, state, mode)
            if not last_errors:
                self._apply_agenda(decision.arguments, state)
                self.director_feedback = None
                self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)
                return
            self.director_feedback = "Invalid plan_day: " + "; ".join(last_errors)
            self.telemetry.record(
                "agenda_plan_rejected",
                {"attempt": attempt, "mode": mode, "errors": last_errors},
            )
            self._print_step(
                "director", decision, {"status": "rejected", "reason": self.director_feedback},
                state, model_ms, 0,
            )

        assert last_decision is not None
        self._apply_agenda(last_decision.arguments, state)
        self.telemetry.record(
            "agenda_accepted_with_gaps",
            {"mode": mode, "errors": last_errors},
        )

    def _agenda_errors(
        self, arguments: dict[str, Any], state: dict[str, Any], mode: str
    ) -> list[str]:
        agenda = arguments.get("agenda", [])
        minimum, maximum = (5, 8) if mode == "morning" else (2, 4)
        errors = []
        if not minimum <= len(agenda) <= maximum:
            errors.append(f"{mode} planning needs {minimum}-{maximum} new items")
        if mode == "morning" and not any(item.get("slot") == "evening" for item in agenda):
            errors.append("at least one new item must use the evening slot")
        if int(state.get("time") or 0) < 1800:
            for item in agenda:
                clauses = [clause.strip().casefold()
                           for clause in (item.get("success_condition") or "").split(",")]
                home = any(re.fullmatch(r"location\s*(?:is|==|=)\s*farmhouse", clause)
                           for clause in clauses)
                allowed = all(
                    re.fullmatch(r"location\s*(?:is|==|=)\s*farmhouse", clause)
                    or re.fullmatch(r"playerfree\s*(?:is|==|=)\s*true", clause)
                    for clause in clauses
                )
                if home and allowed and item.get("slot") != "evening":
                    errors.append("return home is not an agenda item before the evening slot")
                    break

        day = int(state["day"])
        carried = {
            item["id"] for item in self.notebook.remaining(day) if item["status"] == "carried"
        }
        kept = [item.get("carried_id") for item in agenda if item.get("carried_id")]
        dropped = [item.get("id") for item in arguments.get("dropped", [])]
        missing = carried - set(kept) - set(dropped)
        if missing:
            errors.append("carried items need carried_id or a drop reason: " + ", ".join(sorted(missing)))

        if mode == "morning":
            unvisited = self.world.summary(state.get("location"), state.get("time")).get("unvisited", [])
            exploration_found = False
            for item in agenda:
                goal = item.get("goal", "").casefold()
                condition = (item.get("success_condition") or "").casefold()
                for location in unvisited:
                    if location.casefold() in goal and f"location is {location}".casefold() in condition:
                        exploration_found = True
                        break
                if exploration_found:
                    break
            if unvisited and not exploration_found:
                errors.append(
                    "include an item naming an unvisited location whose success_condition contains "
                    "`location is <that name>`"
                )
        return errors

    def _strip_invalid_carried_ids(
        self, arguments: dict[str, Any], state: dict[str, Any]
    ) -> list[str]:
        day = int(state["day"])
        candidates = {
            item["id"] for item in self.notebook.remaining(day) if item["status"] == "carried"
        }
        used: set[str] = set()
        ignored: list[str] = []
        for item in arguments.get("agenda", []):
            carried_id = item.get("carried_id")
            if not carried_id:
                continue
            if carried_id not in candidates or carried_id in used:
                ignored.append(carried_id)
                item.pop("carried_id", None)
                continue
            used.add(carried_id)
        return list(dict.fromkeys(ignored))

    def _apply_agenda(self, arguments: dict[str, Any], state: dict[str, Any]) -> None:
        self.notebook.set_agenda(
            int(state["day"]), arguments["agenda"], arguments["theme"], arguments.get("dropped", [])
        )

    def _apply_farm_plan(self, arguments: dict[str, Any], state: dict[str, Any]) -> None:
        layout = state.get("farmLayout")
        if not isinstance(layout, dict):
            raise ValueError("farmLayout is unavailable")
        width = layout.get("width")
        height = layout.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("farmLayout bounds are unavailable")
        zones = arguments.get("zones", [])
        if not any(zone.get("purpose") == "crops" for zone in zones):
            raise ValueError("at least one crops zone is required")
        for zone in zones:
            if not (
                0 <= zone["x1"] <= zone["x2"] < width
                and 0 <= zone["y1"] <= zone["y2"] < height
            ):
                raise ValueError(f"zone {zone.get('name', '<unnamed>')} is outside Farm bounds")
        self.notebook.set_farm_plan(zones, arguments["notes"], int(state["day"]))

    def _agenda_feedback(self, state: dict[str, Any]) -> None:
        day = state.get("day")
        if not isinstance(day, int) or self.notebook.current_day != day:
            return
        pending = [
            {key: item.get(key) for key in ("id", "goal", "slot", "status")}
            for item in self.notebook.remaining(day)
        ]
        if pending:
            self.director_feedback = "No objective is active. Select from these agenda items: " + json.dumps(
                pending, ensure_ascii=False, separators=(",", ":")
            )

    @staticmethod
    def _time_minutes(value: int) -> int:
        return (value // 100) * 60 + value % 100

    def _complete_verified_objective(self, state: dict[str, Any]) -> bool:
        active = self.ledger.snapshot().get("active")
        evaluation = evaluate_state_condition(active["success_condition"], state) if active else None
        if evaluation is not None and evaluation[0]:
            evidence = (
                f"Harness verified `{active['success_condition']}` against structured state: "
                + ", ".join(f"{key}={state.get(key)!r}" for key in ("location", "time", "day", "menu", "playerFree"))
            )
            agenda_id = active.get("agenda_id")
            self.ledger.complete_objective(evidence)
            if agenda_id is not None:
                self.notebook.mark(agenda_id, "done", evidence)
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
        budget_usd = getattr(self, "budget_usd", None)
        if budget_usd is None:
            return
        cumulative_cost = float(self.telemetry.summary["usage"]["cost"])
        if cumulative_cost >= budget_usd and self.stop_reason != "budget_reached":
            self.stop_reason = "budget_reached"
            self.telemetry.record(
                "budget_reached",
                {"cumulative_cost": cumulative_cost, "budget_usd": budget_usd},
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
                agenda_id = (self.ledger.snapshot().get("active") or {}).get("agenda_id")
                self.ledger.block_objective(arguments["evidence"])
                if agenda_id is not None and hasattr(self, "notebook"):
                    self.notebook.mark(agenda_id, "carried", arguments["evidence"])
        elif decision.name == "set_objective":
            try:
                evaluation = evaluate_state_condition(arguments["success_condition"], state)
                if evaluation is not None and evaluation[0]:
                    raise ObjectiveError("The proposed success condition is already satisfied.")
                if evaluation is not None and any(" is unavailable" in item for item in evaluation[1]):
                    raise ObjectiveError("The proposed success condition uses an unavailable state field.")
                agenda_id = arguments.get("agenda_id")
                day = state.get("day")
                remaining = (
                    self.notebook.remaining(day)
                    if hasattr(self, "notebook") and isinstance(day, int) else []
                )
                if remaining and agenda_id is None:
                    raise ObjectiveError("An agenda_id is required while agenda items remain.")
                if agenda_id is not None and not any(item["id"] == agenda_id for item in remaining):
                    raise ObjectiveError("The agenda_id is not a remaining item for today.")
                self.ledger.set_objective(
                    arguments["goal"],
                    arguments["success_condition"],
                    arguments["milestone"],
                    agenda_id,
                )
                if agenda_id is not None and hasattr(self, "notebook"):
                    self.notebook.mark(agenda_id, "active")
            except ObjectiveError as error:
                self.director_feedback = (
                    f"Previous set_objective was rejected: {error} "
                    f"Rejected condition: {arguments['success_condition']!r}. Propose a different, currently false condition."
                )
                self.telemetry.record(
                    "objective_update_rejected",
                    {"reason": str(error), "arguments": arguments},
                )
        elif decision.name == "plan_day":
            day = state.get("day")
            entry = self.notebook.data["days"].get(str(day)) if isinstance(day, int) else None
            mode = "refill" if entry and entry["theme"] and not self.notebook.remaining(day) else "morning"
            ignored_carried_ids = self._strip_invalid_carried_ids(arguments, state)
            if ignored_carried_ids:
                self.telemetry.record("ignored_carried_ids", {"ids": ignored_carried_ids})
            errors = self._agenda_errors(arguments, state, mode)
            if errors:
                self.director_feedback = "Invalid plan_day: " + "; ".join(errors)
            else:
                self._apply_agenda(arguments, state)
        elif decision.name == "update_farm_plan":
            try:
                self._apply_farm_plan(arguments, state)
            except ValueError as error:
                self.director_feedback = f"Farm plan was rejected: {error}"
        elif decision.name == "reflect":
            self.notebook.reflect(int(state["day"]), arguments["summary"], arguments["learned"])
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
            zone_rejection = self._crop_zone_rejection(tiles, before_state or {})
            if zone_rejection is not None:
                return zone_rejection
            response = plant_seeds(self.bridge, before_state or {}, arguments["seed_slot"], tiles,
                                   len(tiles) * 5 if self.continuous else self.max_actions - self.game_actions)
            self.game_actions += response["controls_executed"]
            return response
        if name in {"water_crops", "till_tiles"}:
            skill = water_crops if name == "water_crops" else till_tiles
            tiles = arguments["tiles"]
            if name == "till_tiles":
                zone_rejection = self._crop_zone_rejection(tiles, before_state or {})
                if zone_rejection is not None:
                    return zone_rejection
            response = skill(self.bridge, before_state or {}, tiles,
                             len(tiles) * 8 if self.continuous else self.max_actions - self.game_actions)
            self.game_actions += response["controls_executed"]
            return response
        if name == "clear_debris":
            targets = arguments["targets"]
            response = clear_debris(
                self.bridge, before_state or {}, targets,
                len(targets) * 16 if self.continuous else self.max_actions - self.game_actions,
            )
            self.game_actions += response["controls_executed"]
            return response
        if name == "go_home_and_sleep":
            if not self._bedtime_allowed(before_state or {}):
                return {
                    "status": "rejected",
                    "reason": "bedtime_not_allowed_before_2000_unless_exhausted",
                    "time": (before_state or {}).get("time"),
                    "stamina": (before_state or {}).get("stamina"),
                }
            if hasattr(self, "notebook"):
                self._reflect_before_sleep(before_state or {}, frame)
            response = go_home_and_sleep(self.bridge, before_state or {},
                                         45 if self.continuous else self.max_actions - self.game_actions)
            self.game_actions += response["controls_executed"]
            return response
        if name == "travel_to":
            action_budget = (max(3, len(self.world.nodes) * 3) if self.continuous
                             else self.max_actions - self.game_actions)
            response = travel_to(
                self.bridge, before_state or {}, self.world, arguments["destination"], action_budget
            )
            self.game_actions += response["controls_executed"]
            return response
        if name == "world_map":
            state = before_state or {}
            return {
                "status": "completed",
                "route": self.world.route(
                    state.get("location") or "", arguments["destination"], state.get("time")
                ),
                "summary": self.world.summary(state.get("location"), state.get("time")),
            }
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
            state = before_state or {}
            hours = self.world.edge_hours(state.get("location") or "", arguments["location"])
            current_time = state.get("time")
            if hours is not None and isinstance(current_time, int):
                open_time, close_time = hours
                if not self.world._is_open(current_time, open_time, close_time):
                    return {"status": "rejected", "reason": f"door_closed_until_{open_time}",
                            "openTime": open_time, "closeTime": close_time}
            response = self.bridge.request("go_to_location", location=arguments["location"], ticks=600)
            reason = response.get("error") or response.get("reason")
            if response.get("status") != "completed" and reason == "no_walkable_path":
                response_state = response.get("state") or {}
                self.world.record_blocked_path(
                    (before_state or {}).get("location") or "",
                    arguments["location"],
                    int(response_state.get("day") or (before_state or {}).get("day") or 1),
                )
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
        if (name == "go_to_location" and (response.get("state") or {}).get("location") == arguments["location"]
                and (before_state or {}).get("location") != arguments["location"]):
            self.world.clear_blocked_path((before_state or {}).get("location") or "", arguments["location"])
        if movement_key is not None and self._same_position(before_state, response.get("state")):
            self.blocked_movements.add(movement_key)
            response = {
                **response,
                "status": "blocked",
                "movement_changed": False,
                "warning": "Movement did not change location or position; choose a different direction.",
            }
        return response

    def _crop_zone_rejection(
        self, tiles: list[dict[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any] | None:
        if state.get("location") != "Farm" or not hasattr(self, "notebook"):
            return None
        plan = self.notebook.farm_plan
        if plan is None:
            return None
        zones = [zone for zone in plan["zones"] if zone["purpose"] == "crops"]
        if all(self.notebook.in_zone(tile["x"], tile["y"], "crops") for tile in tiles):
            return None
        return {"status": "rejected", "reason": "outside_crop_zone", "zones": zones}

    def _reflect_before_sleep(self, state: dict[str, Any], frame: Frame) -> None:
        day = state.get("day")
        if not isinstance(day, int):
            self.telemetry.record("reflection_skipped", {"reason": "day_unavailable"})
            return
        self.telemetry.step += 1
        try:
            decision, model_ms = self._decide(
                DIRECTOR_SYSTEM_PROMPT, self._context("director", state, frame), frame,
                [REFLECT_TOOL], "director",
            )
            if decision.name != "reflect":
                raise HarnessError(f"Expected reflect, received {decision.name}")
            self.notebook.reflect(day, decision.arguments["summary"], decision.arguments["learned"])
            self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)
        except (HarnessError, OpenRouterError, KeyError, TypeError, ValueError) as error:
            self.telemetry.record("reflection_skipped", {"reason": str(error)})
        for item in self.notebook.remaining(day):
            self.notebook.mark(item["id"], "carried", "Unfinished at bedtime.")

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
        display_response = self.bridge.request("set_display_mode", mode="borderless")
        if display_response.get("status") != "completed":
            raise HarnessError(f"Could not restore borderless full-screen mode after reconnect: {display_response}")

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

    @staticmethod
    def _bedtime_allowed(state: dict[str, Any]) -> bool:
        return not (
            (state.get("time") or 0) < 2000
            and (state.get("stamina") or 0) >= 30
            and (state.get("health") or 0) >= 50
        )

    @staticmethod
    def _progress_fingerprint(state: dict[str, Any]) -> tuple[Any, ...]:
        inventory_counts = tuple(sorted((state.get("inventoryCounts") or {}).items()))
        return tuple(state.get(field) for field in (
            "location", "tileX", "tileY", "day", "time", "money", "stamina",
            "plantedCrops", "wateredCrops", "tilledTiles", "harvestableCrops",
        )) + (inventory_counts, state.get("menu"))

    def _update_stall_watchdog(self, tool_name: str, state: dict[str, Any]) -> None:
        fingerprint = self._progress_fingerprint(state)
        self.recent_actor_tools.append(tool_name)
        del self.recent_actor_tools[:-3]
        if fingerprint == self.last_progress_fingerprint:
            self.stalled_decisions += 1
        else:
            self.stalled_decisions = 0
        self.last_progress_fingerprint = fingerprint

        if self.stalled_decisions == 8:
            tools = ", ".join(self.recent_actor_tools)
            self.telemetry.record(
                "stall_detected",
                {"count": self.stalled_decisions, "tools": list(self.recent_actor_tools)},
            )
            self.director_feedback = (
                f"The last 8 decisions changed nothing in the world: {tools}. "
                "Choose a different tactic or objective."
            )
            self.stall_review_requested = True
        elif self.stalled_decisions == 16:
            assert self.bridge is not None
            focus = self.bridge.request("focus")
            display = self.bridge.request("set_display_mode", mode="borderless")
            self.telemetry.record(
                "stall_recovery_attempted",
                {"count": self.stalled_decisions, "focus": focus.get("status"),
                 "display_mode": display.get("status")},
            )
        elif self.stalled_decisions == 24:
            self.stop_reason = "stalled"

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
            "harnessBedtimeAllowed": self._bedtime_allowed(state),
            "harnessStalledDecisions": getattr(self, "stalled_decisions", 0),
        }
        # Slow-changing fields first so a caching provider can reuse the longest possible prefix.
        packet = {
            "role": role,
            "objective_ledger": prompt_ledger(self.ledger.snapshot()),
            "memory": self.telemetry.context(),
            "wiki_results": self.latest_wiki_results,
            "director_feedback": self.director_feedback if role == "director" else None,
            "world": self.world.summary(state.get("location"), state.get("time")),
            "notebook": (
                self.notebook.context(state.get("day")) if hasattr(self, "notebook") else None
            ),
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
