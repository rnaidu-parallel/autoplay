from __future__ import annotations

import json
import ctypes
import os
import re
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .bridge import BridgeError, NamedPipeBridge
from .calendar import calendar_day
from .checkpoint import Checkpoints
from .control import OperatorControl
from .history import History
from .capture import CaptureError, Frame, ScreenCapture
from .farming import check_mail, clear_debris, go_home_and_sleep, look_at, nearest_empty_tiles, plant_seeds, till_tiles, water_crops
from .attention import AttentionYield, AttentiveBridge
from . import life as life_policy
from .encounters import approach_and_talk
from .progress import ActivityProgress
from .notebook import Notebook
from .objectives import ObjectiveError, ObjectiveLedger, evaluate_state_condition, validate_new_condition
from .openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from .prompts import ACTOR_SYSTEM_PROMPT, DIRECTOR_SYSTEM_PROMPT
from .recording import GameplayRecorder, RecordingError
from .supervisor import GameSupervisor
from .state_actor import STATE_ACTOR_PROMPT, STATE_ACTOR_TOOLS, can_use_state_actor, compact_state, prompt_ledger
from .telemetry import Telemetry
from .tools import ACTOR_TOOLS, DIRECTOR_TOOLS, PLAN_DAY_TOOL, REFLECT_TOOL, UPDATE_FARM_PLAN_TOOL
from .wiki import StardewWiki
from .world import WorldMap, travel_to


# Context JSON estimates; fixed instructions, tool schemas and images are measured separately.
ACTOR_CONTEXT_BUDGET = 6000
DIRECTOR_CONTEXT_BUDGET = 10000


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
        director_reasoning_effort: str | None = None,
        isolated_state: bool = False,
        actor_mode: str = "state-first",
        budget_usd: float | None = None,
        max_minutes: int | None = None,
        forever: bool = False,
        resume_checkpoint: bool = False,
        attach: bool = False,
    ) -> None:
        if max_actions < 1 or max_decisions < 1 or director_interval < 1:
            raise HarnessError("max_actions, max_decisions, and director_interval must be positive.")
        if max_minutes is not None and max_minutes < 1:
            raise HarnessError("max_minutes must be positive.")
        self.repository_root = repository_root
        self.run_id = str(uuid.uuid4())
        self.run_directory = repository_root / "harness" / "runs" / self.run_id
        self.state_directory = self.run_directory / "state" if isolated_state else repository_root / "harness" / "state"
        self.checkpoints = Checkpoints(self.state_directory, Path(os.environ["APPDATA"]) / "StardewValley" / "Saves")
        self.resumed_checkpoint = self.checkpoints.restore() if resume_checkpoint else None
        self.control = OperatorControl(self.run_directory)
        self.operator_mode = "playing"
        self.operator_guidance = ""
        self.operator_revision = 0
        self.finish_baseline = None
        self.history = History(repository_root / "harness" / "runs", self.state_directory)
        self.latest_history_results = []
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
        self.objective_decision_id: str | None = None
        self.objective_decisions = 0
        self.stop_reason: str | None = None
        self.keep_game_open = keep_game_open or not launch_game
        self.attach = attach
        self.continuous = continuous
        self.title_screen_ready = False
        self.latest_wiki_results: list[dict[str, Any]] = []
        self.blocked_movements: set[tuple[str | None, int | None, int | None, tuple[str, ...]]] = set()
        self.last_action_fingerprint: str | None = None
        self.last_progress_fingerprint: tuple[Any, ...] | None = None
        self.stalled_decisions = 0
        self.recent_actor_tools: list[str] = []
        self.stall_review_requested = False
        audience = self.notebook.audience_demand()
        self.audience_review_requested = bool(audience and audience["status"] == "pending")
        self.last_result: dict[str, Any] | None = None
        self.director_feedback: str | None = None
        self.budget_usd = budget_usd
        self.max_minutes = max_minutes
        self.forever = forever
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
        self.director_reasoning_effort = director_reasoning_effort or reasoning_effort
        OpenRouterClient.validate_effort(model, self.director_reasoning_effort)

    @staticmethod
    def _keep_display_awake(on: bool) -> None:
        """Desktop duplication dies when Windows switches the display off; an unattended run must hold it on."""
        if os.name != "nt":
            return
        continuous, system_required, display_required = 0x80000000, 0x00000001, 0x00000002
        flags = continuous | system_required | display_required if on else continuous
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def run(self) -> str:
        started_at = time.monotonic()
        self._keep_display_awake(True)
        self.control.update(mode="starting", closed=False, message="Connecting to the farmer.")
        self.telemetry.record(
            "session_started",
            {
                "run_id": self.run_id,
                "model": self.client.model,
                "reasoning_effort": self.client.reasoning_effort,
                "director_reasoning_effort": self.director_reasoning_effort,
                "actor_mode": self.actor_mode,
                "provider_policy": self.client.PROVIDER_PREFERENCES[self.client.model],
                "initial_objective": self.ledger.snapshot().get("active"),
                "isolated_state": self.state_directory == self.run_directory / "state",
            },
        )
        try:
            self.bridge = self.supervisor.connect_bridge()
            state, _ = self._observe()
            if self.attach and not state.get("worldReady"):
                raise HarnessError("Attach requires an already loaded game. Load the save before attaching.")
            if not self.attach and self.supervisor.launch_if_needed:
                display_response = self.bridge.request("set_display_mode", mode="borderless")
                if display_response.get("status") != "completed":
                    raise HarnessError(f"Could not enable borderless full-screen mode: {display_response}")
                time.sleep(3)
            if (self.attach or self.resumed_checkpoint) and (state.get("menu") or "").startswith("GameMenu"):
                response = self._close_menu()
                if (response.get("state", {}).get("menu") or "").startswith("GameMenu"):
                    raise HarnessError(f"Could not close the handoff menu while attaching: {response.get('reason') or response.get('error')}")
                self.telemetry.record("handoff_resumed", {"state": response.get("state")}, include_recent=False)
            self._ensure_world_loaded()
            if self.resumed_checkpoint:
                state, _ = self._observe()
                expected = self.resumed_checkpoint
                if (state.get("saveId") != expected["save_id"]
                        or any(state.get(key) != value for key, value in expected["date"].items())):
                    self.keep_game_open = True
                    raise HarnessError("Loaded game does not match the checkpoint. Load the matching save before resuming.")
                self.telemetry.record("checkpoint_resumed", {"checkpoint": expected["id"]})
            self.control.update(mode="playing", message="Farmer is playing.")
            consecutive_errors = 0
            while not self.stop_reason and (self.continuous or self.operator_mode == "finishing" or (self.game_actions < self.max_actions and self.decisions < self.max_decisions)):
                if self.max_minutes is not None and time.monotonic() - started_at >= self.max_minutes * 60:
                    self.stop_reason = "time_limit_reached"
                    break
                try:
                    state, frame = self._observe()
                    self._poll_operator(state)
                    self._check_activity_progress(state)
                    if self.stop_reason:
                        continue
                    if self.operator_mode == "finishing":
                        self._finish_save_step(state, frame)
                        continue
                    self._plan_day_if_needed(state, frame)
                    if self.audience_review_requested:
                        if self.notebook.audience_demand() is None:
                            self.audience_review_requested = False
                        else:
                            if self.director_future is not None:
                                self._finish_director_review(wait=True, apply=False)
                            if self._request_agenda(state, frame, "audience"):
                                self.audience_review_requested = False
                    if self.stall_review_requested and self.director_future is not None:
                        self._finish_director_review(wait=True, apply=False)
                    else:
                        self._finish_director_review()
                    if self.stop_reason:
                        continue
                    if self.stall_review_requested:
                        self._director_review()
                        self.stall_review_requested = False
                    elif self.ledger.snapshot().get("active") and self.director_future is None and self.game_actions - self.last_director_action_count >= self.director_interval:
                        self._start_director_review()
                    if self.stop_reason:
                        continue
                    self._restart_recorder_if_needed()
                    self._actor_step()
                    consecutive_errors = 0
                    if (self.max_minutes is not None
                            and time.monotonic() - started_at >= self.max_minutes * 60
                            and self.stop_reason in {None, "max_actions_reached", "max_decisions_reached"}):
                        self.stop_reason = "time_limit_reached"
                except (BridgeError, CaptureError, OpenRouterError, ObjectiveError, HarnessError,
                        KeyError, TypeError, ValueError) as error:
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
                    delay = min(2 ** (consecutive_errors - 1), 30)
                    if self.max_minutes is not None:
                        remaining = (self.max_minutes * 60) - (time.monotonic() - started_at)
                        if remaining <= 0:
                            self.stop_reason = "time_limit_reached"
                            break
                        delay = min(delay, remaining)
                    time.sleep(delay)
                except Exception as error:
                    self.telemetry.record(
                        "fatal_error",
                        {
                            "error_type": type(error).__name__,
                            "message": str(error),
                            "traceback": traceback.format_exc(),
                        },
                        include_recent=False,
                    )
                    raise
            if (not self.stop_reason and self.max_minutes is not None
                    and time.monotonic() - started_at >= self.max_minutes * 60):
                self.stop_reason = "time_limit_reached"
            if not self.stop_reason and not self.continuous:
                self.stop_reason = "max_decisions_reached" if self.decisions >= self.max_decisions else "max_actions_reached"
            self.telemetry.record("session_stopped", {"reason": self.stop_reason})
            self.telemetry.save_summary()
            return self.stop_reason
        finally:
            if self.stop_reason in {"stalled", "home_route_blocked"}:
                self.keep_game_open = True
                self.operator_mode = "needs_attention"
            if self.operator_mode == "finishing" and self.stop_reason != "finish_saved":
                self.keep_game_open = True
                self.operator_mode = "needs_attention"
            if self.stop_reason == "finish_saved" and self.supervisor.process is None:
                self.keep_game_open = True
            self.control.update(mode=self.operator_mode if self.operator_mode != "playing" else "stopped",
                                closed=True, reason=self.stop_reason,
                                message=("Game left open for assistance; see the error or stop reason." if self.operator_mode == "needs_attention"
                                         else "Saved and stopped." if self.stop_reason == "finish_saved" else "Run stopped."))
            if self.recorder is not None:
                try:
                    self.recorder.stop()
                except Exception:
                    pass
            if self.bridge is not None:
                if self.keep_game_open:
                    try:
                        state = self.bridge.observe().get("state") or {}
                        if state.get("worldReady") and state.get("playerFree") and state.get("menu") == "none" and not state.get("eventUp"):
                            response = self.bridge.request("press", buttons=["Escape"])
                            paused = ((response.get("state") or {}).get("menu") or "").startswith("GameMenu")
                            self.telemetry.record("paused_for_handoff" if paused else "handoff_pause_failed",
                                                  {"state": response.get("state")}, include_recent=False)
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
                try:
                    self.bridge.close()
                except Exception:
                    pass
            try:
                self.capture.release()
            except Exception:
                pass
            try:
                if self.keep_game_open:
                    if self.operator_mode != "needs_attention":
                        self.supervisor.wait_for_started_game()
                else:
                    self.supervisor.stop_started_game()
            except Exception:
                pass
            self._keep_display_awake(False)
            try:
                self.client.cancelled.set()
            except Exception:
                pass
            try:
                self.director_executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
            try:
                self._finish_director_review(apply=False)
            except Exception:
                pass
            try:
                self.telemetry.save_summary()
            except Exception:
                pass

    def _poll_operator(self, state: dict[str, Any]) -> bool:
        if not hasattr(self, "control"):
            return False
        commands = self.control.consume()
        if commands:
            state = self.bridge.observe().get("state") or state
        if (self.state_directory / "STOP").exists() and self.operator_mode == "playing":
            commands.append({"id": "STOP", "kind": "finish_save", "message": "STOP file received"})
        terminal_requested = any(command["kind"] in {"finish_save", "hold"} for command in commands)
        for command in commands:
            self.operator_revision += 1
            kind = command["kind"]
            if kind == "finish_save" and self.operator_mode == "playing":
                self.operator_mode = "finishing"
                self.finish_baseline = dict(state)
                active = self.ledger.data.get("active")
                self.finish_previous_objective = (
                    active.get("resume_objective") if active and active.get("operator_finish") else active
                )
                previous = self.finish_previous_objective or {}
                if previous.get("agenda_id"):
                    self.notebook.mark(previous["agenda_id"], "pending", "Operator requested a save and session end.")
                self.ledger.set_objective(
                    "Return home, sleep to save, then end this session.",
                    f"saveCount > {state.get('saveCount') or 0}, location is FarmHouse, playerFree is true, nightActive is false",
                    "Leave chores and detours. Return to FarmHouse and use the bed; early sleep is allowed.",
                    interruption_reason="Operator requested Finish & Save.",
                    operator_finish=True,
                )
                self.stall_review_requested = False
                self.last_action_fingerprint = None
                self._finish_director_review(apply=False)
            elif kind == "steer":
                self.operator_guidance = command["message"]
                self.operator_guidance_scope = self._scene_scope(state)
                self.last_action_fingerprint = None
                self.inspect_next_scene = True
            elif kind == "hold":
                self.operator_mode = "held"
                self.keep_game_open = True
                self.stop_reason = "operator_hold"
            elif kind == "audience":
                day = calendar_day(state)
                try:
                    if self.operator_mode != "playing" or terminal_requested:
                        raise ValueError("audience demands require an active playing run")
                    self.notebook.set_audience_demand(
                        command["id"], command["message"], command.get("support", 0), day
                    )
                except ValueError as error:
                    self.telemetry.record("audience_demand_rejected", {"id": command["id"], "reason": str(error)})
                    command = {**command, "status": "rejected", "reason": str(error)}
                else:
                    self.audience_review_requested = True
                    self.telemetry.record("audience_demand", {"id": command["id"], "goal": command["message"],
                                                              "support": command.get("support", 0)})
            self.telemetry.record("operator_command", {"command": command, "mode": self.operator_mode})
            received = command if command.get("status") == "rejected" else {**command, "status": "received"}
            self.control.update(mode=self.operator_mode, last_command=received,
                                message=(command.get("reason") or
                                         ("Returning home to save." if self.operator_mode == "finishing"
                                          else "Command received.")))
        # The audience waits for the next review. Only the operator is worth a discarded decision.
        return any(command["kind"] != "audience" for command in commands)

    def _finish_save_step(self, state: dict[str, Any], frame: Frame) -> None:
        if self._checkpoint_if_saved(state):
            return
        self._finish_director_review(apply=False)
        if self.stop_reason:
            return
        # The existing bedtime controller is a verified ordinary-input chore. Try it
        # once per position; leave failed navigation/menu recovery to the actor.
        position = (state.get("location"), state.get("tileX"), state.get("tileY"), calendar_day(state))
        if (state.get("playerFree") and state.get("location") in {"Farm", "FarmHouse"}
                and position != getattr(self, "finish_sleep_position", None)):
            self.finish_sleep_position = position
            self.telemetry.step += 1
            result = self._execute_actor_tool(ToolDecision("go_home_and_sleep", {}, {}, None), frame, state)
            self.last_result = {"tool": "go_home_and_sleep", "status": result.get("status"), "reason": result.get("reason")}
            self.telemetry.record("tool_result", {"tool": "go_home_and_sleep", "source": "operator_finish", "result": result})
            after = result.get("state") or state
            if not self._checkpoint_if_saved(after):
                self._track_action_retries(ToolDecision("go_home_and_sleep", {}, {}, None), result, state, after)
            return
        if self.budget_usd is not None and float(self.telemetry.summary["usage"]["cost"]) >= self.budget_usd:
            self.stop_reason = "budget_reached"
            return
        self._actor_step()

    def _checkpoint_if_saved(self, state: dict[str, Any]) -> bool:
        baseline = getattr(self, "finish_baseline", None)
        if not baseline or self.operator_mode != "finishing":
            return False
        day, before_day = calendar_day(state), calendar_day(baseline)
        if not (day is not None and before_day is not None and day > before_day
                and (state.get("saveCount") or 0) > (baseline.get("saveCount") or 0)
                and state.get("saveId") == baseline.get("saveId")
                and state.get("worldReady") and state.get("playerFree") and not state.get("nightActive")
                and state.get("location") == "FarmHouse" and state.get("menu") == "none"):
            return False
        self.ledger.complete_objective("Nightly save completed and next morning observed.")
        previous = self.finish_previous_objective
        if previous:
            if previous.get("kind") == "curiosity":
                self.ledger.pursue_interest(previous["goal"], previous["milestone"])
            else:
                self.ledger.set_objective(previous["goal"], previous["success_condition"], previous["milestone"],
                                          agenda_id=previous.get("agenda_id"))
            if previous.get("quest_id"):
                self.ledger.data["active"]["quest_id"] = previous["quest_id"]
                self.ledger._save()
        self.world.observe(state)
        try:
            checkpoint = self.checkpoints.create(self.run_id, state)
        except (OSError, ValueError, KeyError) as error:
            self.stop_reason = "finish_checkpoint_failed"
            self.control.update(error=str(error))
            self.telemetry.record("checkpoint_failed", {"error": str(error)})
            return True
        self.operator_mode = "saved"
        self.stop_reason = "finish_saved"
        self.control.update(mode="saved", checkpoint={key: checkpoint[key] for key in ("id", "save_id", "date")})
        self.telemetry.record("checkpoint_saved", {"checkpoint": checkpoint})
        return True

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
                handle = self.capture._find_game_window()
                if handle:
                    self.capture._raised_handle = handle
                    self.capture._activate_window(handle)
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
                if getattr(self, "blind", False):
                    self.blind = False
                    self.telemetry.record("capture_restored", {}, include_recent=False)
            except CaptureError as error:
                if attempt < 6:
                    time.sleep(0.25)
                    continue
                # A live run keeps playing from structured state alone rather than dying for want of a picture.
                viewport = response.get("state") or {}
                frame = Frame(str(uuid.uuid4()), viewport.get("viewportWidth") or 1280, viewport.get("viewportHeight") or 720,
                              "", None)
                capture_ms = 0.0
                if not getattr(self, "blind", False):
                    self.blind = True
                    self.telemetry.record("capture_unavailable", {"error": str(error)[:300]}, include_recent=False)
            state = response.get("state") or {}
            if state.get("worldReady"):
                self.world.load(self.bridge, state.get("worldMapVersion"))
            self.world.observe(state)
            if hasattr(self, "notebook"):
                self._expire_scene_work(state)
                self.notebook.observe_life(state)
                completed = {key: "complete" for key, quest in self.notebook.data["life"]["quests"].items() if quest.get("complete")}
                state["questStates"] = {**completed, **state.get("questStates", {})}
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
        self._check_activity_progress(state)
        if getattr(self, "stop_reason", None):
            return
        if self._complete_verified_objective(state):
            return
        # Reflexes that need no decision: watch a scene through, greet someone in reach, get home at night.
        if self._play_dialogue(state) or self._greet_in_passing(state):
            state, frame = self._observe()
            if self._complete_verified_objective(state):
                return
        if self._bedtime_reflex(state):
            return
        state_only = (getattr(self, "actor_mode", "visual") == "state-first"
                      and not self.inspect_next_scene and can_use_state_actor(state)) or not getattr(frame, "data_url", True)
        self.inspect_next_scene = False
        if getattr(self, "last_progress_fingerprint", None) is None:
            self.last_progress_fingerprint = self._stall_fingerprint(state)
        context = self._context("actor", state, frame, state_only=state_only)
        objective_id = (self.ledger.snapshot().get("active") or {}).get("id")
        if objective_id != getattr(self, "objective_decision_id", None):
            self.objective_decision_id = objective_id
            self.objective_decisions = 0
        self.objective_decisions = getattr(self, "objective_decisions", 0) + 1
        self.decisions += 1
        tools = STATE_ACTOR_TOOLS if state_only else ACTOR_TOOLS
        if self._bedtime_due(state):
            # Late enough that the only sensible decision is how to say goodnight.
            tools = [tool for tool in tools if tool["function"]["name"] in {"go_home_and_sleep", "close_menu", "stop_session"}]
        if state.get("eventUp"):
            unavailable = {"navigate_to", "go_to_location", "travel_to", "check_mail",
                           "open_menu_tab", "plant_seeds", "plant_nearest_seeds",
                           "till_tiles", "water_crops", "clear_debris", "go_home_and_sleep"}
            tools = [tool for tool in tools if tool["function"]["name"] not in unavailable]
        decision, model_ms = self._decide(STATE_ACTOR_PROMPT if state_only else ACTOR_SYSTEM_PROMPT,
                                         context, frame, tools, "actor")
        if hasattr(self, "control"):
            if self._poll_operator(state):
                self.telemetry.record("operator_discarded_decision", {"tool": decision.name})
                return
        self._check_activity_progress(state)
        if self.stop_reason:
            return
        current_state = state
        game_input = decision.name not in {"inspect_scene", "objective_progress", "record_opportunity", "remember_interaction", "change_objective", "pursue_interest", "consider_interest",
                                          "read_life_text", "wiki_search", "search_history", "world_map", "stop_session"}
        if game_input or decision.name == "change_objective":
            current_state, _ = self._observe()
        fingerprint = self._action_fingerprint(decision, state)
        execute_started = time.perf_counter()
        blocked_before = list(self.world.blocked_paths) if hasattr(self, "world") else []
        if self._decision_scene_changed(decision, state, current_state):
            result = {"status": "rejected", "reason": "scene_changed_while_thinking", "state": current_state}
            self.inspect_next_scene = True
        elif fingerprint == self.last_action_fingerprint:
            result = {
                "status": "rejected",
                "error": "The identical action was already attempted with unchanged observable state; choose a different action.",
            }
        else:
            self.last_action_fingerprint = fingerprint
            result = self._execute_actor_tool(decision, frame, current_state)
            if life_policy.pickup_failed(decision.name, decision.arguments, current_state, result.get("state") or {}):
                result = {**result, "status": "blocked", "reason": "inventory_full: pickup failed; make room before collecting more"}
                if hasattr(self, "notebook"):
                    self.notebook.data["life"]["inventory_blocked"] = True
                    self.notebook._save()
            if game_input and current_state.get("worldReady"):
                after = result.get("state") or current_state
                self.last_interaction = {
                    **{key: current_state.get(key) for key in ("location", "year", "season", "day", "time")},
                    "tool": decision.name, "status": result.get("status"),
                    "reason": str(result.get("reason") or result.get("error") or "")[:180],
                    "changes": [key for key in ("location", "menu", "dialogueText", "inventoryCounts", "stamina", "health",
                                               "plantedCrops", "seedsSown", "wateredCrops", "harvestableCrops")
                                if current_state.get(key) != after.get(key)],
                    "dialogue": (after.get("dialogueText") or "")[:180],
                    "run_id": getattr(self, "run_id", None), "step": self.telemetry.step,
                }
        bridge_ms = round((time.perf_counter() - execute_started) * 1000)
        self.last_result = {"tool": decision.name, "status": result.get("status")}
        for key in ("error", "warning", "reason"):
            if key in result:
                self.last_result[key] = result[key]
        self.telemetry.record("tool_result", {"tool": decision.name, "bridge_ms": bridge_ms, "result": result,
                                             "actor_cycle_ms": round((time.perf_counter() - cycle_started) * 1000)})
        result_state = result.get("state") or current_state
        self._record_actor_lessons(decision, result, result_state, blocked_before)
        self._update_stall_watchdog(decision.name, result_state)
        self._check_activity_progress(result_state, decision=True)
        self._print_step("actor", decision, result, result_state, model_ms, bridge_ms)
        if result.get("state"):
            if self._checkpoint_if_saved(result["state"]):
                return
            self._complete_verified_objective(result["state"])
        self._track_action_retries(decision, result, current_state, result_state)
        if not self.continuous and self.decisions >= self.max_decisions and not self.stop_reason:
            self.stop_reason = "max_decisions_reached"

    @staticmethod
    def _decision_scene_changed(decision: ToolDecision, before: dict[str, Any], after: dict[str, Any]) -> bool:
        """Reject a decision only when the change would make *this* action wrong.

        Dialogue pages advance and villagers walk while the model thinks; that is the game
        running, not staleness. Pointer input and menu entries are the only coordinate-bound
        actions, so they are the only ones that care about geometry."""
        def changed(*fields: str) -> bool:
            return any(before.get(field) != after.get(field) for field in fields)

        def scene(state: dict[str, Any]) -> str:
            menu = str(state.get("menu") or "none")
            if menu.startswith("DialogueBox"):
                return "dialogue"
            return menu

        if changed("worldReady", "day", "season", "year") or (after.get("health") or 0) < (before.get("health") or 0):
            return True
        name = decision.name
        if name in {"click", "move_cursor", "scroll"}:
            return changed("location", "menu", "eventUp", "pixelX", "pixelY", "viewportWidth", "viewportHeight", "npcsNearby")
        if name == "click_menu_entry":
            return changed("menu", "menuEntries", "journal", "letterText")
        if name == "choose_dialogue_response":
            return changed("location", "eventId", "dialogueResponses")
        if name in {"press", "hold", "control_sequence", "idle", "wait"}:
            return (scene(before) != scene(after) or changed("location", "eventId")
                    or bool(before.get("dialogueResponses")) != bool(after.get("dialogueResponses")))
        # Skills and navigation verify the world themselves; only a different place or a cutscene invalidates them.
        return changed("location", "eventUp", "minigame")

    # Reflexes: things a person does without deliberating. Each runs local controls only.

    @staticmethod
    def _scene_needs_watching(state: dict[str, Any]) -> bool:
        if not state.get("worldReady") or state.get("dialogueResponses"):
            return False
        menu = str(state.get("menu") or "none")
        if menu.startswith("DialogueBox"):
            return True
        return bool(state.get("eventUp")) and not state.get("eventCanMove") and menu == "none"

    def _play_dialogue(self, state: dict[str, Any]) -> bool:
        """Read a conversation or cutscene through at reading pace. One model reaction follows, not one per page."""
        if not self._scene_needs_watching(state) or getattr(self, "dialogue_autoplay", True) is False:
            return False
        started = time.perf_counter()
        opening = state.get("dialogueText") or state.get("eventId")
        self.telemetry.record("scene_watching", {"opening": (opening or "")[:180], "eventId": state.get("eventId")}, include_recent=False)
        response = self.bridge.request("watch_dialogue", ticks=2400, pageTicks=90)
        reason = response.get("reason") or response.get("error") or response.get("status")
        if response.get("status") in {"error", "rejected"} and "unknown" in str(reason).lower():
            self.dialogue_autoplay = False
            self.telemetry.record("scene_watch_unavailable", {"reason": str(reason)}, include_recent=False)
            return False
        self.game_actions += 1
        self.telemetry.record("scene_watched", {"status": response.get("status"), "reason": reason,
                                                "elapsed_ms": round((time.perf_counter() - started) * 1000)})
        return True

    def _greet_in_passing(self, state: dict[str, Any]) -> bool:
        """Say hello to a villager within reach who has not been greeted today. No deliberation, no model call."""
        if (getattr(self, "operator_mode", None) == "finishing" or state.get("menu") != "none" or state.get("eventUp")
                or not state.get("playerFree") or not state.get("canMove") or (state.get("time") or 0) >= 2200
                or (state.get("health") or 0) < 50 or not hasattr(self, "bridge")):
            return False
        greeted = getattr(self, "greeted", {})
        if greeted.get("day") != calendar_day(state):
            greeted = {"day": calendar_day(state), "tried": {}}
        self.greeted = greeted
        now = time.monotonic()
        near = [npc for npc in state.get("npcsNearby", [])
                if npc.get("kind") == "Villager" and not npc.get("talkedToday")
                and abs(npc["x"] - state.get("tileX", 0)) + abs(npc["y"] - state.get("tileY", 0)) <= 6
                and now - greeted["tried"].get(npc.get("id", npc["name"]), -999) > 90]
        if not near:
            return False
        npc = min(near, key=lambda n: abs(n["x"] - state.get("tileX", 0)) + abs(n["y"] - state.get("tileY", 0)))
        identity = npc.get("id", npc["name"])
        greeted["tried"][identity] = now
        pending = lambda: hasattr(self, "control") and any((self.control.directory / "inbox").glob("*.json"))
        notice = {"id": f"npc:{identity}", "target_id": identity}
        result = approach_and_talk(self.bridge, notice, state, pending)
        self.game_actions += result["controls_executed"]
        self.telemetry.record("greeting", {"npc": npc["name"], "status": result["status"], "reason": result["reason"],
                                           "elapsed_ms": result["elapsed_ms"]})
        if result["status"] == "completed":
            after = self.bridge.observe().get("state") or {}
            self._play_dialogue(after)
        return True

    def _bedtime_due(self, state: dict[str, Any]) -> bool:
        forced = getattr(self, "forced_bedtime", None) is not None and self.forced_bedtime == calendar_day(state)
        return bool(state.get("worldReady") and (forced or (state.get("time") or 0) >= 2330)
                    and state.get("location") != "FarmHouse" and not state.get("eventUp")
                    and getattr(self, "operator_mode", None) != "finishing")

    def _bedtime_reflex(self, state: dict[str, Any]) -> bool:
        """Past 1 AM nobody deliberates about bed. Walk home and sleep without a model call."""
        if not self._bedtime_due(state) or (state.get("time") or 0) < 2500 or state.get("menu") != "none":
            return False
        self.telemetry.record("bedtime_reflex", {"time": state.get("time"), "location": state.get("location")})
        decision = ToolDecision("go_home_and_sleep", {}, {}, None)
        result = self._execute_actor_tool(decision, None, state)
        self.last_result = {"tool": "go_home_and_sleep", "status": result.get("status"), "reason": result.get("reason")}
        self.telemetry.record("tool_result", {"tool": "go_home_and_sleep", "source": "bedtime_reflex", "result": result})
        if result.get("state"):
            self._checkpoint_if_saved(result["state"])
        return True

    def _track_action_retries(self, decision: ToolDecision, result: dict[str, Any],
                             before: dict[str, Any], after: dict[str, Any]) -> None:
        if result.get("status") == "yielded":
            return
        active = self.ledger.snapshot().get("active")
        if (not self.continuous and getattr(self, "operator_mode", None) != "finishing") or result.get("reason") == "scene_changed_while_thinking":
            return
        if decision.name == "change_objective" and result.get("status") == "completed":
            return
        objective_id = (active or {}).get("id")
        if objective_id != getattr(self, "retry_objective_id", None) or not hasattr(self, "failed_targets"):
            self.retry_objective_id = objective_id
            self.failed_targets = {}
        # Clock ticks alone must not disguise a blocked action. Tool work, movement,
        # inventory changes, menus, and day changes still count as real progress.
        progressed = self._stall_fingerprint(before) != self._stall_fingerprint(after)
        intentional_wait = decision.name in {"idle", "wait"} and result.get("status") == "completed"
        self.retry_no_progress = 0 if progressed or intentional_wait else getattr(self, "retry_no_progress", 0) + 1
        arguments = {key: value for key, value in decision.arguments.items() if key not in {"say", "frame_id"}}
        name = decision.name
        if name in {"go_to_location", "travel_to"}:
            name, arguments = "travel", {"destination": arguments.get("location") or arguments.get("destination")}
        target = json.dumps([before.get("location"), name, arguments], sort_keys=True)
        if result.get("status") in {"blocked", "rejected", "timeout"} and not progressed:
            self.failed_targets[target] = self.failed_targets.get(target, 0) + 1
        elif result.get("status") == "completed":
            self.failed_targets.pop(target, None)
        failures = self.failed_targets.get(target, 0)
        if failures < 3 and self.retry_no_progress < 6:
            return
        if getattr(self, "operator_mode", None) == "finishing":
            self.stop_reason = "finish_needs_attention"
            self.telemetry.record("finish_blocked", {"failures": failures, "no_progress": self.retry_no_progress})
            return
        evidence = (f"Retry limit reached: {failures} failed attempts at {name}; "
                    f"{self.retry_no_progress} decisions without progress. "
                    f"Last result: {result.get('reason') or result.get('error') or result.get('status')}.")
        if active and self._retry_condition_key(active["success_condition"]) == "location=farmhouse" and self._bedtime_allowed(after):
            self.telemetry.record("home_return_blocked", {"objective_id": active["id"], "evidence": evidence})
            # Keep trying: forget the failed targets so a rerouted attempt is not rejected as a repeat.
            self.retry_no_progress = 0
            self.failed_targets = {}
            self.last_action_fingerprint = None
            return
        evidence += " Deferred until another day; choose a different agenda task."
        if active:
            self.ledger.data["active"]["retry_deferred_on"] = [after.get(key) for key in ("year", "season", "day")]
            self.ledger.block_objective(evidence)
        agenda_id = (active or {}).get("agenda_id")
        if agenda_id is not None:
            self.notebook.mark(agenda_id, "deferred", evidence)
        self.notebook.add_lesson(f"retry_limit:{(active or {}).get('goal', name)[:40]}", evidence, "auto", calendar_day(after))
        self.telemetry.record("objective_deferred" if active else "retry_recovery", {"objective_id": objective_id, "agenda_id": agenda_id,
                                                    "target": target, "failures": failures,
                                                    "no_progress_decisions": self.retry_no_progress, "evidence": evidence})
        self.director_feedback = evidence
        self.stall_review_requested = True
        self.retry_no_progress = 0
        self.failed_targets = {}
        self.last_action_fingerprint = None

    @staticmethod
    def _retry_condition_key(condition: str) -> str:
        if not condition:
            return "curiosity"
        clauses = [re.sub(r"\s+", "", clause).casefold().replace("==", "=") for clause in condition.split(",")]
        # A new wording or extra clause must not reopen the same failed destination.
        for clause in clauses:
            match = re.fullmatch(r"location(?:is|=)(.+)", clause)
            if match:
                return "location=" + match[1]
        return ",".join(sorted(clauses))

    def _plan_day_if_needed(self, state: dict[str, Any], frame: Frame) -> None:
        day = calendar_day(state)
        if not isinstance(day, int):
            return
        existing = self.notebook.data["days"].get(str(day))
        if self.notebook.current_day == day and existing is not None and (existing.get("reviewed") or existing["theme"]):
            return
        if self.notebook.current_day != day:
            self.notebook.rollup_week()
        self.notebook.start_day(day)
        if life_policy.mail_due(self.notebook.data["life"], state):
            return
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

        # Morning carries existing work. Real discoveries trigger explicit quest/notice
        # reviews; waking up alone must not manufacture another list of activities.
        self.notebook.set_agenda(day, [], "Continue existing intentions; review mail and changed quests.")
        agenda = self.notebook.data["days"][str(day)]["agenda"]
        self.telemetry.record("day_planned", {"day": day, "agenda_size": len(agenda)})

    def _request_agenda(self, state: dict[str, Any], frame: Frame, mode: str) -> bool:
        last_decision: ToolDecision | None = None
        last_errors: list[str] = []
        for attempt in range(1, 3):
            self.telemetry.step += 1
            decision, model_ms = self._decide(
                DIRECTOR_SYSTEM_PROMPT, self._context("director", state, frame), frame,
                [PLAN_DAY_TOOL], "director",
            )
            last_decision = decision
            if self._poll_operator(state):
                return False
            ignored_carried_ids = self._strip_invalid_carried_ids(decision.arguments, state)
            if ignored_carried_ids:
                self.telemetry.record("ignored_carried_ids", {"ids": ignored_carried_ids})
            last_errors = self._agenda_errors(decision.arguments, state, mode, bind_audience=attempt == 1)
            if not last_errors:
                self._apply_agenda(decision.arguments, state)
                self._settle_audience_demand(decision.arguments)
                self.director_feedback = None
                self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)
                return True
            self.director_feedback = "Invalid plan_day: " + "; ".join(last_errors)
            self.telemetry.record(
                "agenda_plan_rejected",
                {"attempt": attempt, "mode": mode, "errors": last_errors},
            )
            self._print_step(
                "director", decision, {"status": "rejected", "reason": self.director_feedback},
                state, model_ms, 0,
            )

        raise HarnessError("Intention review rejected: " + "; ".join(last_errors))

    def _agenda_errors(
        self, arguments: dict[str, Any], state: dict[str, Any], mode: str, bind_audience: bool = True
    ) -> list[str]:
        agenda = arguments.get("agenda", [])
        errors = []
        demand = self.notebook.audience_demand() if hasattr(self, "notebook") else None
        if demand is not None and bind_audience:
            expected_source = "chat:" + demand["id"]
            chat_sources = [str(item.get("source") or "") for item in agenda
                            if str(item.get("source") or "").startswith("chat:")]
            if chat_sources != [expected_source]:
                errors.append(
                    f"the audience asked for \"{demand['goal']}\" ({demand['support']} viewers): include exactly one "
                    f"agenda item that pursues it, with source \"{expected_source}\""
                )
        if len(agenda) > 8:
            errors.append("Keep the current review to at most 8 intentions; existing work persists.")
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

        day = calendar_day(state)
        if mode == "morning":
            # A life has threads: something toward an open quest and somewhere new, every day they exist.
            carried = {item["id"]: item for item in (self.notebook.remaining(day) if isinstance(day, int) else [])}
            sources = [str(item.get("source") or carried.get(item.get("carried_id") or "", {}).get("source") or "")
                       for item in agenda]
            categories = [item.get("category") for item in agenda]
            active_quests = [quest for quest in state.get("quests", []) if not quest.get("complete")]
            if active_quests and not any(source.startswith("quest:") for source in sources):
                ids = ", ".join(f"quest:{quest['id']} ({quest.get('title', '')})" for quest in active_quests[:4])
                errors.append(f"include one concrete step toward an open quest, with its source set to the quest id: {ids}")
            unvisited = self.world.summary(state.get("location"), state.get("time")).get("unvisited", []) if hasattr(self, "world") else []
            if unvisited and "exploring" not in categories:
                errors.append(f"include one exploring item for a place not yet visited, such as {', '.join(unvisited[:3])}")
        errors.extend(self.notebook.variety_errors(day, agenda, arguments.get("theme", ""), mode))
        return errors

    def _strip_invalid_carried_ids(
        self, arguments: dict[str, Any], state: dict[str, Any]
    ) -> list[str]:
        day = calendar_day(state)
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
            calendar_day(state), arguments["agenda"], arguments["theme"], arguments.get("dropped", [])
        )
        for item in self.notebook.data["days"][str(calendar_day(state))]["agenda"]:
            if item.get("category") == "event" and item["status"] in {"pending", "active", "carried"}:
                item["scope"] = self._scene_scope(state)
        self.notebook._save()

    def _settle_audience_demand(self, arguments: dict[str, Any]) -> None:
        """Say plainly whether the plan took the demand up; the overlay reports either way."""
        demand = self.notebook.audience_demand()
        if demand is None:
            return
        expected_source = "chat:" + demand["id"]
        bound = next((item for item in arguments.get("agenda", [])
                      if str(item.get("source") or "") == expected_source), None)
        if bound is None:
            self.notebook.mark_audience("missed", "The plan did not take it up.")
        else:
            self.notebook.mark_audience("bound", bound["goal"])
        self.telemetry.record("audience_demand_settled",
                              {"id": demand["id"], "status": "bound" if bound else "missed",
                               "goal": bound["goal"] if bound else None})

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
        self.notebook.set_farm_plan(zones, arguments["notes"], calendar_day(state))

    def _agenda_feedback(self, state: dict[str, Any]) -> None:
        if getattr(self, "director_feedback", None):
            return
        day = calendar_day(state)
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
        if getattr(self, "operator_mode", None) == "finishing":
            return False
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
            if getattr(self, "operator_guidance", ""):
                self.operator_guidance = ""
                self.telemetry.record("operator_guidance_cleared", {"objective_id": active["id"]})
            print(f"[{self.telemetry.step}] objective {active['id']} completed by harness", flush=True)
            return True
        return False

    def _director_review(self) -> None:
        self.telemetry.step += 1
        state, frame = self._observe()
        scope = self._scene_scope(state)
        context = self._context("director", state, frame)
        decision, model_ms = self._decide(DIRECTOR_SYSTEM_PROMPT, context, frame, DIRECTOR_TOOLS, "director")
        state, _ = self._observe()
        if self._scene_scope(state) != scope:
            self.telemetry.record("director_review_discarded", {"reason": "scene_scope_changed"})
            return
        self._apply_director_decision(decision, state)
        self.last_director_action_count = self.game_actions
        self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)

    def _director_state_key(self, state: dict[str, Any]) -> str:
        # A routine review recommends outcomes, not cursor coordinates or a movement sequence.
        # Invalidate it on objective, resource, crop, menu, location, or day changes.
        fields = ("worldReady", "location", "day", "season", "year", "menu", "eventUp", "eventId", "eventPhase",
                  "inventory", "plantedCrops", "seedsSown", "wateredCrops", "harvestableCrops", "money", "health", "questRevision", "mailCount")
        return json.dumps({"active": self.ledger.snapshot().get("active"),
                           "operator_revision": getattr(self, "operator_revision", 0),
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
        self.telemetry.record("model_request", {"role": role, "input_image": system_prompt != STATE_ACTOR_PROMPT,
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
        packet = json.loads(context)
        # Keep the ledger first even though each block is serialized with sorted keys.
        stable_context = "\n".join(
            json.dumps({key: packet.pop(key)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for key in ("objective_ledger", "notebook", "world")
        )
        context = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        decision = self.client.choose_tool(system_prompt, context, None if system_prompt == STATE_ACTOR_PROMPT else frame.data_url,
                                           tools, cache_namespace=role, stable_context=stable_context,
                                           reasoning_effort=getattr(self, "director_reasoning_effort", None) if role == "director" else None,
                                           max_tokens=2400 if role == "director" else 1200 if any(t["function"]["name"] == "review_quests" for t in tools) and len(tools) <= 4 else None)
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
        if (cumulative_cost >= budget_usd and self.stop_reason != "budget_reached"
                and getattr(self, "operator_mode", None) not in {"saved", "held", "needs_attention"}):
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
        if self._poll_operator(state):
            return
        if getattr(self, "operator_mode", None) == "finishing":
            return
        arguments = decision.arguments
        self.director_feedback = None
        if decision.name == "search_history":
            self.latest_history_results = self.history.search(**arguments)
        elif decision.name == "continue_objective":
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
                active = self.ledger.snapshot().get("active") or {}
                agenda_id = active.get("agenda_id")
                if (active.get("id") == getattr(self, "objective_decision_id", None)
                        and getattr(self, "objective_decisions", 0) >= 8
                        and hasattr(self, "notebook") and isinstance(state.get("day"), int)):
                    goal = active["goal"][:40]
                    self.notebook.add_lesson(
                        f"wasted_decisions:{goal}",
                        f"Change tactic sooner when working on: {goal}.", "auto", calendar_day(state),
                    )
                self.ledger.block_objective(arguments["evidence"])
                if agenda_id is not None and hasattr(self, "notebook"):
                    self.notebook.mark(agenda_id, "carried", arguments["evidence"])
        elif decision.name == "set_objective":
            try:
                validate_new_condition(arguments["success_condition"], state)
                agenda_id = arguments.get("agenda_id")
                day = calendar_day(state)
                bedtime_return = False
                if hasattr(self, "notebook") and isinstance(day, int):
                    deferred = self.notebook.data["days"].get(str(day), {}).get("agenda", [])
                    retry_key = self._retry_condition_key(arguments["success_condition"])
                    bedtime_return = retry_key == "location=farmhouse" and self._bedtime_allowed(state)
                    if self._target_deferred_today(retry_key, state):
                        raise ObjectiveError("This target reached its retry limit today; choose a different task.")
                remaining = (
                    self.notebook.remaining(day)
                    if hasattr(self, "notebook") and isinstance(day, int) else []
                )
                if bedtime_return:
                    remaining += [item for item in deferred if item["status"] == "deferred" and item["id"] == agenda_id]
                if agenda_id is not None and not any(item["id"] == agenda_id for item in remaining):
                    agenda_id = None
                self.ledger.set_objective(
                    arguments["goal"],
                    arguments["success_condition"],
                    arguments["milestone"],
                    agenda_id,
                )
                self._scope_event_objective(state)
                if agenda_id is not None and hasattr(self, "notebook"):
                    self.notebook.mark(agenda_id, "active")
                self.invalid_objective_attempts = {}
                self.consecutive_objective_rejections = 0
            except ObjectiveError as error:
                # Run 7a81817b cycled through deferred targets one paid call at a time because the
                # rejection replaced the agenda list; name the deferred keys and remaining items instead.
                day = calendar_day(state)
                has_notebook = hasattr(self, "notebook") and isinstance(day, int)
                remaining = [{key: item.get(key) for key in ("id", "goal", "slot", "status")}
                             for item in self.notebook.remaining(day)] if has_notebook else []
                deferred = self._deferred_targets_today(state) if has_notebook else []
                self.director_feedback = (
                    f"Previous set_objective was rejected: {error} "
                    f"Rejected condition: {arguments['success_condition']!r}. Propose a different, currently false condition. "
                    f"Targets already deferred today: {json.dumps(deferred)}. Remaining agenda items: "
                    + json.dumps(remaining, ensure_ascii=False, separators=(",", ":")) + "."
                )
                self.telemetry.record(
                    "objective_update_rejected",
                    {"reason": str(error), "arguments": arguments},
                )
                attempts = getattr(self, "invalid_objective_attempts", {})
                key = self._retry_condition_key(arguments["success_condition"])
                attempts[key] = attempts.get(key, 0) + 1
                self.invalid_objective_attempts = attempts
                if attempts[key] >= 3:
                    self.stop_reason = "director_repeated_invalid_objective"
                streak = getattr(self, "consecutive_objective_rejections", 0) + 1
                self.consecutive_objective_rejections = streak
                if streak >= 3:
                    delay = min(5 * 2 ** (streak - 3), 30)
                    self.director_backoff_until = time.monotonic() + delay
                    self.telemetry.record("director_rejection_streak", {"streak": streak, "backoff_seconds": delay})
        elif decision.name == "plan_day":
            day = calendar_day(state)
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
                self._settle_audience_demand(arguments)
        elif decision.name == "update_farm_plan":
            try:
                self._apply_farm_plan(arguments, state)
            except ValueError as error:
                self.director_feedback = f"Farm plan was rejected: {error}"
        elif decision.name == "reflect":
            self._record_reflection(calendar_day(state), arguments)
        else:
            raise HarnessError(f"Unknown director tool: {decision.name}")

    def _execute_life_tool(self, name: str, arguments: dict, state: dict) -> dict:
        life = self.notebook.data["life"]
        if name == "read_life_text":
            identity = arguments["id"]
            if identity.startswith("quest:"):
                content = life["quests"].get(identity.removeprefix("quest:"))
            else:
                content = life.get("texts", {}).get(identity) or life.get("notices", {}).get(identity)
            if content is None:
                return {"status": "rejected", "reason": "Unknown retained text or quest ID."}
            text = json.dumps(content, ensure_ascii=False)
            offset = arguments.get("offset", 0)
            if not isinstance(offset, int) or not 0 <= offset < len(text):
                return {"status": "rejected", "reason": "Choose an offset inside the retained text."}
            self.latest_life_text = {"id": identity, "offset": offset, "text": text[offset:offset + 4000],
                                     "nextOffset": offset + 4000 if offset + 4000 < len(text) else None}
            return {"status": "completed", **self.latest_life_text}
        if name == "open_menu_tab":
            if state.get("menu") == "none":
                state = self.bridge.request("press", buttons=["E"])["state"]
                self.game_actions += 1
            if not str(state.get("menu", "")).startswith("GameMenu"):
                return {"status": "rejected", "reason": "Resolve the current dialogue or menu first.", "state": state}
            entry = next((entry for entry in state.get("menuEntries", []) if entry["label"].casefold() == "tab: " + arguments["tab"].casefold()), None)
            if entry is None:
                return {"status": "rejected", "reason": "That tab is not currently available.", "state": state}
            return self._click_visible_menu_entry(entry, state)
        raise HarnessError(f"Unknown life tool: {name}")

    def _execute_actor_tool(
        self, decision: ToolDecision, frame: Frame, before_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        local = {"navigate_to", "go_to_location", "travel_to", "go_home_and_sleep", "plant_seeds",
                 "plant_nearest_seeds", "till_tiles", "water_crops", "clear_debris", "control_sequence", "check_mail", "look_at"}
        if decision.name not in local:
            event_work = (before_state or {}).get("eventUp")
            previous_id = (self.ledger.snapshot().get("active") or {}).get("id") if event_work else None
            result = self._execute_actor_action(decision, frame, before_state)
            if (event_work and (self.ledger.snapshot().get("active") or {}).get("id") != previous_id
                    and result.get("status") not in {"rejected", "blocked"}):
                self._scope_event_objective(before_state or {})
            return result
        bridge = self.bridge
        before_actions = self.game_actions
        urgent = getattr(self, "operator_mode", None) == "finishing" or decision.name == "go_home_and_sleep"
        pending = lambda: hasattr(self, "control") and any((self.control.directory / "inbox").glob("*.json"))
        self.bridge = AttentiveBridge(bridge, before_state or {}, pending, urgent,
                                     follow_route=decision.name in {"travel_to", "go_to_location", "go_home_and_sleep"})
        try:
            return self._execute_actor_action(decision, frame, before_state)
        except AttentionYield as pause:
            self.game_actions = before_actions + pause.result["controls_executed"]
            if pause.result["reason"] == "arrived_in_new_area" and hasattr(self, "world"):
                self.world.clear_blocked_path((before_state or {}).get("location"), pause.result["state"].get("location"))
            self.inspect_next_scene = pause.result["reason"] != "movement_segment_finished"
            self.telemetry.record("attention_yield", {"tool": decision.name, **pause.result}, include_recent=False)
            return pause.result
        finally:
            self.bridge = bridge

    def _execute_actor_action(
        self,
        decision: ToolDecision,
        frame: Frame,
        before_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert self.bridge is not None
        name = decision.name
        arguments = decision.arguments
        finishing = getattr(self, "operator_mode", None) == "finishing"
        if hasattr(self, "notebook"):
            urgent = finishing or (name == "go_home_and_sleep" and self._bedtime_allowed(before_state or {}))
            rejection = life_policy.guard(self.notebook.data["life"], name, arguments, before_state or {}, urgent)
            if rejection:
                return {"status": "rejected", "reason": rejection, "state": before_state}
        if name in {"read_life_text", "open_menu_tab"}:
            return self._execute_life_tool(name, arguments, before_state or {})
        if finishing and name == "check_mail":
            return {"status": "rejected", "reason": "Finish & Save has priority over checking notifications."}
        if name in {"pursue_interest", "consider_interest"}:
            if finishing:
                return {"status": "rejected", "reason": "Finish & Save has priority."}
            if name == "pursue_interest":
                active = self.ledger.snapshot().get("active")
                if active and active.get("agenda_id"):
                    self.notebook.mark(active["agenda_id"], "pending", arguments["reason"])
                self.ledger.pursue_interest(arguments["goal"], arguments["reason"])
                self.last_interaction = None
                self.telemetry.record("objective_changed_by_actor", {"objective": self.ledger.snapshot()["active"]})
            else:
                if not getattr(self, "last_interaction", None):
                    return {"status": "rejected", "reason": "First observe an interaction or attempt."}
                self.ledger.consider_interest(arguments["evidence"])
                self.notebook.data["life"].pop("responding_to", None)
                self.notebook._save()
                self.telemetry.record("interest_considered", {"evidence": arguments["evidence"]})
            return {"status": "recorded"}
        if name == "check_mail":
            response = check_mail(self.bridge, before_state or {})
            self.game_actions += response.get("controls_executed", 0)
            return response
        if name == "look_at":
            response = look_at(self.bridge, before_state or {}, arguments["x"], arguments["y"])
            self.game_actions += response.get("controls_executed", 0)
            target = response.get("target")
            if target and hasattr(self, "notebook"):
                tried = self.notebook.data["life"].setdefault("curiosities", {})
                tried[f"{(before_state or {}).get('location')}:{target['id']}"] = {
                    "day": calendar_day(before_state or {}), "outcome": response.get("reason") or "looked",
                    "changes": response.get("changes", [])}
                self.notebook._save()
                self.telemetry.record("curiosity", {"target": target, "status": response.get("status"),
                                                    "reason": response.get("reason"), "changes": response.get("changes")})
            return response
        if name == "click_menu_entry":
            entries = (before_state or {}).get("menuEntries", [])
            index = arguments["index"]
            if not isinstance(index, int) or not 0 <= index < len(entries):
                return {"status": "rejected", "reason": "Choose a currently visible menu entry index."}
            entry = entries[index]
            return self._click_visible_menu_entry(entry, before_state or {})
        if finishing and name in {"change_objective", "stop_session", "objective_progress", "plant_seeds", "plant_nearest_seeds", "till_tiles", "water_crops"}:
            return {"status": "rejected", "reason": "Operator requested saving. Return home and sleep; the harness verifies the save before stopping."}
        if name == "change_objective":
            return self._change_actor_objective(arguments, before_state or {})
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
            if not finishing and not self._bedtime_allowed(before_state or {}):
                return {
                    "status": "rejected",
                    "reason": "bedtime_not_allowed_before_2000_unless_exhausted",
                    "time": (before_state or {}).get("time"),
                    "stamina": (before_state or {}).get("stamina"),
                }
            if hasattr(self, "notebook") and not finishing:
                self._reflect_before_sleep(before_state or {}, frame)
            response = go_home_and_sleep(self.bridge, before_state or {},
                                         45 if self.continuous or finishing else self.max_actions - self.game_actions,
                                         self.world)
            self.game_actions += response["controls_executed"]
            return response
        if name in {"travel_to", "go_to_location"}:
            state = before_state or {}
            destination = arguments.get("destination") or arguments["location"]
            hours = self.world.edge_hours(state.get("location") or "", destination)
            if hours and isinstance(state.get("time"), int) and not self.world._is_open(state["time"], *hours):
                return {"status": "rejected", "reason": f"door_closed_until_{hours[0]}",
                        "openTime": hours[0], "closeTime": hours[1]}
            action_budget = (max(3, len(self.world.nodes) * 3) if self.continuous
                             else self.max_actions - self.game_actions)
            response = travel_to(
                self.bridge, before_state or {}, self.world,
                destination, action_budget
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
        elif name == "close_menu":
            response = self.bridge.request("close_menu")
        elif name == "inventory_move":
            response = self.bridge.request("inventory_move", fromSlot=arguments["from_slot"], toSlot=arguments["to_slot"])
        elif name == "inventory_trash":
            response = self.bridge.request("inventory_trash", slot=arguments["slot"])
        elif name == "inventory_drop":
            response = self.bridge.request("inventory_drop", slot=arguments["slot"], count=arguments["count"])
        elif name == "ship_item":
            response = self.bridge.request("ship_item", slot=arguments["slot"], count=arguments["count"])
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
        elif name == "remember_interaction":
            observed = getattr(self, "last_interaction", None)
            if observed is None:
                return {"status": "rejected", "reason": "No new attempted interaction to remember. Try or observe a real interaction first."}
            try:
                revisit = arguments.get("revisit_when")
                if revisit and evaluate_state_condition(revisit, before_state or {}) is None:
                    return {"status": "rejected", "reason": "Use a structured state condition for a future revisit."}
                entry = self.notebook.remember_interaction(
                    **{key: arguments[key] for key in ("subject", "interaction", "outcome", "preference", "note")},
                    observed=observed,
                    revisit_when=arguments.get("revisit_when"),
                )
            except ValueError as error:
                return {"status": "rejected", "reason": str(error)}
            self.last_interaction = None
            return {"status": "recorded", "subject": entry["subject"], "outcome": entry["outcome"], "preference": entry["preference"]}
        elif name == "search_history":
            self.latest_history_results = self.history.search(arguments["query"], arguments.get("kind", "all"), arguments.get("limit", 5))
            return {"status": "completed", "results": self.latest_history_results}
        elif name == "wiki_search":
            progress = getattr(self, "activity_progress", None)
            marker = progress.last_progress if progress else None
            if progress and getattr(self, "wiki_progress_marker", None) == marker:
                return {"status": "rejected", "reason": "A lookup was already attempted without new game progress. Try a different activity."}
            self.wiki_progress_marker = marker
            try:
                self.latest_wiki_results = self.wiki.search(arguments["query"])
            except (OSError, ValueError) as error:
                self.latest_wiki_results = []
                return {"status": "blocked", "reason": f"Wiki lookup unavailable: {error}. Choose another feasible activity."}
            return {"status": "completed", "results": self.latest_wiki_results}
        elif name == "stop_session":
            if getattr(self, "forever", False):
                reason = arguments["reason"]
                if "unsafe" in reason.lower() or "unrecoverable" in reason.lower():
                    self.stop_reason = reason
                    return {"status": "stopped", "reason": self.stop_reason}
                self.telemetry.record("stop_not_allowed_in_forever_mode", {"reason": reason})
                return {"status": "rejected", "reason": "stop_not_allowed_in_forever_mode"}
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

    def _change_actor_objective(self, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        active = self.ledger.snapshot().get("active")
        condition = arguments["success_condition"]
        try:
            validate_new_condition(condition, state)
        except ObjectiveError as error:
            return {"status": "rejected", "reason": str(error)}
        if any(not arguments[key].strip() for key in ("goal", "milestone", "reason")):
            return {"status": "rejected", "reason": "Explain your new intention, next step, and reason for changing plans."}
        target = self._retry_condition_key(condition)
        if active and target == self._retry_condition_key(active["success_condition"]):
            return {"status": "rejected", "reason": "This is the current target; act or change tactic without resetting its objective."}
        day = calendar_day(state)
        agenda = self.notebook.data["days"].get(str(day), {}).get("agenda", [])
        bedtime_return = target == "location=farmhouse" and self._bedtime_allowed(state)
        if self._target_deferred_today(target, state):
            return {"status": "rejected", "reason": "This target reached its retry limit today; choose a different pursuit."}
        agenda_id = arguments.get("agenda_id")
        if agenda_id is not None and not any(item["id"] == agenda_id and
                (item["status"] in {"pending", "carried"} or (bedtime_return and item["status"] == "deferred")) for item in agenda):
            # A misremembered agenda id is not a reason to waste the decision: it is simply a new pursuit.
            agenda_id = None
        self.ledger.set_objective(arguments["goal"], condition, arguments["milestone"], agenda_id,
                                  interruption_reason=arguments["reason"])
        if active and active.get("agenda_id"):
            self.notebook.mark(active["agenda_id"], "pending", arguments["reason"])
        if agenda_id is not None:
            self.notebook.mark(agenda_id, "active")
        self.director_feedback = None
        self.invalid_objective_attempts = {}
        self.telemetry.record("objective_changed_by_actor", {
            "interrupted_id": active["id"] if active else None,
            "objective": self.ledger.snapshot()["active"], "reason": arguments["reason"],
        })
        return {"status": "completed", "reason": "New intention saved; previous work remains unfinished."}

    def _deferred_targets_today(self, state: dict[str, Any]) -> list[str]:
        agenda = self.notebook.data["days"].get(str(calendar_day(state)), {}).get("agenda", [])
        today = [state.get(key) for key in ("year", "season", "day")]
        deferred = [item for item in agenda if item["status"] == "deferred"]
        deferred += [item for item in self.ledger.data["history"] if item.get("retry_deferred_on") == today]
        return sorted({self._retry_condition_key(item["success_condition"]) for item in deferred if item.get("success_condition")})

    def _target_deferred_today(self, target: str, state: dict[str, Any]) -> bool:
        if target == "location=farmhouse" and self._bedtime_allowed(state):
            return False
        return target in self._deferred_targets_today(state)

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
        day = calendar_day(state)
        if self.notebook.data["days"].get(str(day), {}).get("reflection"):
            return
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
            self._record_reflection(day, decision.arguments)
            self._print_step("director", decision, {"status": "applied"}, state, model_ms, 0)
        except (HarnessError, OpenRouterError, KeyError, TypeError, ValueError) as error:
            self.telemetry.record("reflection_skipped", {"reason": str(error)})
        for item in self.notebook.remaining(day):
            self.notebook.mark(item["id"], "carried", "Unfinished at bedtime.")

    def _record_reflection(self, day: int, arguments: dict[str, Any]) -> None:
        self.notebook.reflect(day, arguments["summary"], arguments["learned"])
        for lesson in arguments.get("lessons", []):
            text = " ".join(lesson.split())
            if text:
                self.notebook.add_lesson(f"reflection:{text.casefold()}", text, "reflection", day)

    def _record_actor_lessons(
        self, decision: ToolDecision, result: dict[str, Any], state: dict[str, Any],
        blocked_before: list[dict[str, Any]],
    ) -> None:
        day = calendar_day(state)
        if not hasattr(self, "notebook") or not isinstance(day, int):
            return
        reason = str(result.get("reason") or result.get("error") or "rejected")
        if result.get("status") == "rejected":
            text = {
                "outside_crop_zone": "Planting and tilling must stay in the crop zone.",
                "bedtime_not_allowed_before_2000_unless_exhausted":
                    "Bedtime is after 8 PM unless exhausted; fill the day.",
            }.get(reason, "Avoid: " + reason.replace("_", " ") + ".")
            match = re.fullmatch(r"door_closed_until_(\d+)", reason)
            if match:
                door_time = int(match[1])
                hour, minute = divmod(door_time, 100)
                destination = decision.arguments.get("destination") or decision.arguments.get("location") or "The destination"
                text = f"{destination} is closed until {hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}; plan visits after that."
            self.notebook.add_lesson(f"rejected:{reason}", text, "auto", day)
        if "tick_budget_exhausted" in reason:
            self.notebook.add_lesson(
                f"timeout:{decision.name}",
                f"Use a shorter route or a different tactic after {decision.name.replace('_', ' ')} runs out of time.",
                "auto", day,
            )
        for path in self.world.blocked_paths if hasattr(self, "world") else []:
            if path not in blocked_before:
                self.notebook.add_lesson(
                    f"blocked_path:{path['from']}->{path['to']}",
                    f"The path from {path['from']} to {path['to']} is blocked; clear it or use another route.",
                    "auto", day,
                )

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
            if hasattr(self, "notebook"):
                rejection = life_policy.guard(self.notebook.data["life"], "hold", step, current_state,
                                              getattr(self, "operator_mode", None) == "finishing")
                if rejection:
                    results.append({"status": "rejected", "reason": rejection, "state": current_state})
                    break
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
        if not self.attach and self.supervisor.launch_if_needed:
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
            "time": state.get("time") if decision.name in {"wait", "idle"} else None,
            "day": state.get("day"),
            "inventory": inventory,
        }
        if decision.name == "click_menu_entry":
            observable.update({key: state.get(key) for key in ("menuEntries", "journal", "letterText")})
        return json.dumps(
            {"tool": decision.name, "arguments": {key: value for key, value in decision.arguments.items() if key not in {"say", "frame_id"}},
             "state": observable},
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
    def _scene_scope(state: dict[str, Any]) -> dict:
        return {"day": calendar_day(state), "event": state.get("eventId") if state.get("eventUp") else None}

    def _expire_scene_work(self, state: dict[str, Any]) -> None:
        if not state.get("worldReady"):
            return
        scope = self._scene_scope(state)
        previous = getattr(self, "last_scene_scope", scope)
        self.last_scene_scope = scope
        def expired(old):
            return old and (old["day"] != scope["day"] or (old.get("event") and old["event"] != scope["event"]))
        if expired(getattr(self, "operator_guidance_scope", None)):
            self.operator_guidance = ""
            self.operator_guidance_scope = None
            self.telemetry.record("operator_guidance_cleared", {"reason": "scene_scope_expired"})
        if previous != scope:
            self.latest_life_text = None
            self.latest_wiki_results = []
            self.latest_history_results = []
            self.last_interaction = None
            self.inspect_next_scene = True
        active = self.ledger.snapshot().get("active")
        if active and expired(active.get("scope")):
            self.ledger.block_objective("The event or day for this temporary intention ended.")
            self.director_feedback = "Temporary event work expired. Choose from the current scene and remaining missions."
            self.stall_review_requested = True
        changed = False
        for day in self.notebook.data["days"].values():
            for item in day["agenda"]:
                if item["status"] in {"pending", "active", "carried", "deferred"} and expired(item.get("scope")):
                    item.update(status="dropped", note="Event scope expired; unrelated intentions retained.")
                    changed = True
        if changed:
            self.notebook._save()

    def _check_activity_progress(self, state: dict[str, Any], decision: bool = False) -> None:
        if getattr(self, "stop_reason", None) or getattr(self, "operator_mode", None) == "finishing":
            return
        if not hasattr(self, "activity_progress"):
            self.activity_progress = ActivityProgress()
        action = self.activity_progress.observe(state, decision)
        if action == "escalate":
            self._change_scene(state, "Nothing new has happened for a long while despite a replan.")
        elif action == "replan":
            self.director_feedback = ("No new game milestone for 45 seconds or 8 decisions. Small movements, menu changes, "
                "waiting and rewording the goal do not count. Use wiki_search once if game knowledge is missing; "
                "otherwise choose a different feasible activity.")
            self.stall_review_requested = True
            self.telemetry.record("activity_replan", {"reason": self.director_feedback})

    def _change_scene(self, state: dict[str, Any], evidence: str) -> None:
        """A stall is recovered by changing the character's mind, never by ending the run.

        Evening: go home. Otherwise drop the current intention so the director must choose a
        different one, and forget local retry state that may itself be the obstacle."""
        self.blocked_movements.clear()
        self.last_action_fingerprint = None
        self.stalled_decisions = 0
        if hasattr(self, "activity_progress"):
            self.activity_progress.reset()
        evening = (state.get("time") or 0) >= 2000 or (isinstance(state.get("stamina"), int) and state["stamina"] < 30)
        if evening and state.get("location") != "FarmHouse":
            self.forced_bedtime = calendar_day(state)
            self.telemetry.record("activity_escalated", {"action": "bedtime", "evidence": evidence})
            return
        active = self.ledger.snapshot().get("active")
        if active:
            self.ledger.block_objective(evidence + " Abandoned to try something else.")
            if active.get("agenda_id") and hasattr(self, "notebook"):
                self.notebook.mark(active["agenda_id"], "deferred", evidence)
        self.director_feedback = (evidence + " The previous intention was abandoned. Choose a clearly different activity "
                                  "in a different place or with different people; do not reword the old one.")
        self.stall_review_requested = True
        self.telemetry.record("activity_escalated", {"action": "new_objective", "evidence": evidence,
                                                     "abandoned": (active or {}).get("goal")})

    def _scope_event_objective(self, state: dict[str, Any]) -> None:
        if state.get("eventUp") and self.ledger.data.get("active"):
            self.ledger.data["active"]["scope"] = self._scene_scope(state)
            self.ledger._save()

    @staticmethod
    def _stall_fingerprint(state: dict[str, Any]) -> tuple[Any, ...]:
        """Clock ticks, spent stamina and animation flags do not prove useful progress."""
        return AutoplayHarness._progress_fingerprint({**state, "time": 0, "stamina": 0,
                                                     "canMove": True, "playerFree": True}) + (
            state.get("questRevision"), state.get("mailCount"), state.get("letterText"),
        )

    @staticmethod
    def _progress_fingerprint(state: dict[str, Any]) -> tuple[Any, ...]:
        inventory_counts = tuple(sorted((state.get("inventoryCounts") or {}).items()))
        return tuple(state.get(field) for field in (
            "location", "tileX", "tileY", "day", "time", "money", "stamina",
            "plantedCrops", "seedsSown", "wateredCrops", "tilledTiles", "harvestableCrops",
            "dialogueText", "eventUp", "playerFree", "canMove",
        )) + (inventory_counts, state.get("menu"))

    def _update_stall_watchdog(self, tool_name: str, state: dict[str, Any]) -> None:
        fingerprint = self._stall_fingerprint(state)
        self.recent_actor_tools.append(tool_name)
        del self.recent_actor_tools[:-3]
        recent = getattr(self, "recent_progress_fingerprints", [])
        recent.append(fingerprint)
        del recent[:-12]
        self.recent_progress_fingerprints = recent
        cycling = any(len(recent) >= 3 * period
                      and recent[-period:] == recent[-2 * period:-period] == recent[-3 * period:-2 * period]
                      for period in (2, 3, 4))
        if fingerprint == self.last_progress_fingerprint or cycling:
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
            if hasattr(self, "notebook") and isinstance(state.get("day"), int):
                self.notebook.add_lesson(
                    f"stall:{tools}", f"Change tactic when {tools.replace('_', ' ')} makes no progress.",
                    "auto", calendar_day(state),
                )
            self.director_feedback = (
                f"The last 8 decisions made no lasting progress or repeated the same states: {tools}. "
                "Choose a different tactic or objective."
            )
            self.stall_review_requested = True
        elif self.stalled_decisions == 16:
            assert self.bridge is not None
            focus = self.bridge.request("focus")
            display = {"status": "preserved"}
            if not self.attach and self.supervisor.launch_if_needed:
                display = self.bridge.request("set_display_mode", mode="borderless")
            self.telemetry.record(
                "stall_recovery_attempted",
                {"count": self.stalled_decisions, "focus": focus.get("status"),
                 "display_mode": display.get("status")},
            )
        elif self.stalled_decisions == 24:
            if self.continuous:
                self._change_scene(state, f"The last 24 decisions repeated the same states: {', '.join(self.recent_actor_tools)}.")
            else:
                self.stop_reason = "stalled"

    def _close_menu(self) -> dict[str, Any]:
        """Close whatever menu is open. The bridge returns a cursor-held item first; an old bridge falls back to Escape."""
        assert self.bridge is not None
        response = self.bridge.request("close_menu")
        reason = str(response.get("reason") or response.get("error") or "")
        if response.get("status") in {"error", "rejected"} and "unknown" in reason.lower():
            response = self.bridge.request("press", buttons=["Escape"])
            if (response.get("state", {}).get("menu") or "").startswith("GameMenu"):
                close = next((entry for entry in response["state"].get("menuEntries", []) if entry["label"] == "Close menu"), None)
                if close:
                    response = self.bridge.request("click", x=close["screenX"], y=close["screenY"], button="left")
        return response

    def _click_visible_menu_entry(self, entry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        self.game_actions += 1
        result = self.bridge.request("click", x=entry["screenX"], y=entry["screenY"], button="left")
        actual = result.get("state", {}).get("menu", "")
        label = entry["label"]
        if label.startswith("Tab: "):
            expected = label[5:].capitalize() + "Page"
            if actual.rsplit(":", 1)[-1] != expected:
                return {**result, "status": "interrupted", "reason": f"Requested {label}, but observed {actual}. Read the current targets before retrying."}
        elif label.startswith("Close ") and actual == state.get("menu"):
            return {**result, "status": "blocked", "reason": "The menu did not close. Check cursor-held items and current controls before retrying."}
        return result

    def _context(self, role: str, state: dict[str, Any], frame: Frame, state_only: bool = False) -> str:
        blocked_here = sorted(
            "+".join(buttons)
            for location, pixel_x, pixel_y, buttons in self.blocked_movements
            if (location, pixel_x, pixel_y) == (state.get("location"), state.get("pixelX"), state.get("pixelY"))
        )
        harness_state = {
            **{key: value for key, value in state.items() if key not in {"notices", "quests"}},
            "menuEntries": [{**entry, "index": index} for index, entry in enumerate(state.get("menuEntries", []))],
            "harnessBlockedDirectionsHere": blocked_here,
            "harnessLastResult": self.last_result,
            "harnessStaminaLow": (state.get("stamina") or 0) < 30 if state.get("worldReady") else False,
            "harnessBedtimeAllowed": getattr(self, "operator_mode", None) == "finishing" or self._bedtime_allowed(state),
            "harnessStalledDecisions": getattr(self, "stalled_decisions", 0),
        }
        if isinstance(state.get("curiosities"), list) and hasattr(self, "notebook"):
            tried = self.notebook.data["life"].get("curiosities", {})
            today = calendar_day(state)
            harness_state["curiosities"] = [
                {**item, "tried": tried.get(f"{state.get('location')}:{item.get('id')}", {}).get("day") == today}
                for item in state["curiosities"]]
        # Slow-changing fields first so a caching provider can reuse the longest possible prefix.
        packet = {
            "role": role,
            "objective_ledger": prompt_ledger(self.ledger.snapshot()),
            "notebook": (
                self.notebook.context(calendar_day(state), role, state) if hasattr(self, "notebook") else None
            ),
            "world": self.world.summary(state.get("location"), state.get("time")),
            "memory": self.telemetry.context(),
            "wiki_results": self.latest_wiki_results,
            "history_results": getattr(self, "latest_history_results", []),
            "operator": {"mode": getattr(self, "operator_mode", "playing"), "guidance": getattr(self, "operator_guidance", "")},
            "audience": self.notebook.audience_demand() if role == "director" and hasattr(self, "notebook") else None,
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
        if role == "actor" and getattr(self, "last_interaction", None):
            packet["recent_interaction"] = {key: value for key, value in self.last_interaction.items() if key != "run_id"}
        if hasattr(self, "notebook"):
            packet["life"] = self.notebook.life_context(state)
            packet["life"]["retrievedText"] = getattr(self, "latest_life_text", None)
        return self._budget_context(packet, role)

    def _budget_context(self, packet: dict[str, Any], role: str) -> str:
        """Estimate context tokens at 3.5 JSON characters per token and trim in priority order."""
        budget = ACTOR_CONTEXT_BUDGET if role == "actor" else DIRECTOR_CONTEXT_BUDGET

        def serialize() -> str:
            return json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        context = serialize()
        initial_tokens = len(context) / 3.5
        cuts = []
        for field in ("wiki_results", "history_results", "memory_recent", "lessons", "agenda_goals", "nearbyObjects", "cropsNearby", "controller_geometry", "interaction_memories",
                      "director_nearby_objects", "director_farm_layout", "actor_tillable_tiles", "older_action_history"):
            if len(context) / 3.5 <= budget:
                break
            if field in {"wiki_results", "history_results"}:
                packet[field] = []
            elif field == "memory_recent":
                memory = packet["memory"]
                memory["recent_events"] = memory.get("recent_events", [])[-6:]
            elif field == "lessons":
                if packet.get("notebook"):
                    packet["notebook"]["lessons"] = packet["notebook"].get("lessons", [])[:3]
            elif field == "agenda_goals":
                today = (packet.get("notebook") or {}).get("today") or {}
                for key in ("agenda", "carried"):
                    items = []
                    for item in today.get(key, []):
                        if isinstance(item, str):
                            parts = item.split("|", 3)
                            parts[-1] = parts[-1][:40]
                            items.append("|".join(parts))
                        else:
                            items.append({**item, "goal": item.get("goal", "")[:40]})
                    if key in today:
                        today[key] = items
            elif field == "controller_geometry":
                fields = ("farmLayout", "navigationRows", "diagnostics") if role == "actor" else ("navigationRows", "diagnostics")
                for key in fields:
                    packet["game_state"].pop(key, None)
            elif field == "director_nearby_objects":
                # Last resort for the director, which otherwise keeps every nearby object and the farm layout.
                if role == "director" and isinstance(packet["game_state"].get("nearbyObjects"), list):
                    packet["game_state"]["nearbyObjects"] = packet["game_state"]["nearbyObjects"][:12]
            elif field == "director_farm_layout":
                if role == "director":
                    packet["game_state"].pop("farmLayout", None)
            elif field == "actor_tillable_tiles":
                if role == "actor" and isinstance(packet["game_state"].get("tillableNearby"), list):
                    packet["game_state"]["tillableNearby"] = packet["game_state"]["tillableNearby"][:6]
            elif field == "older_action_history":
                recent = packet["memory"].get("recent_events", [])
                while len(recent) > 2 and len(serialize()) / 3.5 > budget:
                    recent.pop(0)
            elif field == "interaction_memories":
                memories = (packet.get("notebook") or {}).get("interactions", [])
                while memories and len(serialize()) / 3.5 > budget:
                    memories.pop()
            elif field != "nearbyObjects" or role == "actor":
                limit = 12 if field == "nearbyObjects" else 24
                value = packet["game_state"].get(field)
                if isinstance(value, dict) and "rows" in value:
                    packet["game_state"][field] = {**value, "rows": value["rows"][:limit]}
                elif isinstance(value, list):
                    packet["game_state"][field] = value[:limit]
            updated = serialize()
            if updated != context:
                cuts.append(field)
            context = updated
        estimated_tokens = len(context) / 3.5
        if cuts or estimated_tokens > budget:
            self.telemetry.record("context_trimmed", {
                "role": role, "budget": budget, "initial_tokens": round(initial_tokens, 1),
                "estimated_tokens": round(estimated_tokens, 1), "cuts": cuts,
                "within_budget": estimated_tokens <= budget,
            }, include_recent=False)
        if estimated_tokens > budget:
            raise HarnessError(f"{role} context exceeds {budget} estimated tokens after allowed trims ({estimated_tokens:.0f}); protected fields retained.")
        return context

    @staticmethod
    def _require_current_frame(arguments: dict[str, Any], frame: Frame) -> None:
        if arguments.get("frame_id") != frame.frame_id:
            raise HarnessError("The model attempted a pointer action against a stale frame.")

    @staticmethod
    def _pixels(normalized_x: float, normalized_y: float, frame: Frame) -> tuple[int, int]:
        x = round(float(normalized_x) * (frame.width - 1))
        y = round(float(normalized_y) * (frame.height - 1))
        return x, y
