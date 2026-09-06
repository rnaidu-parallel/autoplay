"""Behaviours that keep a live stream flowing: lenient staleness, pocket-aware routing, human overlay copy."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from autoplay_harness.farming import go_home_and_sleep
from autoplay_harness.openrouter import ToolDecision
from autoplay_harness.overlay import OverlayState, action_outcome
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.world import WorldMap


FREE = dict(worldReady=True, day=16, season="spring", year=1, location="Town", menu="none", eventUp=False,
            eventId=None, playerFree=True, canMove=True, health=100, pixelX=10, pixelY=10, npcsNearby=[])
TALK = {**FREE, "menu": "DialogueBox:question=False:selected=-1:responses=0", "playerFree": False, "canMove": False}


class StalenessTests(unittest.TestCase):
    def changed(self, name, before, after, arguments=None):
        return AutoplayHarness._decision_scene_changed(ToolDecision(name, arguments or {}, {}, None), before, after)

    def test_dialogue_advancing_while_thinking_does_not_reject_a_press(self):
        self.assertFalse(self.changed("press", TALK, {**TALK, "dialogueText": "next page", "eventPhase": "3"}))
        self.assertFalse(self.changed("hold", FREE, {**FREE, "npcsNearby": [{"name": "Lewis"}], "canMove": False}))
        # A dialogue that closed makes X a world action, and a question needs a real choice.
        self.assertTrue(self.changed("press", TALK, FREE))
        self.assertTrue(self.changed("press", TALK, {**TALK, "dialogueResponses": [{"index": 0}]}))

    def test_pointer_and_menu_actions_still_care_about_geometry(self):
        self.assertTrue(self.changed("click", FREE, {**FREE, "pixelX": 40}))
        self.assertTrue(self.changed("click", FREE, {**FREE, "npcsNearby": [{"name": "Lewis"}]}))
        self.assertTrue(self.changed("click_menu_entry", FREE, {**FREE, "menuEntries": [{"label": "x"}]}))
        self.assertTrue(self.changed("choose_dialogue_response", TALK, {**TALK, "dialogueResponses": [{"index": 0}]}))

    def test_skills_only_care_about_place_day_and_cutscenes(self):
        self.assertFalse(self.changed("water_crops", FREE, {**FREE, "menu": "none", "npcsNearby": [{"name": "Lewis"}], "pixelX": 99}))
        self.assertTrue(self.changed("water_crops", FREE, {**FREE, "location": "Farm"}))
        self.assertTrue(self.changed("travel_to", FREE, {**FREE, "eventUp": True}))
        self.assertTrue(self.changed("navigate_to", FREE, {**FREE, "day": 17}))
        self.assertTrue(self.changed("navigate_to", FREE, {**FREE, "health": 40}))


class PocketRoutingTests(unittest.TestCase):
    """The Farm's south entrance is cut off from the house by debris. Home is via Town and the Bus Stop."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.world = WorldMap(Path(self.temp.name))
        names = ["FarmHouse", "Farm", "Forest", "Town", "BusStop"]
        self.world.nodes = {name: {"name": name} for name in names}
        pairs = [("FarmHouse", "Farm"), ("Farm", "FarmHouse"), ("Farm", "Forest"), ("Forest", "Farm"),
                 ("Farm", "BusStop"), ("BusStop", "Farm"), ("Forest", "Town"), ("Town", "Forest"),
                 ("Town", "BusStop"), ("BusStop", "Town")]
        self.world.edges = [{"from": a, "to": b, "kind": "warp"} for a, b in pairs]
        self.world._loaded = True

    def exits(self, reachable, unreachable):
        return ([{"target": t, "reachable": True, "kind": "warp"} for t in reachable]
                + [{"target": t, "reachable": False, "kind": "door"} for t in unreachable])

    def test_route_home_leaves_the_pocket_and_comes_back_by_the_bus_stop(self):
        self.world.observe({"location": "Forest", "day": 16, "season": "spring", "year": 1})
        south = {"location": "Farm", "day": 16, "season": "spring", "year": 1,
                 "exits": self.exits(["Forest"], ["FarmHouse", "BusStop"])}
        self.world.observe(south)
        self.assertEqual(["Forest", "Town", "BusStop", "Farm", "FarmHouse"], self.world.route("Farm", "FarmHouse"))
        summary = self.world.summary("Farm", 2100)
        self.assertEqual(["BusStop", "FarmHouse"], summary["blockedNow"])
        # Entering from the Bus Stop is a different pocket with the house in reach.
        self.world.observe({"location": "BusStop", "day": 16, "season": "spring", "year": 1})
        self.world.observe({"location": "Farm", "day": 16, "season": "spring", "year": 1,
                            "exits": self.exits(["FarmHouse", "BusStop"], ["Forest"])})
        self.assertEqual(["FarmHouse"], self.world.route("Farm", "FarmHouse"))
        reloaded = WorldMap(Path(self.temp.name))
        self.assertIn("Farm|Forest", reloaded.pockets)

    def test_old_pocket_observations_lapse_once_debris_may_be_cleared(self):
        self.world.observe({"location": "Forest", "day": 10, "season": "spring", "year": 1})
        self.world.observe({"location": "Farm", "day": 10, "season": "spring", "year": 1,
                            "exits": self.exits(["Forest"], ["FarmHouse", "BusStop"])})
        self.assertEqual(5, len(self.world.route("Farm", "FarmHouse")))
        self.world.observe({"location": "Farm", "day": 16, "season": "spring", "year": 1})
        self.assertEqual(["FarmHouse"], self.world.route("Farm", "FarmHouse"))

    def test_go_home_from_town_uses_the_route_home(self):
        bridge = Mock()
        state = {**FREE, "time": 2340, "stamina": 100, "money": 10}
        farmhouse = {**state, "location": "FarmHouse", "bedTile": None}
        bridge.request.return_value = {"status": "completed", "state": farmhouse}
        self.world.observe({"location": "Town", "day": 16, "season": "spring", "year": 1})
        result = go_home_and_sleep(bridge, state, 40, self.world)
        # Far from home the skill travels instead of refusing; the mocked hop fails honestly.
        self.assertEqual("go_to_location", bridge.request.call_args_list[0].args[0])
        self.assertTrue(result["reason"].startswith("hop_failed:"))
        self.assertEqual("not_on_farm", go_home_and_sleep(bridge, state, 40, None)["reason"])


class OverlayCopyTests(unittest.TestCase):
    def test_outcomes_read_like_a_person_not_a_log(self):
        self.assertEqual("blocked: no clear path that way", action_outcome({"status": "blocked", "reason": "no_walkable_path"}))
        self.assertEqual("blocked: that way out is cut off from here", action_outcome({"status": "blocked", "reason": "hop_failed:no_walkable_path_to_exit"}))
        self.assertEqual("rejected: the moment passed", action_outcome({"status": "rejected", "reason": "scene_changed_while_thinking"}))
        self.assertEqual("completed", action_outcome({"status": "completed", "reason": "control_released"}))

    def test_scene_conversation_and_reflex_reach_the_page(self):
        overlay = OverlayState("run")
        overlay.apply({"type": "scene_watching", "at": "2026-09-06T10:00:00+00:00", "opening": "Lewis: Welcome!"})
        self.assertEqual("Lewis: Welcome!", overlay.snapshot()["scene"]["opening"])
        overlay.apply({"type": "scene_watched", "at": "2026-09-06T10:00:30+00:00", "reason": "dialogue_finished"})
        overlay.apply({"type": "greeting", "at": "2026-09-06T10:01:00+00:00", "npc": "Lewis", "status": "completed"})
        overlay.notebook = {"life": {"texts": {"conversation:1": {"id": "conversation:1", "kind": "conversation",
                                                                   "text": "Lewis: Welcome to town!\nLewis: Mind the rats.", "day": 16}}}}
        snapshot = overlay.snapshot()
        self.assertIsNone(snapshot["scene"])
        self.assertEqual("Says hello to Lewis", snapshot["reflex"]["text"])
        self.assertEqual("Lewis: Welcome to town! Lewis: Mind the rats.", snapshot["conversation"]["text"])


class CuriosityTests(unittest.TestCase):
    def test_look_at_walks_beside_faces_and_interacts_once(self):
        from autoplay_harness.farming import look_at
        dog = {"id": "pet:Rufus", "kind": "pet", "name": "Rufus", "x": 6, "y": 4, "screenX": 300, "screenY": 200, "interaction": "pet"}
        rows = ["......."] * 8
        start = {**FREE, "location": "Farm", "tileX": 3, "tileY": 4, "facing": 1, "menu": "none", "worldReady": True,
                 "navigationRows": rows, "navigationOriginX": 0, "navigationOriginY": 0, "curiosities": [dog],
                 "hudMessages": [], "inventoryCounts": {}}
        beside = {**start, "tileX": 5}
        petted = {**beside, "hudMessages": ["Rufus loves you"]}
        bridge = Mock()
        bridge.request.side_effect = [{"status": "completed", "state": beside}, {"status": "completed", "state": petted}]
        result = look_at(bridge, start, 6, 4)
        self.assertEqual(["navigate", "click"], [c.args[0] for c in bridge.request.call_args_list])
        self.assertEqual({"x": 300, "y": 200, "button": "right"}, bridge.request.call_args.kwargs)
        self.assertEqual("completed", result["status"])
        self.assertEqual(["hudMessages"], result["changes"])
        self.assertEqual("pet", result["target"]["interaction"])
        self.assertEqual("rejected", look_at(bridge, start, 1, 1)["status"])

    def test_context_marks_curiosities_already_looked_at_today(self):
        from autoplay_harness.notebook import Notebook
        harness = object.__new__(AutoplayHarness)
        with tempfile.TemporaryDirectory() as directory:
            harness.notebook = Notebook(Path(directory))
            harness.notebook.data["life"]["curiosities"] = {"Farm:pet:Rufus": {"day": 16, "outcome": "looked"}}
            state = {**FREE, "location": "Farm", "curiosities": [{"id": "pet:Rufus"}, {"id": "package:3:4"}]}
            harness.blocked_movements = set(); harness.last_result = None; harness.ledger = Mock()
            harness.ledger.snapshot.return_value = {"active": None}
            harness.world = Mock(); harness.world.summary.return_value = {}
            harness.telemetry = Mock(); harness.telemetry.context.return_value = {}
            harness.latest_wiki_results = []; harness.continuous = True; harness.game_actions = 0
            harness.decisions = 0; harness.director_interval = 12; harness.actor_mode = "state-first"
            harness.director_feedback = None; harness.notebook.start_day(16)
            harness._budget_context = lambda packet, role: packet
            from autoplay_harness.capture import Frame
            packet = harness._context("actor", state, Frame("f", 1280, 720, "", None), state_only=False)
            self.assertEqual([True, False], [c["tried"] for c in packet["game_state"]["curiosities"]])

    def test_morning_plan_needs_a_quest_step_and_somewhere_new(self):
        from autoplay_harness.notebook import Notebook
        harness = object.__new__(AutoplayHarness)
        with tempfile.TemporaryDirectory() as directory:
            harness.notebook = Notebook(Path(directory)); harness.notebook.start_day(16)
            harness.world = Mock(); harness.world.summary.return_value = {"unvisited": ["Beach", "Mountain"]}
            state = {**FREE, "time": 620, "quests": [{"id": "9", "title": "Introductions", "complete": False}]}
            plain = {"theme": "Chores", "agenda": [{"goal": "Water crops", "slot": "morning", "category": "farming"}]}
            errors = harness._agenda_errors(plain, state, "morning")
            self.assertTrue(any("quest:9" in error for error in errors))
            self.assertTrue(any("Beach" in error for error in errors))
            rich = {"theme": "Chores", "agenda": plain["agenda"] + [
                {"goal": "Greet two villagers", "slot": "midday", "category": "social", "source": "quest:9"},
                {"goal": "See the Beach", "slot": "afternoon", "category": "exploring"}]}
            errors = harness._agenda_errors(rich, state, "morning")
            self.assertFalse(any("quest:" in error or "exploring" in error for error in errors))
            self.assertEqual([], [e for e in harness._agenda_errors(plain, state, "refill") if "quest:" in e or "exploring" in e])


if __name__ == "__main__":
    unittest.main()
