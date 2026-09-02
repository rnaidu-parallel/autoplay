import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.capture import Frame
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from autoplay_harness.runner import AutoplayHarness, HarnessError
from autoplay_harness.telemetry import Telemetry
from autoplay_harness.tools import PLAN_DAY_TOOL, REFLECT_TOOL, UPDATE_FARM_PLAN_TOOL
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

    def test_context_puts_stable_fields_first_and_surfaces_blocked_directions(self) -> None:
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

            self.assertLess(context.index('"objective_ledger"'), context.index('"game_state"'))
            self.assertLess(context.index('"game_state"'), context.index('"counters"'))
            self.assertIn('"harnessBlockedDirectionsHere":["W"]', context)
            self.assertIn('"harnessStaminaLow":true', context)
            self.assertIn('"harnessBedtimeAllowed":true', context)
            self.assertIn('"harnessStalledDecisions":5', context)
            self.assertIn('"harnessLastResult":{"tool":"hold","status":"blocked"}', context)
            self.assertIn('"world":{"here":"FarmHouse","exits":["Farm"]}', context)

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
            self.assertIn('"world":{"here":"Farm","exits":["Town"]}', context)

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
                {"goal": "Visit Town", "success_condition": "location is Town", "slot": "morning"},
                {"goal": "Gather wood", "success_condition": "inventory.Wood >= 10", "slot": "midday"},
                {"goal": "Clear debris", "success_condition": "stamina <= 200", "slot": "afternoon"},
                {"goal": "Return home", "success_condition": "location is FarmHouse", "slot": "evening"},
                {"goal": "Organize tools", "success_condition": "toolbarIndex >= 0", "slot": "evening"},
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
                    {"goal": f"Goal {index}", "slot": "evening" if index == 3 else "morning"}
                    for index in range(4)
                ], "dropped": []}, {}, None,
            )
            valid = ToolDecision(
                "plan_day",
                {"theme": "Full", "agenda": [
                    {"goal": f"Goal {index}", "slot": "evening" if index == 4 else "morning"}
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
                    {"goal": f"Goal {index}", "slot": "evening" if index == 3 else "morning"}
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

    def test_objective_completion_marks_linked_agenda_item_done(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = object.__new__(AutoplayHarness)
            harness.notebook = Notebook(Path(directory))
            harness.notebook.start_day(3)
            harness.notebook.set_agenda(
                3,
                [{"goal": "Earn money", "success_condition": "money >= 10", "slot": "morning"}],
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
                4, [{"goal": "Late task", "slot": "evening"}], "Long day"
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


if __name__ == "__main__":
    unittest.main()
