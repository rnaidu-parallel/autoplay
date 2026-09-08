import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.attention import AttentiveBridge, AttentionYield
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger, ObjectiveError, evaluate_state_condition
from autoplay_harness.overlay import OverlayState
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.openrouter import ToolDecision


class LifeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.notebook = Notebook(self.root)

    def test_unfinished_intention_survives_two_mornings_without_new_tasks(self):
        notebook = self.notebook
        notebook.start_day(1)
        notebook.set_agenda(1, [{"goal": "Find Robin's axe", "slot": "afternoon", "category": "exploring", "source": "quest:9"}], "")
        identity = notebook.remaining(1)[0]["id"]
        for day in (2, 3):
            notebook.start_day(day)
            notebook.set_agenda(day, [], "")
            self.assertEqual([identity], [item["id"] for item in notebook.remaining(day)])
            self.assertEqual("quest:9", notebook.remaining(day)[0]["source"])
        reloaded = Notebook(self.root)
        self.assertEqual(identity, reloaded.remaining(3)[0]["id"])

    def test_journal_revision_and_letters_are_remembered_without_icon_loop(self):
        state = {"worldReady": True, "menu": "none", "questRevision": "a", "mailCount": 2}
        # The journal is already in structured state; only unread mail deserves a nudge.
        self.assertEqual(1, len(self.notebook.life_context(state)["attention"]))
        self.notebook.observe_life({**state, "menu": "QuestLog", "journal": [{"id": "9", "title": "Lost axe", "description": "Search the forest"}]})
        self.assertEqual("Search the forest", self.notebook.data["life"]["quests"]["9"]["description"])
        self.assertEqual(1, len(self.notebook.life_context({**state, "questRevision": "b"})["attention"]))
        self.assertEqual([], self.notebook.life_context({**state, "mailCount": 0})["attention"])
        letter = {**state, "menu": "LetterViewerMenu", "letterText": "Please bring my axe."}
        self.notebook.observe_life(letter)
        self.notebook.observe_life(letter)
        self.assertEqual(["Please bring my axe."], self.notebook.data["life"]["letters"])
        self.assertEqual(1, len(self.notebook.life_context(state)["attention"]))  # unread remaining mail

    def test_journal_special_order_without_id_is_keyed_by_title(self):
        state = {"worldReady": True, "menu": "QuestLog", "questRevision": "a",
                 "journal": [{"id": None, "title": "Robin's Project", "description": "Bring 80 hardwood"}]}
        self.notebook.observe_life(state)  # a None key used to break the JSON save
        self.assertEqual("Bring 80 hardwood", self.notebook.data["life"]["quests"]["order:Robin's Project"]["description"])

    def test_navigation_yield_preserves_state_and_bounds_nested_controller(self):
        before = {"location": "Farm", "pixelX": 20}
        after = {"location": "Farm", "pixelX": 400}
        raw = Mock()
        raw.request.return_value = {"status": "yielded", "reason": "encounter_noticed", "state": after}
        bridge = AttentiveBridge(raw, before, lambda: False)
        with self.assertRaises(AttentionYield) as error:
            bridge.request("navigate", x=50, y=10, ticks=600)
        self.assertEqual(after, error.exception.result["state"])
        self.assertEqual(1, error.exception.result["controls_executed"])
        # Whole segments, no interruption for passers-by: the greet reflex handles them at the boundary.
        self.assertEqual(900, raw.request.call_args.kwargs["segmentTicks"])
        self.assertFalse(raw.request.call_args.kwargs["noticeEncounters"])

    def test_arrival_and_operator_commands_stop_before_next_control(self):
        raw = Mock()
        raw.request.return_value = {"status": "completed", "state": {"location": "SeedShop"}}
        bridge = AttentiveBridge(raw, {"location": "Town"}, lambda: False)
        with self.assertRaisesRegex(AttentionYield, "arrived_in_new_area"):
            bridge.request("go_to_location", location="SeedShop")
        self.assertEqual(1, raw.request.call_count)
        bridge = AttentiveBridge(raw, {"location": "Town"}, lambda: True)
        with self.assertRaisesRegex(AttentionYield, "operator_pending"):
            bridge.request("press", buttons=["W"])
        self.assertEqual(1, raw.request.call_count)

    def test_urgent_save_skips_curiosity_and_does_not_interrupt_night(self):
        raw = Mock()
        raw.request.return_value = {"status": "completed", "state": {"location": "FarmHouse", "nightActive": True}}
        bridge = AttentiveBridge(raw, {"location": "Farm", "nightActive": True}, lambda: True, urgent=True)
        bridge.request("wait", field="night_active", value="false", ticks=180)
        self.assertEqual(1, raw.request.call_count)

    def test_wrapper_yields_without_charging_failed_attempt_or_double_counting(self):
        harness = object.__new__(AutoplayHarness)
        harness.bridge = Mock()
        harness.bridge.request.return_value = {"status": "yielded", "reason": "movement_segment_finished", "state": {"location": "Farm"}}
        original = harness.bridge
        harness.game_actions = 4
        harness.telemetry = Mock()
        harness.continuous = True
        result = harness._execute_actor_tool(ToolDecision("navigate_to", {"tile_x": 1, "tile_y": 2}, {}, None), Mock(), {"location": "Farm"})
        self.assertEqual("yielded", result["status"])
        self.assertEqual(5, harness.game_actions)
        self.assertIs(original, harness.bridge)
        # No ledger is needed for a planned yield: it must bypass failure counting.
        harness._track_action_retries(ToolDecision("navigate_to", {}, {}, None), result, {}, {})

    def test_interest_is_considered_and_never_falsely_completes_a_task(self):
        ledger = ObjectiveLedger(self.root / "objectives.json", "Find axe", "questStates.9 is complete")
        with self.assertRaises(ObjectiveError):
            ledger.consider_interest("Walked into the forest")
        ledger.pursue_interest("Look around the shop", "Curious about its offerings")
        self.assertIsNone(evaluate_state_condition(ledger.snapshot()["active"]["success_condition"], {"location": "SeedShop"}))
        ledger.consider_interest("Read prices; cannot afford the backpack yet")
        self.assertEqual(["interrupted", "considered"], [item["status"] for item in ledger.data["history"]])

    def test_revisit_condition_resurfaces_an_old_encounter(self):
        observed = {"location": "SeedShop", "status": "completed", "tool": "click", "day": 1}
        self.notebook.remember_interaction("Backpack", "Read price", "possible", "liked", "Need more money", observed, "money >= 2000")
        self.assertFalse(self.notebook.interaction_context({"money": 5}, 4)[0]["revisit_due"])
        self.assertTrue(self.notebook.interaction_context({"money": 2000}, 4)[0]["revisit_due"])

    def test_feed_retains_seven_turns_through_director_noise(self):
        overlay = OverlayState("life")
        for step in range(10):
            overlay.apply({"type": "actor_decision", "step": step, "tool": "check_journal", "arguments": {"say": str(step)}})
        for step in range(10, 30):
            overlay.apply({"type": "director_decision", "step": step, "tool": "continue_objective"})
        self.assertEqual(["9", "8", "7", "6", "5", "4", "3"], [item["say"] for item in overlay.snapshot()["actorActions"]])

    def test_changed_letter_button_is_not_an_identical_failed_action(self):
        decision = ToolDecision("click_menu_entry", {"index": 0}, {}, None)
        before = {"menu": "LetterViewerMenu", "letterText": "Please find my axe",
                  "menuEntries": [{"label": "Accept request", "screenX": 640, "screenY": 500}]}
        after = {**before, "menuEntries": [{"label": "Close letter", "screenX": 1060, "screenY": 120}]}
        fingerprint = AutoplayHarness._action_fingerprint
        self.assertEqual(fingerprint(decision, before), fingerprint(decision, dict(before)))
        self.assertNotEqual(fingerprint(decision, before), fingerprint(decision, after))
        self.assertNotEqual(fingerprint(decision, before), fingerprint(decision, {**before, "letterText": "Page two"}))


if __name__ == "__main__":
    unittest.main()
