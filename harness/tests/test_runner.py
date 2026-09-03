import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.capture import Frame
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from autoplay_harness.runner import ACTOR_CONTEXT_BUDGET, DIRECTOR_CONTEXT_BUDGET, AutoplayHarness, HarnessError
from autoplay_harness.state_actor import STATE_ACTOR_TOOLS, STATE_ACTOR_PROMPT
from autoplay_harness.telemetry import Telemetry
from autoplay_harness.tools import ACTOR_TOOLS, DIRECTOR_TOOLS, PLAN_DAY_TOOL, REFLECT_TOOL, UPDATE_FARM_PLAN_TOOL
from autoplay_harness.world import WorldMap


class _Bridge:
    def __init__(self) -> None:
        self.calls = []

    def request(self, request_type, **arguments):
        self.calls.append((request_type, arguments))
        return {"status": "completed", "state": {"location": "Farm", "pixelX": 1, "pixelY": 2}}


class _Wiki:
    def search(self, query):
        return [{"title": query}]


class _Client:
    def choose_tool(self, *args, **kwargs):
        raise AssertionError("The model should not be called when the objective condition is already true.")


class RunnerTests(unittest.TestCase):
    @patch("autoplay_harness.runner.time.sleep")
    @patch("autoplay_harness.runner.ScreenCapture")
    @patch("autoplay_harness.runner.GameSupervisor")
    def test_finally_continues_when_cleanup_steps_raise(self, supervisor, capture, _sleep):
        bridge = supervisor.return_value.connect_bridge.return_value
        bridge.request.return_value = {"status": "completed"}
        bridge.close.side_effect = RuntimeError("close failed")
        capture.return_value.release.side_effect = RuntimeError("capture failed")
        with tempfile.TemporaryDirectory() as directory:
            harness = AutoplayHarness(
                Path(directory), "Plant five", "plantedCrops >= 5",
                OpenRouterClient.QWEN_MODEL, "secret", continuous=True, isolated_state=True,
            )
            harness._observe = Mock(side_effect=[({}, self.frame), RuntimeError("unexpected")])
            harness._ensure_world_loaded = Mock()
            harness.recorder = Mock()
            harness.recorder.stop.side_effect = RuntimeError("recorder failed")
            harness.telemetry = Mock(summary={"usage": {"cost": 0}})
            harness.client.cancelled = Mock()
            harness.director_executor = Mock()
            harness._finish_director_review = Mock(side_effect=RuntimeError("director failed"))

            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                harness.run()

            harness.recorder.stop.assert_called_once_with()
            bridge.close.assert_called_once_with()
            capture.return_value.release.assert_called_once_with()
            supervisor.return_value.stop_started_game.assert_called_once_with()
            harness.client.cancelled.set.assert_called_once_with()
            harness.director_executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
            harness.telemetry.save_summary.assert_called_once_with()

    @patch("autoplay_harness.runner.time.sleep")
    @patch("autoplay_harness.runner.ScreenCapture")
    @patch("autoplay_harness.runner.GameSupervisor")
    def test_unexpected_loop_exception_records_fatal_error(self, supervisor, _capture, _sleep):
        supervisor.return_value.connect_bridge.return_value.request.return_value = {"status": "completed"}
        with tempfile.TemporaryDirectory() as directory:
            harness = AutoplayHarness(
                Path(directory), "Plant five", "plantedCrops >= 5",
                OpenRouterClient.QWEN_MODEL, "secret", continuous=True, isolated_state=True,
            )
            harness._observe = Mock(side_effect=[({}, self.frame), RuntimeError("unexpected")])
            harness._ensure_world_loaded = Mock()

            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                harness.run()

            events = harness.telemetry.events_path.read_text(encoding="utf-8")
            self.assertIn('"type":"fatal_error"', events)
            self.assertIn('"error_type":"RuntimeError"', events)
            self.assertIn("unexpected", events)

    @patch("autoplay_harness.runner.time.sleep")
    @patch("autoplay_harness.runner.ScreenCapture")
    @patch("autoplay_harness.runner.GameSupervisor")
    def test_max_minutes_stops_bounded_and_continuous_runs(self, supervisor, _capture, _sleep):
        supervisor.return_value.connect_bridge.return_value.request.return_value = {"status": "completed"}
        for continuous in (False, True):
            with self.subTest(continuous=continuous), tempfile.TemporaryDirectory() as directory:
                harness = AutoplayHarness(
                    Path(directory), "Plant five", "plantedCrops >= 5",
                    OpenRouterClient.QWEN_MODEL, "secret", continuous=continuous,
                    isolated_state=True, max_minutes=1,
                )
                harness._observe = Mock(return_value=({}, self.frame))
                harness._ensure_world_loaded = Mock()
                with patch("autoplay_harness.runner.time.monotonic", side_effect=[0, 61]):
                    self.assertEqual("time_limit_reached", harness.run())
                self.assertEqual(0, harness.decisions)

    def test_forever_stop_tool_rejects_routine_reason_and_honors_unsafe_reason(self) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.forever = True
        harness.bridge = _Bridge()
        harness.stop_reason = None
        harness.telemetry = Mock()

        rejected = harness._execute_actor_tool(
            ToolDecision("stop_session", {"reason": "Objective finished"}, {}, None), self.frame
        )
        self.assertEqual({"status": "rejected", "reason": "stop_not_allowed_in_forever_mode"}, rejected)
        self.assertIsNone(harness.stop_reason)
        harness.telemetry.record.assert_called_once_with(
            "stop_not_allowed_in_forever_mode", {"reason": "Objective finished"}
        )

        stopped = harness._execute_actor_tool(
            ToolDecision("stop_session", {"reason": "Unsafe state is unrecoverable"}, {}, None), self.frame
        )
        self.assertEqual("stopped", stopped["status"])
        self.assertEqual("Unsafe state is unrecoverable", harness.stop_reason)

    @patch("autoplay_harness.runner.time.sleep")
    @patch("autoplay_harness.runner.ScreenCapture")
    @patch("autoplay_harness.runner.GameSupervisor")
    def test_run_stops_at_call_cap_even_when_every_actor_request_fails(self, supervisor, _capture, _sleep):
        supervisor.return_value.connect_bridge.return_value.request.return_value = {"status": "completed"}
        with tempfile.TemporaryDirectory() as directory:
            harness = AutoplayHarness(Path(directory), "Plant five", "plantedCrops >= 5",
                                      OpenRouterClient.QWEN_MODEL, "secret", max_decisions=2, isolated_state=True)
            harness._observe = lambda: ({"plantedCrops": 0}, self.frame)
            harness._ensure_world_loaded = Mock()
            harness._start_director_review = Mock()
            harness.client.choose_tool = Mock(side_effect=OpenRouterError("HTTP 429"))
            self.assertEqual("max_decisions_reached", harness.run())
            self.assertEqual(2, harness.client.choose_tool.call_count)
            self.assertEqual(0, harness.game_actions)

    def test_failed_model_call_consumes_decision_budget_without_game_input(self):
        harness = object.__new__(AutoplayHarness)
        harness.telemetry = Mock(step=0)
        harness.ledger = Mock()
        harness.ledger.snapshot.return_value = {"active": None}
        harness._observe = lambda: ({}, self.frame)
        harness._context = Mock(return_value="context")
        harness._decide = Mock(side_effect=OpenRouterError("HTTP 429"))
        harness._execute_actor_tool = Mock()
        harness.decisions = 0
        with self.assertRaises(OpenRouterError):
            harness._actor_step()
        self.assertEqual(1, harness.decisions)
        harness._execute_actor_tool.assert_not_called()

    @patch("autoplay_harness.runner.ScreenCapture")
    def test_isolated_state_does_not_read_or_replace_shared_ledger(self, _capture):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "harness/state/objectives.json"
            shared.parent.mkdir(parents=True)
            shared.write_text("shared ledger sentinel", encoding="utf-8")
            harness = AutoplayHarness(root, "Plant five", "plantedCrops >= 5",
                                      OpenRouterClient.QWEN_MODEL, "secret", isolated_state=True)
            try:
                self.assertEqual("shared ledger sentinel", shared.read_text(encoding="utf-8"))
                self.assertEqual(harness.run_directory / "state", harness.state_directory)
                self.assertEqual([], harness.ledger.snapshot()["history"])
                self.assertEqual("Plant five", harness.ledger.snapshot()["active"]["goal"])
            finally:
                harness.director_executor.shutdown()

    def test_final_action_records_completion_without_another_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Plant five", "plantedCrops >= 5")
            harness.telemetry = Telemetry(Path(directory) / "run")
            harness._observe = lambda: ({"plantedCrops": 0}, self.frame)
            harness._context = Mock(return_value="context")
            harness._decide = Mock(return_value=(ToolDecision("plant_seeds", {}, {}, None), 1))
            harness._execute_actor_tool = Mock(return_value={"status": "completed", "state": {"plantedCrops": 5}})
            harness._print_step = Mock()
            harness.decisions = 0
            harness.max_decisions = 1
            harness.continuous = False
            harness.stop_reason = None
            harness.last_action_fingerprint = None
            harness.last_progress_fingerprint = None
            harness.stalled_decisions = 0
            harness.recent_actor_tools = []
            harness.stall_review_requested = False
            harness._actor_step()
            self.assertEqual("completed", harness.ledger.snapshot()["history"][-1]["status"])
            self.assertEqual(1, harness._decide.call_count)

    def setUp(self) -> None:
        self.frame = Frame("frame-1", 1920, 1080, "data:image/jpeg;base64,", None)

    def test_normalized_coordinates_map_to_frame(self) -> None:
        self.assertEqual((1919, 1079), AutoplayHarness._pixels(1, 1, self.frame))
        self.assertEqual((0, 0), AutoplayHarness._pixels(0, 0, self.frame))

    @patch("autoplay_harness.runner.ScreenCapture")
    def test_existing_objective_does_not_schedule_duplicate_startup_review(self, _capture) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = AutoplayHarness(Path(directory), "Reach farm", "location is Farm",
                                      OpenRouterClient.LUNA_MODEL, "test", director_interval=24)
            try:
                self.assertLess(harness.game_actions - harness.last_director_action_count, harness.director_interval)
                self.assertIsNotNone(harness.ledger.snapshot()["active"])
            finally:
                harness.director_executor.shutdown()

    def test_actor_completes_verified_objective_without_a_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.ledger = ObjectiveLedger(
                Path(directory) / "objectives.json", "Return home", "location is FarmHouse"
            )
            harness.telemetry = Telemetry(Path(directory) / "run")
            harness.client = _Client()
            harness._observe = lambda: ({"location": "FarmHouse"}, self.frame)

            harness._actor_step()

            snapshot = harness.ledger.snapshot()
            self.assertIsNone(snapshot["active"])
            self.assertEqual("completed", snapshot["history"][-1]["status"])
            self.assertIn("Harness verified", snapshot["history"][-1]["evidence"])

    def test_movement_guard_ignores_facing_presses_and_short_holds(self) -> None:
        state = {"location": "Farm", "pixelX": 1, "pixelY": 2}
        self.assertIsNone(AutoplayHarness._movement_key("press", {"buttons": ["W"]}, state))
        self.assertIsNone(AutoplayHarness._movement_key("hold", {"buttons": ["W"], "ticks": 3}, state))
        self.assertIsNotNone(AutoplayHarness._movement_key("hold", {"buttons": ["W"], "ticks": 15}, state))

    def test_context_surfaces_blocked_directions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Reach the farm", "location is Farm")
            harness.telemetry = Telemetry(Path(directory) / "run")
            harness.latest_wiki_results = []
            harness.blocked_movements = {("FarmHouse", 5, 6, ("W",)), ("FarmHouse", 9, 9, ("A",))}
            harness.last_result = {"tool": "hold", "status": "blocked"}
            harness.director_feedback = None
            harness.stalled_decisions = 5
            harness.continuous = True
            harness.game_actions = 3
            harness.decisions = 4
            harness.director_interval = 24
            harness.world = Mock()
            harness.world.summary.return_value = {"here": "FarmHouse", "exits": ["Farm"]}

            context = harness._context(
                "actor", {"worldReady": True, "location": "FarmHouse", "pixelX": 5, "pixelY": 6,
                          "time": 1200, "health": 100, "stamina": 12}, self.frame
            )

            packet = json.loads(context)
            self.assertEqual("Reach the farm", packet["objective_ledger"]["active"]["goal"])
            self.assertIn('"harnessBlockedDirectionsHere":["W"]', context)
            self.assertIn('"harnessStaminaLow":true', context)
            self.assertIn('"harnessBedtimeAllowed":true', context)
            self.assertIn('"harnessStalledDecisions":5', context)
            self.assertEqual({"tool": "hold", "status": "blocked"}, packet["game_state"]["harnessLastResult"])
            self.assertEqual({"here": "FarmHouse", "exits": ["Farm"]}, packet["world"])

    @patch("autoplay_harness.runner.travel_to")
    def test_dispatches_travel_to_and_includes_world_in_actor_context(self, travel_skill) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = _Bridge()
            harness.world = Mock(nodes={"Farm": {}, "Town": {}})
            harness.world.summary.return_value = {"here": "Farm", "exits": ["Town"]}
            harness.ledger = ObjectiveLedger(
                Path(directory) / "objectives.json", "Reach town", "location is Town"
            )
            harness.telemetry = Telemetry(Path(directory) / "run")
            harness.latest_wiki_results = []
            harness.blocked_movements = set()
            harness.last_result = None
            harness.director_feedback = None
            harness.stalled_decisions = 0
            harness.continuous = False
            harness.max_actions = 9
            harness.max_decisions = 9
            harness.game_actions = 0
            harness.decisions = 1
            travel_skill.return_value = {
                "status": "completed", "controls_executed": 3, "state": {"location": "Town"}
            }

            result = harness._execute_actor_tool(
                ToolDecision("travel_to", {"destination": "Town"}, {}, None),
                self.frame,
                {"location": "Farm", "time": 900},
            )
            context = harness._context("actor", {"location": "Farm", "time": 900}, self.frame)

            self.assertEqual("completed", result["status"])
            self.assertEqual(3, harness.game_actions)
            travel_skill.assert_called_once_with(
                harness.bridge, {"location": "Farm", "time": 900}, harness.world, "Town", 9
            )
            self.assertEqual({"here": "Farm", "exits": ["Town"]}, json.loads(context)["world"])

    def test_stale_frame_is_rejected(self) -> None:
        with self.assertRaises(HarnessError):
            AutoplayHarness._require_current_frame({"frame_id": "old"}, self.frame)

    def test_same_position_detects_blocked_movement(self) -> None:
        before = {"location": "FarmHouse", "pixelX": 348, "pixelY": 671}
        after = {"location": "FarmHouse", "pixelX": 348, "pixelY": 671}
        self.assertTrue(AutoplayHarness._same_position(before, after))

    def test_action_fingerprint_changes_only_with_action_or_observable_state(self) -> None:
        decision = ToolDecision("press", {"buttons": ["D3"]}, {}, None)
        state = {"location": "Farm", "pixelX": 1, "pixelY": 2, "tool": "Watering Can"}
        same = AutoplayHarness._action_fingerprint(decision, dict(state))
        changed = AutoplayHarness._action_fingerprint(decision, {**state, "tool": "Scythe"})

        self.assertEqual(same, AutoplayHarness._action_fingerprint(decision, dict(state)))
        self.assertNotEqual(same, changed)

    def test_observe_refocuses_an_inactive_game_window(self) -> None:
        class FocusBridge(_Bridge):
            def __init__(self) -> None:
                super().__init__()
                self.observations = [
                    {"worldReady": True, "gameActive": False, "menu": "none"},
                    {"worldReady": True, "gameActive": True, "menu": "none"},
                ]

            def observe(self):
                return {"status": "completed", "state": self.observations.pop(0)}

        harness = object.__new__(AutoplayHarness)
        harness.bridge = FocusBridge()
        harness.capture = Mock()
        harness.capture.capture.return_value = self.frame
        harness.telemetry = Mock()
        harness.world = Mock()
        harness.recorder = None
        harness.title_screen_ready = True

        state, _ = harness._observe()

        self.assertTrue(state["gameActive"])
        self.assertEqual(("focus", {}), harness.bridge.calls[0])
        self.assertEqual(
            ("wait", {"field": "game_active", "value": "true", "ticks": 120}),
            harness.bridge.calls[1],
        )
        harness.telemetry.record.assert_any_call(
            "window_refocused", {"attempt": 1, "game_active_after": True}
        )

    def test_dead_recorder_is_restarted(self) -> None:
        recorder = Mock()
        recorder.directory.glob.return_value = [Path("gameplay-000.mp4")]
        recorder.process = object()
        recorder.is_alive.return_value = False
        harness = object.__new__(AutoplayHarness)
        harness.recorder = recorder
        harness.telemetry = Mock()

        harness._restart_recorder_if_needed()

        recorder.start.assert_called_once_with()
        harness.telemetry.record.assert_called_once_with(
            "recording_restarted", {"segment_count": 1}
        )

    @patch("autoplay_harness.runner.time.sleep")
    def test_bootstrap_loads_first_save_without_model_decisions(self, _sleep) -> None:
        class BootstrapBridge(_Bridge):
            def __init__(self) -> None:
                super().__init__()
                self.observations = [
                    {"menu": "TitleMenu", "viewportWidth": 1920, "viewportHeight": 1080},
                    {"menu": "TitleMenu:LoadGameMenu", "viewportWidth": 1920, "viewportHeight": 1080},
                ]

            def observe(self):
                return {"status": "completed", "state": self.observations.pop(0)}

            def request(self, request_type, **arguments):
                self.calls.append((request_type, arguments))
                state = {"worldReady": request_type == "wait"}
                return {"status": "completed", "state": state}

        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = BootstrapBridge()
            harness.telemetry = Telemetry(Path(directory) / "run")

            harness._ensure_world_loaded()

            self.assertEqual(("click", {"x": 835, "y": 972, "button": "left"}), harness.bridge.calls[0])
            self.assertEqual(("click", {"x": 960, "y": 302, "button": "left"}), harness.bridge.calls[1])
            self.assertEqual("wait", harness.bridge.calls[2][0])

    @patch("autoplay_harness.runner.time.sleep")
    def test_dispatches_every_game_bound_actor_tool(self, _sleep) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        harness.world = Mock()
        harness.world.edge_hours.return_value = None
        harness.blocked_movements = set()
        harness.game_actions = 0
        cases = [
            ("navigate_to", {"tile_x": 12, "tile_y": 7}, ("navigate", {"x": 12, "y": 7, "ticks": 600})),
            ("go_to_location", {"location": "BusStop"}, ("go_to_location", {"location": "BusStop", "ticks": 600})),
            ("press", {"buttons": ["W"]}, ("press", {"buttons": ["W"]})),
            ("hold", {"buttons": ["A"], "ticks": 12}, ("hold", {"buttons": ["A"], "ticks": 12})),
            (
                "move_cursor",
                {"frame_id": "frame-1", "x": 0.5, "y": 0.25},
                ("move_cursor", {"x": 960, "y": 270}),
            ),
            (
                "click",
                {"frame_id": "frame-1", "x": 0.5, "y": 0.25, "button": "right"},
                ("click", {"x": 960, "y": 270, "button": "right"}),
            ),
            (
                "drag",
                {
                    "frame_id": "frame-1",
                    "start_x": 0,
                    "start_y": 0,
                    "end_x": 1,
                    "end_y": 1,
                    "button": "left",
                    "ticks": 9,
                },
                (
                    "drag",
                    {"startX": 0, "startY": 0, "endX": 1919, "endY": 1079, "button": "left", "ticks": 9},
                ),
            ),
            ("scroll", {"direction": "up", "steps": 2}, ("scroll", {"direction": "up", "steps": 2})),
            (
                "wait",
                {"field": "player_free", "value": True, "timeout_ticks": 30},
                ("wait", {"field": "player_free", "value": "true", "ticks": 30}),
            ),
            ("idle", {"ticks": 90}, ("idle", {"ticks": 90})),
            (
                "choose_dialogue_response",
                {"index": 1},
                ("choose_dialogue_response", {"index": 1}),
            ),
        ]

        for name, arguments, expected in cases:
            with self.subTest(tool=name):
                result = harness._execute_actor_tool(ToolDecision(name, arguments, {}, None), self.frame)
                self.assertEqual("completed", result["status"])
                self.assertEqual(expected, harness.bridge.calls[-1])
        self.assertEqual(len(cases), harness.game_actions)
        _sleep.assert_not_called()

    def test_bedtime_guard_rejects_early_sleep_unless_exhausted(self) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        harness.game_actions = 0
        harness.continuous = True

        def dispatch_sleep(bridge, _state, _actions):
            response = bridge.request("sleep_test")
            return {**response, "controls_executed": 1}

        decision = ToolDecision("go_home_and_sleep", {}, {}, None)
        with patch("autoplay_harness.runner.go_home_and_sleep", side_effect=dispatch_sleep):
            rejected = harness._execute_actor_tool(
                decision, self.frame, {"time": 1200, "stamina": 200, "health": 100}
            )
            self.assertEqual("rejected", rejected["status"])
            self.assertEqual("bedtime_not_allowed_before_2000_unless_exhausted", rejected["reason"])
            self.assertEqual([], harness.bridge.calls)

            exhausted = harness._execute_actor_tool(
                decision, self.frame, {"time": 1200, "stamina": 20, "health": 100}
            )
            evening = harness._execute_actor_tool(
                decision, self.frame, {"time": 2100, "stamina": 200, "health": 100}
            )

        self.assertEqual("completed", exhausted["status"])
        self.assertEqual("completed", evening["status"])
        self.assertEqual([("sleep_test", {}), ("sleep_test", {})], harness.bridge.calls)

    def test_stall_watchdog_detects_resets_recovers_and_stops(self) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        harness.telemetry = Mock()
        harness.stalled_decisions = 0
        harness.recent_actor_tools = []
        harness.stall_review_requested = False
        harness.director_feedback = None
        harness.stop_reason = None
        state = {
            "location": "Farm", "tileX": 10, "tileY": 11, "day": 16, "time": 720,
            "money": 500, "stamina": 200, "plantedCrops": 4, "wateredCrops": 0,
            "tilledTiles": 4, "harvestableCrops": 0, "inventoryCounts": {"Wood": 3},
            "menu": "none",
        }
        harness.last_progress_fingerprint = harness._progress_fingerprint(state)

        for _ in range(8):
            harness._update_stall_watchdog("navigate_to", state)

        self.assertEqual(8, harness.stalled_decisions)
        self.assertTrue(harness.stall_review_requested)
        self.assertIn("The last 8 decisions changed nothing", harness.director_feedback)
        harness.telemetry.record.assert_any_call(
            "stall_detected", {"count": 8, "tools": ["navigate_to"] * 3}
        )

        changed = {**state, "wateredCrops": 1}
        harness._update_stall_watchdog("water_crops", changed)
        self.assertEqual(0, harness.stalled_decisions)

        for _ in range(24):
            harness._update_stall_watchdog("click", changed)

        self.assertEqual("stalled", harness.stop_reason)
        self.assertIn(("focus", {}), harness.bridge.calls)
        self.assertIn(("set_display_mode", {"mode": "borderless"}), harness.bridge.calls)
        harness.telemetry.record.assert_any_call(
            "stall_recovery_attempted",
            {"count": 16, "focus": "completed", "display_mode": "completed"},
        )

    def test_budget_tripwire_counts_actor_and_director_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.telemetry = Telemetry(Path(directory) / "run")
            harness.budget_usd = 0.001
            harness.stop_reason = None
            decision = ToolDecision("press", {"buttons": ["W"]}, {"cost": 0.0006}, None)

            harness._record_decision("actor", decision, 1)
            self.assertIsNone(harness.stop_reason)
            harness._record_decision("director", decision, 1)

            self.assertEqual("budget_reached", harness.stop_reason)
            self.assertAlmostEqual(0.0012, harness.telemetry.summary["usage"]["cost"])

    def test_keyboard_sequence_executes_steps_and_stops_on_location_change(self) -> None:
        class TransitionBridge(_Bridge):
            def request(self, request_type, **arguments):
                self.calls.append((request_type, arguments))
                location = "Farm" if len(self.calls) == 2 else "FarmHouse"
                return {
                    "status": "completed",
                    "state": {
                        "location": location,
                        "menu": "none",
                        "pixelX": len(self.calls),
                        "pixelY": 2,
                    },
                }

        harness = object.__new__(AutoplayHarness)
        harness.bridge = TransitionBridge()
        harness.blocked_movements = set()
        harness.game_actions = 0
        harness.continuous = True
        harness.director_interval = 12
        harness.last_director_action_count = 0
        result = harness._execute_actor_tool(
            ToolDecision(
                "control_sequence",
                {
                    "steps": [
                        {"buttons": ["A"], "ticks": 15},
                        {"buttons": ["S"], "ticks": 15},
                        {"buttons": ["D4"], "ticks": 1},
                    ]
                },
                {},
                None,
            ),
            self.frame,
            {"location": "FarmHouse", "menu": "none", "pixelX": 0, "pixelY": 2},
        )

        self.assertEqual(2, result["sequence_steps_executed"])
        self.assertEqual("Farm", result["state"]["location"])
        self.assertEqual(2, harness.game_actions)
        self.assertEqual(0, harness.last_director_action_count)

    def test_dispatches_every_memory_actor_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = _Bridge()
            harness.blocked_movements = set()
            harness.game_actions = 0
            harness.stop_reason = None
            harness.continuous = False
            harness.director_interval = 12
            harness.last_director_action_count = 0
            harness.ledger = ObjectiveLedger(
                Path(directory) / "objectives.json", "Reach the farm", "location is Farm"
            )
            harness.wiki = _Wiki()
            harness.latest_wiki_results = []

            progress = harness._execute_actor_tool(
                ToolDecision("objective_progress", {"note": "Outside", "evidence": "location=Farm"}, {}, None),
                self.frame,
            )
            opportunity = harness._execute_actor_tool(
                ToolDecision("record_opportunity", {"note": "Forage", "reason": "Visible item"}, {}, None),
                self.frame,
            )
            wiki = harness._execute_actor_tool(
                ToolDecision("wiki_search", {"query": "Parsnip"}, {}, None), self.frame
            )
            stopped = harness._execute_actor_tool(
                ToolDecision("stop_session", {"reason": "Objective finished"}, {}, None), self.frame
            )

            self.assertEqual("recorded", progress["status"])
            self.assertEqual("recorded", opportunity["status"])
            self.assertEqual("Parsnip", wiki["results"][0]["title"])
            self.assertEqual("Objective finished", stopped["reason"])
            self.assertEqual(1, len(harness.ledger.snapshot()["progress"]))
            self.assertEqual(1, len(harness.ledger.snapshot()["opportunities"]))

    def test_stop_tool_requests_review_during_continuous_play(self) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        harness.blocked_movements = set()
        harness.game_actions = 7
        harness.stop_reason = None
        harness.continuous = True
        harness.director_interval = 12
        harness.last_director_action_count = 7

        result = harness._execute_actor_tool(
            ToolDecision("stop_session", {"reason": "Objective appears complete"}, {}, None), self.frame
        )

        self.assertEqual("review_requested", result["status"])
        self.assertIsNone(harness.stop_reason)
        self.assertLessEqual(harness.last_director_action_count, harness.game_actions - harness.director_interval)

    def test_continuous_director_cannot_block_and_churn_objective(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.continuous = True
            harness.ledger = ObjectiveLedger(
                Path(directory) / "objectives.json", "Reach the farm", "location is Farm"
            )
            harness.telemetry = Telemetry(Path(directory) / "run")

            harness._apply_director_decision(
                ToolDecision("block_objective", {"evidence": "Movement failed repeatedly"}, {}, None),
                {"location": "FarmHouse"},
            )

            self.assertIsNotNone(harness.ledger.snapshot()["active"])
            self.assertEqual([], harness.ledger.snapshot()["history"])

    def test_settles_non_movable_transition_before_next_decision(self) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        response = {
            "status": "completed",
            "state": {"worldReady": True, "canMove": False, "menu": "none", "location": "FarmHouse"},
        }
        settled = harness._settle_transition(response)
        self.assertEqual("wait", harness.bridge.calls[-1][0])
        self.assertEqual("completed", settled["status"])

    def test_applies_every_director_tool_and_rejects_unverifiable_goal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.continuous = False
            harness.ledger = ObjectiveLedger(
                Path(directory) / "objectives.json", "Reach the farm", "location is Farm"
            )
            harness.telemetry = Telemetry(Path(directory) / "run")

            harness._apply_director_decision(
                ToolDecision("continue_objective", {"milestone": "Use the door", "reason": "Still inside"}, {}, None),
                {"location": "FarmHouse"},
            )
            self.assertEqual("Use the door", harness.ledger.snapshot()["active"]["milestone"])
            harness.ledger.complete_objective("Harness verified location is Farm")
            self.assertIsNone(harness.ledger.snapshot()["active"])

            harness._apply_director_decision(
                ToolDecision(
                    "set_objective",
                    {"goal": "Stay here", "success_condition": "location is Farm", "milestone": "Observe"},
                    {},
                    None,
                ),
                {"location": "Farm"},
            )
            self.assertIsNone(harness.ledger.snapshot()["active"])
            self.assertIn("already satisfied", harness.director_feedback)
            harness._apply_director_decision(
                ToolDecision(
                    "set_objective",
                    {"goal": "Clear debris", "success_condition": "Clear enough debris", "milestone": "Use axe"},
                    {},
                    None,
                ),
                {"location": "Farm"},
            )
            self.assertIsNone(harness.ledger.snapshot()["active"])
            harness._apply_director_decision(
                ToolDecision(
                    "set_objective",
                    {"goal": "Go home", "success_condition": "location is FarmHouse", "milestone": "Enter house"},
                    {},
                    None,
                ),
                {"location": "Farm"},
            )
            harness._apply_director_decision(
                ToolDecision("block_objective", {"evidence": "Door is unavailable"}, {}, None),
                {"location": "Farm"},
            )
            self.assertIsNone(harness.ledger.snapshot()["active"])
            self.assertEqual("blocked", harness.ledger.snapshot()["history"][-1]["status"])

    def test_morning_planning_uses_restricted_tools_on_day_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.telemetry = Mock(step=0)
            harness.director_feedback = None
            harness.world = Mock()
            harness.world.summary.return_value = {"unvisited": ["Town"]}
            harness._context = Mock(return_value="context")
            harness._print_step = Mock()
            farm_plan = ToolDecision(
                "update_farm_plan",
                {"zones": [{"name": "Plot", "purpose": "crops", "x1": 1, "y1": 1, "x2": 5, "y2": 5}],
                 "notes": "Near the house"},
                {}, None,
            )
            agenda = [
                {"goal": "Visit Town", "success_condition": "location is Town", "slot": "morning", "category": "exploring"},
                {"goal": "Gather wood", "success_condition": "inventory.Wood >= 10", "slot": "midday", "category": "foraging"},
                {"goal": "Clear debris", "success_condition": "stamina <= 200", "slot": "afternoon", "category": "clearing"},
                {"goal": "Return home", "success_condition": "location is FarmHouse", "slot": "evening", "category": "home"},
                {"goal": "Organize tools", "success_condition": "toolbarIndex >= 0", "slot": "evening", "category": "home"},
            ]
            day_plan = ToolDecision("plan_day", {"theme": "Explore", "agenda": agenda, "dropped": []}, {}, None)
            harness._decide = Mock(side_effect=[(farm_plan, 1), (day_plan, 1)])
            state = {"day": 17, "location": "FarmHouse", "time": 600,
                     "farmLayout": {"width": 80, "height": 65}}

            harness._plan_day_if_needed(state, self.frame)

            self.assertEqual(17, harness.notebook.current_day)
            self.assertEqual(5, len(harness.notebook.remaining(17)))
            self.assertEqual([UPDATE_FARM_PLAN_TOOL], harness._decide.call_args_list[0].args[3])
            self.assertEqual([PLAN_DAY_TOOL], harness._decide.call_args_list[1].args[3])
            harness.telemetry.record.assert_any_call("day_planned", {"day": 17, "agenda_size": 5})

    def test_invalid_morning_agenda_produces_feedback_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.set_farm_plan(
                [{"name": "Plot", "purpose": "crops", "x1": 1, "y1": 1, "x2": 5, "y2": 5}],
                "Small plot", 1,
            )
            harness.telemetry = Mock(step=0)
            harness.director_feedback = None
            harness.world = Mock()
            harness.world.summary.return_value = {"unvisited": []}
            harness._context = Mock(return_value="context")
            harness._print_step = Mock()
            invalid = ToolDecision(
                "plan_day",
                {"theme": "Short", "agenda": [
                    {"goal": f"Goal {index}", "slot": "evening" if index == 3 else "morning",
                     "category": "farming" if index < 2 else "exploring"}
                    for index in range(4)
                ], "dropped": []}, {}, None,
            )
            valid = ToolDecision(
                "plan_day",
                {"theme": "Full", "agenda": [
                    {"goal": f"Goal {index}", "slot": "evening" if index == 4 else "morning",
                     "category": "farming" if index < 2 else "exploring"}
                    for index in range(5)
                ], "dropped": []}, {}, None,
            )
            harness._decide = Mock(side_effect=[(invalid, 1), (valid, 1)])

            harness._plan_day_if_needed(
                {"day": 2, "location": "FarmHouse", "time": 600,
                 "farmLayout": {"width": 80, "height": 65}},
                self.frame,
            )

            self.assertEqual(2, harness._decide.call_count)
            self.assertIsNone(harness.director_feedback)
            harness.telemetry.record.assert_any_call(
                "agenda_plan_rejected",
                {"attempt": 1, "mode": "morning", "errors": ["morning planning needs 5-8 new items"]},
            )

    def test_unknown_carried_ids_are_stripped_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.set_farm_plan(
                [{"name": "Plot", "purpose": "crops", "x1": 1, "y1": 1, "x2": 5, "y2": 5}],
                "Small plot", 1,
            )
            harness.telemetry = Mock(step=0)
            harness.director_feedback = None
            harness.world = Mock()
            harness.world.summary.return_value = {"unvisited": []}
            harness._context = Mock(return_value="context")
            harness._print_step = Mock()
            agenda = [
                {
                    "goal": f"Fresh goal {index}",
                    "slot": "evening" if index == 4 else "morning",
                    "category": "farming" if index < 2 else "exploring",
                    "carried_id": f"explore-backwoods-{index}",
                }
                for index in range(5)
            ]
            decision = ToolDecision(
                "plan_day", {"theme": "Fresh start", "agenda": agenda, "dropped": []}, {}, None
            )
            harness._decide = Mock(return_value=(decision, 1))

            harness._plan_day_if_needed(
                {"day": 2, "location": "FarmHouse", "time": 600,
                 "farmLayout": {"width": 80, "height": 65}},
                self.frame,
            )

            self.assertEqual(1, harness._decide.call_count)
            self.assertTrue(all("carried_id" not in item for item in agenda))
            self.assertEqual(5, len(harness.notebook.remaining(2)))
            harness.telemetry.record.assert_any_call(
                "ignored_carried_ids",
                {"ids": [f"explore-backwoods-{index}" for index in range(5)]},
            )
            self.assertFalse(any(
                call.args[0] == "agenda_plan_rejected"
                for call in harness.telemetry.record.call_args_list
            ))

    def test_invalid_agenda_is_accepted_with_gaps_after_two_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.set_farm_plan(
                [{"name": "Plot", "purpose": "crops", "x1": 1, "y1": 1, "x2": 5, "y2": 5}],
                "Small plot", 1,
            )
            harness.telemetry = Mock(step=0)
            harness.director_feedback = None
            harness.world = Mock()
            harness.world.summary.return_value = {"unvisited": []}
            harness._context = Mock(return_value="context")
            harness._print_step = Mock()
            invalid = ToolDecision(
                "plan_day",
                {"theme": "Short", "agenda": [
                    {"goal": f"Goal {index}", "slot": "evening" if index == 3 else "morning",
                     "category": "farming" if index < 2 else "exploring"}
                    for index in range(4)
                ], "dropped": []}, {}, None,
            )
            harness._decide = Mock(return_value=(invalid, 1))

            harness._plan_day_if_needed(
                {"day": 3, "location": "FarmHouse", "time": 600,
                 "farmLayout": {"width": 80, "height": 65}},
                self.frame,
            )

            self.assertEqual(2, harness._decide.call_count)
            self.assertEqual(4, len(harness.notebook.remaining(3)))
            harness.telemetry.record.assert_any_call(
                "agenda_accepted_with_gaps",
                {"mode": "morning", "errors": ["morning planning needs 5-8 new items"]},
            )

    def test_refill_needs_no_evening_item_and_rejects_early_home_only_goal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.start_day(2)
            harness.world = Mock()
            refill = {"agenda": [
                {"goal": "Gather wood", "success_condition": "inventory.Wood >= 10", "slot": "morning", "category": "foraging"},
                {"goal": "Visit Town", "success_condition": "location is Town", "slot": "midday", "category": "exploring"},
            ], "dropped": []}

            self.assertEqual([], harness._agenda_errors(refill, {"day": 2, "time": 1200}, "refill"))

            refill["agenda"][0] = {
                "goal": "Return home", "success_condition": "location is FarmHouse, playerFree is true",
                "slot": "morning", "category": "home",
            }
            self.assertEqual(
                ["return home is not an agenda item before the evening slot"],
                harness._agenda_errors(refill, {"day": 2, "time": 1200}, "refill"),
            )
            refill["agenda"][0]["slot"] = "evening"
            self.assertEqual([], harness._agenda_errors(refill, {"day": 2, "time": 1200}, "refill"))

    def test_go_to_location_rejects_closed_door_without_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = _Bridge()
            harness.world = WorldMap(Path(directory))
            harness.world.edges = [{"from": "Town", "to": "SeedShop", "kind": "action_warp",
                                    "openTime": 900, "closeTime": 2100}]
            harness.game_actions = 0
            harness.blocked_movements = set()

            result = harness._execute_actor_tool(
                ToolDecision("go_to_location", {"location": "SeedShop"}, {}, None),
                self.frame,
                {"location": "Town", "day": 7, "time": 850},
            )

            self.assertEqual({"status": "rejected", "reason": "door_closed_until_900",
                              "openTime": 900, "closeTime": 2100}, result)
            self.assertEqual([], harness.bridge.calls)

    def test_go_to_location_records_no_walkable_path_for_current_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = Mock()
            harness.bridge.request.return_value = {
                "status": "blocked",
                "error": "no_walkable_path",
                "state": {"location": "Farm", "day": 7},
            }
            harness.world = WorldMap(Path(directory))
            harness.game_actions = 0
            harness.blocked_movements = set()

            result = harness._execute_actor_tool(
                ToolDecision("go_to_location", {"location": "Backwoods"}, {}, None),
                self.frame,
                {"location": "Farm", "day": 7},
            )

            self.assertEqual("no_walkable_path", result["error"])
            self.assertEqual(
                [{"from": "Farm", "to": "Backwoods", "day": 7}],
                harness.world.blocked_paths,
            )

    def test_go_to_location_success_clears_stale_blocked_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = Mock()
            harness.bridge.request.return_value = {
                "status": "completed",
                "state": {"location": "Backwoods", "day": 7, "worldReady": True,
                          "canMove": True, "menu": "none"},
            }
            harness.world = WorldMap(Path(directory))
            harness.world.record_blocked_path("Farm", "Backwoods", 7)
            harness.game_actions = 0
            harness.blocked_movements = set()

            result = harness._execute_actor_tool(
                ToolDecision("go_to_location", {"location": "Backwoods"}, {}, None),
                self.frame,
                {"location": "Farm", "day": 7, "time": 1200},
            )

            self.assertEqual("completed", result["status"])
            self.assertEqual([], harness.world.blocked_paths)

    @patch("autoplay_harness.runner.clear_debris")
    def test_clear_debris_dispatch_uses_per_target_continuous_budget(self, debris_skill) -> None:
        harness = object.__new__(AutoplayHarness)
        harness.bridge = _Bridge()
        harness.game_actions = 0
        harness.continuous = True
        debris_skill.return_value = {"status": "completed", "controls_executed": 3}
        state = {"location": "Farm"}
        targets = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]

        result = harness._execute_actor_tool(
            ToolDecision("clear_debris", {"targets": targets}, {}, None), self.frame, state
        )

        self.assertEqual("completed", result["status"])
        debris_skill.assert_called_once_with(harness.bridge, state, targets, 32)
        self.assertEqual(3, harness.game_actions)

    def test_objective_completion_marks_linked_agenda_item_done(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.start_day(3)
            harness.notebook.set_agenda(
                3,
                [{"goal": "Earn money", "success_condition": "money >= 10", "slot": "morning", "category": "shopping"}],
                "Progress",
            )
            agenda_id = harness.notebook.remaining(3)[0]["id"]
            harness.notebook.mark(agenda_id, "active")
            harness.ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Bootstrap", "money >= 1")
            harness.ledger.complete_objective("done")
            harness.ledger.set_objective("Earn money", "money >= 10", "Sell something", agenda_id)
            harness.telemetry = Mock(step=0)

            self.assertTrue(harness._complete_verified_objective({"day": 3, "money": 10}))

            item = harness.notebook.data["days"]["3"]["agenda"][0]
            self.assertEqual("done", item["status"])

    def test_bedtime_reflects_before_sleep_and_carries_remaining_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = _Bridge()
            harness.notebook = Notebook(Path(directory))
            harness.notebook.start_day(4)
            harness.notebook.set_agenda(
                4, [{"goal": "Late task", "slot": "evening", "category": "home"}], "Long day"
            )
            harness.telemetry = Mock(step=0)
            harness._context = Mock(return_value="context")
            harness._print_step = Mock()
            harness._decide = Mock(return_value=(
                ToolDecision("reflect", {"summary": "Worked hard", "learned": ["Town closes late"]}, {}, None),
                1,
            ))
            harness.game_actions = 0
            harness.continuous = True
            order = []

            def sleep(_bridge, _state, _actions):
                order.append("sleep")
                return {"status": "completed", "controls_executed": 1}

            original_decide = harness._decide
            harness._decide = Mock(side_effect=lambda *args: (order.append("reflect"), original_decide(*args))[1])
            with patch("autoplay_harness.runner.go_home_and_sleep", side_effect=sleep):
                result = harness._execute_actor_tool(
                    ToolDecision("go_home_and_sleep", {}, {}, None), self.frame,
                    {"day": 4, "time": 2100, "stamina": 100, "health": 100},
                )

            self.assertEqual("completed", result["status"])
            self.assertEqual(["reflect", "sleep"], order)
            self.assertEqual([REFLECT_TOOL], harness._decide.call_args.args[3])
            self.assertEqual("Worked hard", harness.notebook.data["days"]["4"]["reflection"])
            self.assertEqual("carried", harness.notebook.data["days"]["4"]["agenda"][0]["status"])

    @patch("autoplay_harness.runner.till_tiles")
    def test_till_tiles_outside_crop_zone_is_rejected_without_input(self, till_skill) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.bridge = _Bridge()
            harness.notebook = Notebook(Path(directory))
            harness.notebook.set_farm_plan(
                [{"name": "Plot", "purpose": "crops", "x1": 5, "y1": 5, "x2": 8, "y2": 8}],
                "One plot", 5,
            )
            harness.game_actions = 0
            harness.continuous = True

            result = harness._execute_actor_tool(
                ToolDecision("till_tiles", {"tiles": [{"x": 9, "y": 8}]}, {}, None),
                self.frame,
                {"location": "Farm"},
            )

            self.assertEqual("rejected", result["status"])
            self.assertEqual("outside_crop_zone", result["reason"])
            self.assertEqual([], harness.bridge.calls)
            till_skill.assert_not_called()


class LongRunIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.harness = AutoplayHarness.__new__(AutoplayHarness)
        harness = self.harness
        harness.notebook = Notebook(root)
        harness.notebook.start_day(9)
        harness.ledger = ObjectiveLedger(root / "objectives.json", "Gather wood", "inventory.Wood >= 50")
        harness.world = WorldMap(root)
        harness.telemetry = Telemetry(root / "run")
        harness.latest_wiki_results = []
        harness.blocked_movements = set()
        harness.last_result = {"tool": "navigate_to", "status": "blocked", "reason": "no_walkable_path"}
        harness.director_feedback = None
        harness.continuous = True
        harness.game_actions = harness.decisions = harness.stalled_decisions = 0
        harness.director_interval = 12
        harness.last_action_fingerprint = harness.last_progress_fingerprint = None
        harness.recent_actor_tools = []
        harness.stall_review_requested = False
        harness.stop_reason = None
        harness.inspect_next_scene = False
        harness.actor_mode = "state-first"
        harness._print_step = Mock()
        self.frame = Frame("frame-1", 1920, 1080, "image", None)
        self.state = {"day": 9, "time": 800, "location": "Town", "worldReady": True,
                      "playerFree": True, "canMove": True, "menu": "none", "eventUp": False,
                      "minigame": "none", "stamina": 100, "health": 100, "inventoryCounts": {"Wood": 1}}

    def test_actor_can_interrupt_and_resume_work_without_director_or_game_input(self):
        harness = self.harness
        harness.bridge = _Bridge()
        harness.notebook.set_agenda(9, [{"goal": "Gather wood", "success_condition": "inventory.Wood >= 50",
                                        "slot": "morning", "category": "clearing"}], "A flexible morning")
        harness.notebook.mark("d9-1", "active")
        harness.ledger.data["active"]["agenda_id"] = "d9-1"
        pending_experience = {"subject": "Bench", "status": "completed"}
        harness.last_interaction = pending_experience
        harness._observe = Mock(return_value=(dict(self.state), self.frame))
        arguments = {"goal": "Follow the sound by the lake", "success_condition": "location is Mountain",
                     "milestone": "Walk to the lake", "reason": "Something caught my attention",
                     "say": "I wonder what is happening by the water."}
        harness._decide = Mock(return_value=(ToolDecision("change_objective", arguments, {}, None), 1))
        harness._actor_step()
        harness._decide.assert_called_once()
        self.assertIn("change_objective", [tool["function"]["name"] for tool in harness._decide.call_args.args[3]])
        self.assertEqual("Follow the sound by the lake", harness.ledger.snapshot()["active"]["goal"])
        self.assertEqual("interrupted", harness.ledger.snapshot()["history"][-1]["status"])
        self.assertEqual("pending", Notebook(harness.notebook.path.parent).remaining(9)[0]["status"])
        self.assertIs(pending_experience, harness.last_interaction)
        self.assertEqual([], harness.bridge.calls)
        self.assertEqual(0, harness.game_actions)
        result = harness._execute_actor_tool(ToolDecision("change_objective", {
            "goal": "Gather wood", "success_condition": "inventory.Wood >= 50", "milestone": "Find fallen branches",
            "reason": "I want to return to my wood gathering", "agenda_id": "d9-1"}, {}, None), self.frame, self.state)
        self.assertEqual("completed", result["status"])
        self.assertEqual("d9-1", harness.notebook.active_item(9)["id"])
        self.assertEqual(["interrupted", "interrupted"], [item["status"] for item in harness.ledger.snapshot()["history"]])

    def test_actor_objective_change_cannot_erase_current_work_on_invalid_proposal(self):
        harness = self.harness
        arguments = {"goal": "A new idea", "success_condition": "location is Town", "milestone": "Try it", "reason": "Curiosity"}
        for patch_arguments in ({}, {"success_condition": "friendship >= 10"},
                                {"success_condition": "inventory.Wood >= 50"},
                                {"success_condition": "location is Mountain", "agenda_id": "missing"}):
            with self.subTest(patch_arguments=patch_arguments):
                result = harness._change_actor_objective({**arguments, **patch_arguments}, self.state)
                self.assertEqual("rejected", result["status"])
                self.assertEqual("Gather wood", harness.ledger.snapshot()["active"]["goal"])
                self.assertEqual([], harness.ledger.data["history"])

    def test_self_chosen_goal_retry_deferral_cannot_be_bypassed_without_an_agenda_item(self):
        harness = self.harness
        decision = ToolDecision("clear_debris", {"targets": [{"x": 2, "y": 3}]}, {}, None)
        for _ in range(3):
            harness._track_action_retries(decision, {"status": "blocked", "reason": "no_passable_tile_next_to_target"}, self.state, self.state)
        self.assertIsNone(harness.ledger.snapshot()["active"])
        harness.ledger.set_objective("Visit the lake", "location is Mountain", "Walk north")
        arguments = {"goal": "Get some wood", "success_condition": "inventory.Wood >= 50", "milestone": "Try again", "reason": "Another idea"}
        self.assertEqual("rejected", harness._change_actor_objective(arguments, self.state)["status"])
        self.assertTrue(harness._target_deferred_today(harness._retry_condition_key(arguments["success_condition"]), self.state))
        self.assertEqual("completed", harness._change_actor_objective(arguments, {**self.state, "day": 10})["status"])

    def test_repeated_rejection_merges_lesson_in_actor_path(self):
        harness = self.harness
        harness.bridge = _Bridge()
        harness.world.edges = [{"from": "Town", "to": "SeedShop", "kind": "action_warp", "openTime": 900, "closeTime": 2100}]
        harness._observe = lambda: (dict(self.state), self.frame)
        harness._decide = Mock(return_value=(ToolDecision("go_to_location", {"location": "SeedShop", "say": "I check the shop."}, {}, None), 1))
        harness._actor_step()
        self.state["time"] = 810
        harness._actor_step()
        lessons = harness.notebook.data["lessons"]
        self.assertEqual(1, len(lessons))
        self.assertEqual("rejected:door_closed_until_900", lessons[0]["key"])
        self.assertEqual(2, lessons[0]["count"])
        self.assertIn("SeedShop is closed until 9:00 AM", lessons[0]["text"])
        self.assertEqual([], harness.bridge.calls)

    def test_actor_remembers_a_real_encounter_once_and_both_roles_recall_it(self):
        harness = self.harness
        harness.bridge = _Bridge()
        after = {**self.state, "dialogueText": "Good morning, farmer!"}
        harness._observe = Mock(side_effect=[(self.state, self.frame), (self.state, self.frame), (after, self.frame)])
        memory = ToolDecision("remember_interaction", {
            "subject": "Jas", "interaction": "Talk", "outcome": "possible", "preference": "liked",
            "note": "She greeted me warmly. I enjoy talking to her.", "say": "That greeting brightens my morning."}, {}, None)
        harness._decide = Mock(side_effect=[(ToolDecision("press", {"buttons": ["X"], "say": "I say hello."}, {}, None), 1), (memory, 1)])
        execute = harness._execute_actor_tool
        harness._execute_actor_tool = lambda decision, frame, state: (
            {"status": "completed", "state": after} if decision.name == "press" else execute(decision, frame, state))
        harness._actor_step()
        self.assertIn("dialogueText", harness.last_interaction["changes"])
        self.assertEqual("Good morning, farmer!", harness.last_interaction["dialogue"])
        harness._actor_step()
        self.assertEqual(3, harness._observe.call_count)
        self.assertEqual([], harness.bridge.calls)
        self.assertIsNone(harness.last_interaction)
        duplicate = execute(memory, self.frame, after)
        self.assertEqual("rejected", duplicate["status"])
        self.assertEqual(1, len(harness.notebook.data["interactions"]))
        harness.notebook = Notebook(harness.notebook.path.parent)
        for role in ("actor", "director"):
            context = json.loads(harness._context(role, after, self.frame, state_only=role == "actor"))
            self.assertEqual("liked", context["notebook"]["interactions"][0]["preference"])
            self.assertEqual("Jas", context["notebook"]["interactions"][0]["subject"])

    def test_interaction_memory_requires_an_attempt_and_cannot_relabel_a_failure(self):
        harness = self.harness
        harness.bridge = _Bridge()
        arguments = {"subject": "Bench", "interaction": "Sit", "outcome": "possible", "preference": "liked", "note": "It was restful."}
        decision = ToolDecision("remember_interaction", arguments, {}, None)
        self.assertEqual("rejected", harness._execute_actor_tool(decision, self.frame, self.state)["status"])
        harness.last_interaction = {"location": "Town", "day": 9, "tool": "navigate_to", "status": "blocked"}
        result = harness._execute_actor_tool(decision, self.frame, self.state)
        self.assertEqual("rejected", result["status"])
        self.assertEqual([], harness.notebook.data["interactions"])
        self.assertEqual([], harness.bridge.calls)

    def test_speech_does_not_change_action_identity(self):
        first = ToolDecision("navigate_to", {"tile_x": 1, "tile_y": 2, "say": "I try the path."}, {}, None)
        repeated = ToolDecision("navigate_to", {"tile_x": 1, "tile_y": 2, "say": "I try it again."}, {}, None)
        self.assertEqual(AutoplayHarness._action_fingerprint(first, self.state),
                         AutoplayHarness._action_fingerprint(repeated, self.state))

    def test_variety_feedback_retries_without_changing_existing_rules(self):
        harness = self.harness
        old_items = [{"goal": "Old task", "slot": "morning", "category": category}
                     for category in ("farming", "farming", "farming", "clearing", "clearing")]
        harness.notebook.set_agenda(9, old_items, "Farm day")
        for item in harness.notebook.remaining(9):
            harness.notebook.mark(item["id"], "done")
        harness.notebook.start_day(10)
        items = [{"goal": "Fresh task", "slot": "evening", "category": category}
                 for category in ("farming", "farming", "farming", "exploring", "social")]
        invalid = ToolDecision("plan_day", {"theme": " farm DAY ", "agenda": items, "dropped": []}, {}, None)
        valid = ToolDecision("plan_day", {"theme": "Town friends", "agenda": items, "dropped": []}, {}, None)
        harness._decide = Mock(side_effect=[(invalid, 1), (valid, 1)])
        harness._request_agenda({**self.state, "day": 10}, self.frame, "morning")
        events = [json.loads(line) for line in harness.telemetry.events_path.read_text().splitlines()]
        rejected = next(event for event in events if event["type"] == "agenda_plan_rejected")
        self.assertIn("theme", " ".join(rejected["errors"]))
        self.assertEqual("Town friends", harness.notebook.data["days"]["10"]["theme"])
        self.assertIsNone(harness.director_feedback)

    def test_weekly_rollup_preserves_recent_theme_validation(self):
        notebook = self.harness.notebook
        notebook.data["days"] = {
            str(day): {"theme": f"Theme {day}", "agenda": [], "reflection": None, "learned": []}
            for day in range(1, 9)
        }
        notebook.current_day = 8
        notebook.rollup_week()
        notebook.start_day(9)
        self.assertEqual(["8", "9"], list(notebook.data["days"]))
        self.assertEqual(["Theme 6", "Theme 7", "Theme 8"], notebook.recent_themes(3))
        self.assertTrue(any("theme" in error for error in notebook.variety_errors(9, [], "Theme 6", "morning")))

    def test_timeout_blocked_path_stall_reflection_and_wasted_objective_lessons(self):
        harness = self.harness
        harness.world.record_blocked_path("Farm", "Backwoods", 9)
        harness._record_actor_lessons(ToolDecision("travel_to", {}, {}, None),
                                     {"status": "blocked", "reason": "hop_failed:tick_budget_exhausted"}, self.state, [])
        harness.last_progress_fingerprint = harness._progress_fingerprint(self.state)
        for _ in range(8):
            harness._update_stall_watchdog("navigate_to", self.state)
        harness._apply_director_decision(ToolDecision("reflect", {
            "summary": "The path was blocked.", "learned": [], "lessons": ["Try another path tomorrow."]}, {}, None), self.state)
        harness.continuous = False
        harness.objective_decision_id = harness.ledger.snapshot()["active"]["id"]
        harness.objective_decisions = 8
        harness._apply_director_decision(ToolDecision("block_objective", {"evidence": "No accessible wood"}, {}, None), self.state)
        keys = [lesson["key"] for lesson in harness.notebook.data["lessons"]]
        for prefix in ("timeout:travel_to", "blocked_path:Farm->Backwoods", "stall:", "reflection:", "wasted_decisions:Gather wood"):
            self.assertTrue(any(key.startswith(prefix) for key in keys), keys)

    def test_context_budgets_trim_in_order_without_mutating_state_or_notebook(self):
        harness = self.harness
        harness.latest_wiki_results = [{"text": "wiki " * 8000}]
        for index in range(12):
            harness.telemetry.record("tool_result", {"tool": "navigate_to",
                                     "result": {"status": "blocked", "reason": str(index) + "e" * 350}})
        for index in range(6):
            harness.notebook.add_lesson(str(index), "lesson " * 25, "auto", 9)
        harness.notebook.set_agenda(9, [{"goal": "goal " * 75, "slot": "morning", "category": "farming"} for _ in range(8)], "Big plan")
        state = {**self.state,
                 "nearbyObjects": [{"x": i, "y": 1, "name": "object " * 20} for i in range(100)],
                 "cropsNearby": [{"x": i, "y": 1, "crop": "crop " * 40} for i in range(200)]}
        original = json.dumps(harness.notebook.data, sort_keys=True)
        sizes = {}
        for role, state_only, budget in (("actor", True, ACTOR_CONTEXT_BUDGET), ("director", False, DIRECTOR_CONTEXT_BUDGET)):
            with self.subTest(role=role):
                context = harness._context(role, state, self.frame, state_only=state_only)
                sizes[role] = round(len(context) / 3.5, 1)
                self.assertLessEqual(len(context) / 3.5, budget)
                packet = json.loads(context)
                self.assertEqual("Gather wood", packet["objective_ledger"]["active"]["goal"])
                self.assertEqual(harness.last_result, packet["game_state"]["harnessLastResult"])
                event = json.loads(harness.telemetry.events_path.read_text().splitlines()[-1])
                expected = ["wiki_results", "memory_recent"]
                if role == "director":
                    expected.append("lessons")
                expected.append("agenda_goals")
                if role == "actor":
                    expected.append("nearbyObjects")
                expected.append("cropsNearby")
                self.assertEqual(expected, event["cuts"])
        self.assertEqual(original, json.dumps(harness.notebook.data, sort_keys=True))
        self.assertEqual(100, len(state["nearbyObjects"]))
        self.assertEqual(200, len(state["cropsNearby"]))
        print("Estimated context tokens:", sizes)

    def test_action_rechecks_scene_after_model_delay(self):
        harness = self.harness
        after = {**self.state, "location": "FarmHouse", "day": 10}
        harness._observe = Mock(side_effect=[(self.state, self.frame), (after, self.frame)])
        harness._decide = Mock(return_value=(ToolDecision("navigate_to", {"tile_x": 1, "tile_y": 2}, {}, None), 5000))
        harness._execute_actor_tool = Mock()
        harness._actor_step()
        harness._execute_actor_tool.assert_not_called()
        self.assertEqual("scene_changed_while_thinking", harness.last_result["reason"])
        self.assertTrue(harness.inspect_next_scene)
        self.assertIsNotNone(harness.ledger.snapshot()["active"])

    def test_clock_advance_is_allowed_but_moving_pointer_targets_are_not(self):
        decision = ToolDecision("navigate_to", {}, {}, None)
        self.assertFalse(self.harness._decision_scene_changed(decision, self.state, {**self.state, "time": 810}))
        click = ToolDecision("click", {}, {}, None)
        self.assertTrue(self.harness._decision_scene_changed(click, self.state, {**self.state, "npcsNearby": [{"x": 2, "y": 3}]}))

    def test_retry_limit_defers_same_destination_across_tools_and_speech(self):
        harness = self.harness
        harness.notebook.set_agenda(9, [
            {"goal": "Visit Desert", "success_condition": "location is Desert", "slot": "morning", "category": "exploring"},
            {"goal": "Visit Farm", "success_condition": "location is Farm", "slot": "midday", "category": "farming"},
        ], "Travel")
        harness.ledger.data["active"].update(goal="Visit Desert", success_condition="location is Desert", agenda_id="d9-1")
        harness.notebook.mark("d9-1", "active")
        harness._observe = lambda: (dict(self.state), self.frame)
        harness._execute_actor_tool = Mock(side_effect=lambda decision, *args: (
            {"status": "visual_review_requested"} if decision.name == "inspect_scene" else
            {"status": "blocked", "reason": "no_exit_to_location", "state": dict(self.state)}))
        decisions = [
            ToolDecision("travel_to", {"destination": "Desert", "say": "First try."}, {}, None),
            ToolDecision("inspect_scene", {"say": "I look."}, {}, None),
            ToolDecision("go_to_location", {"location": "Desert", "say": "Another try."}, {}, None),
            ToolDecision("inspect_scene", {"say": "A fresh look."}, {}, None),
            ToolDecision("travel_to", {"destination": "Desert", "say": "Third try."}, {}, None),
        ]
        harness._decide = Mock(side_effect=[(decision, 1) for decision in decisions])
        for _ in decisions:
            harness._actor_step()
        self.assertIsNone(harness.ledger.snapshot()["active"])
        self.assertEqual("blocked", harness.ledger.data["history"][-1]["status"])
        self.assertEqual("deferred", harness.notebook.data["days"]["9"]["agenda"][0]["status"])
        self.assertEqual(["d9-2"], [item["id"] for item in harness.notebook.remaining(9)])
        self.assertTrue(harness.stall_review_requested)
        self.assertIsNone(harness.stop_reason)
        event = json.loads(harness.telemetry.events_path.read_text().splitlines()[-1])
        self.assertEqual("objective_deferred", event["type"])
        self.assertEqual(3, event["failures"])
        harness._apply_director_decision(ToolDecision("set_objective", {
            "goal": "Try Desert again", "success_condition": "playerFree = true, location == desert",
            "milestone": "Travel", "agenda_id": "d9-2"}, {}, None), self.state)
        self.assertIsNone(harness.ledger.snapshot()["active"])
        self.assertIn("retry limit", harness.director_feedback)
        harness._apply_director_decision(ToolDecision("set_objective", {
            "goal": "Visit Farm", "success_condition": "location is Farm",
            "milestone": "Travel to Farm", "agenda_id": "d9-2"}, {}, None), self.state)
        self.assertEqual("Visit Farm", harness.ledger.snapshot()["active"]["goal"])

    def test_six_no_progress_decisions_ignore_clock_changes(self):
        for index in range(6):
            self.harness._track_action_retries(
                ToolDecision("navigate_to", {"tile_x": index, "tile_y": 2}, {}, None),
                {"status": "blocked", "reason": "no_walkable_path"}, self.state, {**self.state, "time": 810})
        self.assertIsNone(self.harness.ledger.snapshot()["active"])
        event = json.loads(self.harness.telemetry.events_path.read_text().splitlines()[-1])
        self.assertEqual(6, event["no_progress_decisions"])

    def test_invalid_objective_feedback_survives_agenda_reminder_and_stops_at_three(self):
        harness = self.harness
        harness.notebook.set_agenda(9, [{"goal": "Visit Farm", "slot": "morning", "category": "farming"}], "Farm")
        decision = ToolDecision("set_objective", {"goal": "Already in Town", "success_condition": "location is Town",
                                                 "milestone": "Arrive", "agenda_id": "d9-1"}, {}, None)
        for attempt in range(3):
            harness._apply_director_decision(decision, self.state)
            feedback = harness.director_feedback
            harness._agenda_feedback(self.state)
            self.assertIn("already satisfied", feedback)
            self.assertEqual(feedback, harness.director_feedback)
            self.assertEqual("director_repeated_invalid_objective" if attempt == 2 else None, harness.stop_reason)

    def test_bedtime_can_reopen_deferred_home_but_stops_if_route_stays_blocked(self):
        harness = self.harness
        harness.notebook.set_agenda(9, [{"goal": "Go home", "success_condition": "location is FarmHouse",
                                      "slot": "evening", "category": "home"}], "Return")
        harness.notebook.mark("d9-1", "deferred")
        harness.ledger.block_objective("Earlier path blocked")
        decision = ToolDecision("set_objective", {"goal": "Go home", "success_condition": "location is FarmHouse",
                                                 "milestone": "Arrive safely", "agenda_id": "d9-1"}, {}, None)
        harness._apply_director_decision(decision, self.state)
        self.assertIsNone(harness.ledger.snapshot()["active"])
        self.assertIn("retry limit", harness.director_feedback)
        night = {**self.state, "time": 2200}
        harness._apply_director_decision(decision, night)
        self.assertEqual("active", harness.notebook.remaining(9)[0]["status"])
        self.assertEqual({}, harness.invalid_objective_attempts)
        for _ in range(3):
            harness._track_action_retries(ToolDecision("travel_to", {"destination": "FarmHouse"}, {}, None),
                                         {"status": "blocked", "reason": "no_walkable_path"}, night, night)
        self.assertEqual("home_route_blocked", harness.stop_reason)
        self.assertIsNotNone(harness.ledger.snapshot()["active"])
        self.assertEqual("active", harness.notebook.remaining(9)[0]["status"])

    def test_advancing_story_dialogue_is_progress_for_both_retry_guards(self):
        harness = self.harness
        before = {**self.state, "menu": "DialogueBox:question=False:selected=-1:responses=0",
                  "eventUp": True, "playerFree": False, "canMove": False, "dialogueText": "Why is it locked?"}
        harness.last_progress_fingerprint = harness._progress_fingerprint(before)
        for line in ["I think Gunther has the key.", "Professor Gunther?", "I saw a big rusty old key.",
                     "A creepy sewer door...", "There's something moving around in there!", "Let's go!", ""]:
            after = {**before, "dialogueText": line}
            harness._track_action_retries(ToolDecision("press", {"buttons": ["X"]}, {}, None),
                                         {"status": "completed"}, before, after)
            harness._update_stall_watchdog("press", after)
            before = after
        self.assertIsNotNone(harness.ledger.snapshot()["active"])
        self.assertEqual(0, harness.retry_no_progress)
        self.assertEqual(0, harness.stalled_decisions)

    def test_retry_limit_allows_deliberate_waits_and_real_tool_progress(self):
        harness = self.harness
        for _ in range(8):
            harness._track_action_retries(ToolDecision("idle", {}, {}, None), {"status": "completed"}, self.state, self.state)
            harness._track_action_retries(ToolDecision("clear_debris", {"targets": [{"x": 1, "y": 2}]}, {}, None),
                                          {"status": "blocked", "reason": "action_budget_reached"},
                                          self.state, {**self.state, "stamina": 90})
        self.assertIsNotNone(harness.ledger.snapshot()["active"])
        self.assertEqual(0, harness.retry_no_progress)
        self.assertEqual({}, harness.failed_targets)

    def test_live_visual_context_fits_after_actor_geometry_trim(self):
        packet = json.loads((Path(__file__).parent / "fixtures" / "visual-context-overflow.json").read_text(encoding="utf-8"))
        ledger = json.dumps(packet["objective_ledger"], sort_keys=True)
        last_result = packet["game_state"]["harnessLastResult"]
        director = json.loads(self.harness._budget_context(json.loads(json.dumps(packet)), "director"))
        self.assertIn("farmLayout", director["game_state"])
        context = self.harness._budget_context(packet, "actor")
        self.assertLessEqual(len(context) / 3.5, ACTOR_CONTEXT_BUDGET)
        trimmed = json.loads(context)
        self.assertEqual(ledger, json.dumps(trimmed["objective_ledger"], sort_keys=True))
        self.assertEqual(last_result, trimmed["game_state"]["harnessLastResult"])
        self.assertNotIn("farmLayout", trimmed["game_state"])
        event = json.loads(self.harness.telemetry.events_path.read_text().splitlines()[-1])
        self.assertEqual("controller_geometry", event["cuts"][-1])

    def test_reconstructed_director_overflow_retains_plan_and_objective(self):
        # Day 18 fatal context reconstructed from observations, notebook, and recent decisions.
        packet = json.loads((Path(__file__).parent / "fixtures" / "director-context-overflow.json").read_text(encoding="utf-8"))
        ledger = packet["objective_ledger"]
        last_result = packet["game_state"]["harnessLastResult"]
        layout = packet["game_state"]["farmLayout"]
        context = self.harness._budget_context(packet, "director")
        self.assertLessEqual(len(context) / 3.5, DIRECTOR_CONTEXT_BUDGET)
        trimmed = json.loads(context)
        self.assertEqual(ledger, trimmed["objective_ledger"])
        self.assertEqual(last_result, trimmed["game_state"]["harnessLastResult"])
        self.assertEqual(layout, trimmed["game_state"]["farmLayout"])
        self.assertNotIn("navigationRows", trimmed["game_state"])
        event = json.loads(self.harness.telemetry.events_path.read_text().splitlines()[-1])
        self.assertEqual("controller_geometry", event["cuts"][-1])

    def test_interaction_memories_fit_incident_context_caps_without_losing_persisted_entries(self):
        harness = self.harness
        observed = {"location": "Town", "day": 9, "tool": "press", "status": "completed"}
        for index in range(10):
            harness.notebook.remember_interaction(f"Object {index}", "Inspect", "possible", "neutral", "n" * 180, observed)
        original = json.dumps(harness.notebook.data, sort_keys=True)
        for role, fixture, budget in (("actor", "visual-context-overflow.json", ACTOR_CONTEXT_BUDGET),
                                      ("director", "director-context-overflow.json", DIRECTOR_CONTEXT_BUDGET)):
            packet = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text(encoding="utf-8"))
            ledger = json.dumps(packet["objective_ledger"], sort_keys=True)
            last_result = packet["game_state"]["harnessLastResult"]
            packet["notebook"]["interactions"] = harness.notebook.interaction_context(self.state, 4 if role == "actor" else 6)
            if role == "actor":
                packet["recent_interaction"] = {**observed, "reason": "r" * 180, "dialogue": "d" * 180}
            context = harness._budget_context(packet, role)
            self.assertLessEqual(len(context) / 3.5, budget)
            result = json.loads(context)
            self.assertEqual(ledger, json.dumps(result["objective_ledger"], sort_keys=True))
            self.assertEqual(last_result, result["game_state"]["harnessLastResult"])
        self.assertEqual(original, json.dumps(harness.notebook.data, sort_keys=True))

    def test_oversized_protected_context_is_not_sent_or_dropped(self):
        harness = self.harness
        active = harness.ledger.data["active"]
        active["goal"] = "protected " * 5000
        with self.assertRaisesRegex(HarnessError, "protected fields retained"):
            harness._context("actor", self.state, self.frame)
        self.assertEqual("protected " * 5000, active["goal"])

    def test_consecutive_requests_share_stable_ledger_notebook_world_bytes(self):
        harness = self.harness
        harness.client = Mock()
        harness.client.choose_tool.return_value = ToolDecision("inspect_scene", {"say": "I look around."}, {}, None)
        for role, tools in (("actor", STATE_ACTOR_TOOLS), ("actor", ACTOR_TOOLS), ("director", DIRECTOR_TOOLS)):
            with self.subTest(role=role, tools=len(tools)):
                harness.client.reset_mock()
                for clock_time in (800, 810):
                    context = harness._context(role, {**self.state, "time": clock_time}, self.frame,
                                               state_only=tools is STATE_ACTOR_TOOLS)
                    harness._choose(STATE_ACTOR_PROMPT, context, self.frame, tools, role)
                first, second = harness.client.choose_tool.call_args_list
                self.assertEqual(first.kwargs["stable_context"], second.kwargs["stable_context"])
                blocks = first.kwargs["stable_context"].splitlines()
                self.assertEqual(["objective_ledger", "notebook", "world"], [next(iter(json.loads(block))) for block in blocks])
                self.assertNotEqual(first.args[1], second.args[1])
                self.assertNotIn("objective_ledger", json.loads(first.args[1]))


if __name__ == "__main__":
    unittest.main()
