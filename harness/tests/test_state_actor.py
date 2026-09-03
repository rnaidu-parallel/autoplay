import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.capture import Frame
from autoplay_harness.openrouter import OpenRouterClient, ToolDecision
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.state_actor import STATE_ACTOR_TOOLS, can_use_state_actor, compact_state, prompt_ledger
from autoplay_harness.tools import ACTOR_TOOLS


class StateActorTests(unittest.TestCase):
    state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
             "eventUp": False, "minigame": "none", "location": "Farm", "health": 100,
             "stamina": 100, "npcsNearby": [], "dialogueResponses": [], "plantedCrops": 0}

    def test_interactive_or_unknown_scenes_use_visual_controller(self):
        self.assertTrue(can_use_state_actor(self.state))
        for patch_state in ({"menu": "ShopMenu"}, {"eventUp": True}, {"minigame": "FishingGame"},
                            {"npcsNearby": [{"name": "Slime"}]}, {"playerFree": False}, {"canMove": None}):
            with self.subTest(patch_state=patch_state):
                self.assertFalse(can_use_state_actor({**self.state, **patch_state}))
        self.assertFalse(can_use_state_actor({}))

    def test_compact_tables_preserve_targets_resources_and_do_not_mutate_state(self):
        state = {**self.state, "cropsNearby": [{"x": 3, "y": 4, "crop": None, "watered": True,
                                              "readyToHarvest": False, "dead": False, "screenX": 123}],
                 "inventory": [{"slot": 8, "name": "Seeds", "qualifiedId": "472", "stack": 20, "isSeed": True}]}
        before = copy.deepcopy(state)
        compact = compact_state(state)
        self.assertEqual([[3, 4, None, True, False, False]], compact["cropsNearby"]["rows"])
        self.assertEqual([[8, "Seeds", "472", 20, True, None, None]], compact["inventory"]["rows"])
        self.assertEqual(100, compact["health"])
        self.assertEqual(before, state)
        self.assertFalse({"click", "press", "hold"} & {t["function"]["name"] for t in STATE_ACTOR_TOOLS})

    def test_ledger_removes_only_audit_timestamps(self):
        ledger = {"active": {"goal": "Plant", "created_at": "now"},
                  "progress": [{"note": "arrived", "at": "now"}]}
        self.assertEqual({"active": {"goal": "Plant"}, "progress": [{"note": "arrived"}]}, prompt_ledger(ledger))
        self.assertEqual("now", ledger["active"]["created_at"])

    def test_actor_tools_require_a_short_narration(self):
        for tool in [*ACTOR_TOOLS, *STATE_ACTOR_TOOLS]:
            with self.subTest(tool=tool["function"]["name"]):
                parameters = tool["function"]["parameters"]
                self.assertIn("say", parameters["required"])
                self.assertEqual("string", parameters["properties"]["say"]["type"])
                self.assertEqual(140, parameters["properties"]["say"]["maxLength"])

    @patch("autoplay_harness.runner.ScreenCapture")
    def test_inspection_spends_one_decision_then_uses_fresh_visual_without_game_input(self, _capture):
        with tempfile.TemporaryDirectory() as directory:
            harness = AutoplayHarness(Path(directory), "Plant five", "plantedCrops >= 5",
                                      OpenRouterClient.LUNA_MODEL, "test", max_decisions=2)
            harness.bridge = Mock()
            frames = [Frame(f"frame-{i}", 1920, 1080, f"image-{i}", None) for i in (1, 2)]
            harness._observe = Mock(side_effect=[(self.state, frame) for frame in frames])
            harness.client.choose_tool = Mock(side_effect=[
                ToolDecision("inspect_scene", {}, {}, None),
                ToolDecision("objective_progress", {"note": "Need soil", "evidence": "No crops"}, {}, None)])
            try:
                harness._actor_step()
                harness._actor_step()
                calls = harness.client.choose_tool.call_args_list
                self.assertIsNone(calls[0].args[2])
                self.assertIs(STATE_ACTOR_TOOLS, calls[0].args[3])
                self.assertEqual("image-2", calls[1].args[2])
                self.assertIs(ACTOR_TOOLS, calls[1].args[3])
                self.assertEqual(2, harness.decisions)
                self.assertEqual(0, harness.game_actions)
                harness.bridge.request.assert_not_called()
            finally:
                harness.director_executor.shutdown()
