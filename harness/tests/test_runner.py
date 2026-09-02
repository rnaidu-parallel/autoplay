import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.capture import Frame
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.openrouter import OpenRouterClient, OpenRouterError, ToolDecision
from autoplay_harness.runner import AutoplayHarness, HarnessError
from autoplay_harness.telemetry import Telemetry


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
            harness.continuous = True
            harness.game_actions = 3
            harness.decisions = 4
            harness.director_interval = 24

            context = harness._context(
                "actor", {"worldReady": True, "location": "FarmHouse", "pixelX": 5, "pixelY": 6, "stamina": 12}, self.frame
            )

            self.assertLess(context.index('"objective_ledger"'), context.index('"game_state"'))
            self.assertLess(context.index('"game_state"'), context.index('"counters"'))
            self.assertIn('"harnessBlockedDirectionsHere":["W"]', context)
            self.assertIn('"harnessStaminaLow":true', context)
            self.assertIn('"harnessLastResult":{"tool":"hold","status":"blocked"}', context)

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


if __name__ == "__main__":
    unittest.main()
