import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from autoplay_harness import life
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.openrouter import ToolDecision
from autoplay_harness.overlay import OverlayState


STATE = dict(worldReady=True, location="Farm", menu="none", eventUp=False, day=24, season="spring", year=1,
             time=900, mailCount=0, inventoryCapacity=12, inventoryFreeSlots=0, tileX=64, tileY=20,
             inventory=[{"slot": 11, "qualifiedId": "(T)BambooPole", "name": "Bamboo Pole", "stack": 1, "maxStack": 1, "isTool": True}],
             questRevision="r1", questStates={"100": "active", "101": "active"},
             quests=[{"id": "100", "title": "Robin's Lost Axe", "description": "Search south of Marnie's ranch", "complete": False, "reward": 250, "objectives": ["Find Robin's lost axe."]},
                     {"id": "101", "title": "Jodi's Request", "description": "Bring a cauliflower", "complete": False, "reward": 350, "objectives": ["Bring Jodi a cauliflower."]}])


class LifePolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.notebook = Notebook(self.root)
        self.notebook.observe_life(STATE)
        self.life = self.notebook.data["life"]
        self.harness = object.__new__(AutoplayHarness)
        self.harness.notebook = self.notebook
        self.harness.ledger = ObjectiveLedger(self.root / "objectives.json", "Unrelated old plan", "money >= 1000")
        self.harness.telemetry = Mock()
        self.harness.bridge = Mock()
        self.harness.game_actions = 0
        self.harness.operator_mode = "playing"

    def test_transient_notice_survives_disappearance_restart_and_ack(self):
        text = "The Flower Dance has begun in the forest."
        notice = dict(id="session:1", kind="hud", text=text, day=24, season="spring", year=1, time=900, location="Farm")
        self.notebook.observe_life({**STATE, "notices": [notice]})
        self.notebook.observe_life({**STATE, "notices": []})
        loaded = Notebook(self.root)
        self.assertEqual(text, loaded.data["life"]["texts"]["session:1"]["text"])
        pending = next(n for n in life.pending(loaded.data["life"]) if n["text"] == text)
        life.respond(loaded.data["life"], pending["id"], "act", "Visit the Flower Dance", "It is open now", "", STATE)
        loaded._save()
        loaded.observe_life({**STATE, "notices": [notice]})
        self.assertFalse(any(n["text"] == text for n in life.pending(loaded.data["life"])))

    def test_every_morning_mail_and_new_letter_prevent_departure(self):
        self.assertFalse(life.mail_due(self.life, STATE))
        morning = {**STATE, "day": 25, "location": "FarmHouse", "mailCount": 1, "time": 600}
        self.assertTrue(life.mail_due(self.life, morning))
        self.assertIn("mail", life.guard(self.life, "travel_to", {"destination": "Town"}, morning))
        farm = {**morning, "location": "Farm"}
        self.assertIsNone(life.guard(self.life, "check_mail", {}, farm))
        self.assertIn("mail", life.guard(self.life, "water_crops", {}, farm))
        self.notebook.observe_life({**farm, "mailCount": 0})
        self.assertFalse(life.mail_due(self.life, {**farm, "mailCount": 0}))
        self.assertTrue(life.mail_due(self.life, farm))
        self.assertIsNone(life.guard(self.life, "travel_to", {"destination": "FarmHouse"}, farm, urgent=True))

    def test_full_inventory_parsnip_attempt_blocks_but_storage_remains_available(self):
        state = {**STATE, "hudMessages": ["Inventory Full"],
                 "cropsNearby": [{"x": 64, "y": 21, "readyToHarvest": True, "crop": "Parsnip"}]}
        self.notebook.observe_life(state)
        state["hudMessages"] = []
        self.assertIn("Inventory Full", life.guard(self.life, "press", {"buttons": ["X"]}, state))
        self.assertIsNone(life.guard(self.life, "open_menu_tab", {"tab": "inventory"}, state))
        self.assertIsNone(life.guard(self.life, "press", {"buttons": ["X"]}, {**state, "cropsNearby": [], "nearbyObjects": [{"name": "Chest", "x":64,"y":21}]}))
        self.notebook.observe_life({**state, "inventoryFreeSlots": 1})
        self.assertFalse(self.life["inventory_blocked"])
        self.assertIsNone(life.guard(self.life, "press", {"buttons": ["X"]}, {**state, "inventoryFreeSlots": 1}))

    def test_full_bag_allows_compatible_stack_before_any_failed_pickup(self):
        state = {**STATE, "inventory": [{"name": "Parsnip", "stack": 2, "maxStack": 999}],
                 "cropsNearby": [{"x": 64, "y": 21, "readyToHarvest": True, "crop": "Parsnip"}]}
        self.assertIsNone(life.guard(self.life, "press", {"buttons": ["X"]}, state))
        state["inventory"][0]["stack"] = 999
        self.assertIsNotNone(life.guard(self.life, "press", {"buttons": ["X"]}, state))

    def test_uncollectable_letter_item_cannot_be_clicked(self):
        state = {**STATE, "menu": "LetterViewerMenu", "menuEntries": [{"label": "Take seeds", "canAccept": False}]}
        self.assertIn("cannot accept", life.guard(self.life, "click_menu_entry", {"index":0}, state))

    def test_received_rod_requires_response_and_deferral_resurfaces(self):
        rod = next(n for n in life.pending(self.life) if n["kind"] == "new_tool")
        self.assertEqual("Bamboo Pole", rod["text"])
        with self.assertRaises(ValueError):
            life.respond(self.life, rod["id"], "defer", "Try fishing", "Later", "time >= 800", STATE)
        life.respond(self.life, rod["id"], "defer", "Try fishing", "Need inventory room", "inventoryFreeSlots > 0", STATE)
        self.notebook.observe_life({**STATE, "inventoryFreeSlots": 1})
        self.assertEqual(rod["id"], life.pending(self.life)[0]["id"])

    def test_quest_review_replaces_vague_goal_and_accounts_for_other_missions(self):
        args = {"quest_id":"100", "next_step":"Search south of Marnie's ranch", "reason":"A feasible request from Robin",
                "deferred":[{"quest_id":"101","reason":"No cauliflower yet","revisit_when":"inventory.Cauliflower > 0"}]}
        state = {**STATE, "inventoryCounts": {"Bamboo Pole": 1}}
        bad = self.harness._execute_life_tool("review_quests", {**args, "deferred":[]}, state)
        self.assertEqual("rejected", bad["status"])
        self.assertIn("has not been opened", bad["reason"])
        self.assertIsNone(self.life.get("quest_reviewed_revision"))
        self.notebook.observe_life({**state, "menu": "QuestLog", "journal": state["quests"]})
        result = self.harness._execute_life_tool("review_quests", args, state)
        self.assertEqual("recorded", result["status"])
        active = self.harness.ledger.snapshot()["active"]
        self.assertIn("Robin's Lost Axe", active["goal"])
        self.assertEqual("questStates.100 is complete", active["success_condition"])
        self.assertFalse(life.quest_review_due(self.life, state))
        self.notebook.observe_life({**state, "inventoryCounts": {"Cauliflower":1}})
        self.assertTrue(life.quest_review_due(self.life, state))

    def test_quest_review_can_preserve_an_unrelated_personal_pursuit(self):
        state = {**STATE, "inventoryCounts": {"Bamboo Pole": 1}}
        self.notebook.observe_life({**state, "menu": "QuestLog", "journal": state["quests"]})
        result = self.harness._execute_life_tool("review_quests", {
            "quest_id": "", "next_step": "Try fishing from the beach pier",
            "reason": "I just received a rod and want to learn how it works",
            "deferred": [
                {"quest_id": "100", "reason": "Resume when I reach the search area", "revisit_when": "location is Forest"},
                {"quest_id": "101", "reason": "I do not have the requested crop", "revisit_when": "inventory.Cauliflower > 0"},
            ],
        }, state)
        self.assertEqual("recorded", result["status"])
        active = self.harness.ledger.snapshot()["active"]
        self.assertEqual("Try fishing from the beach pier", active["goal"])
        self.assertNotIn("Robin", active["goal"])
        self.assertIsNone(active["success_condition"])

    def test_all_quests_and_full_descriptions_can_be_retrieved(self):
        state = {**STATE, "quests": [dict(STATE["quests"][0], id=str(i)) for i in range(8)]}
        self.notebook.observe_life(state)
        self.assertEqual(8, len(self.notebook.life_context(state)["quests"]))
        result = self.harness._execute_life_tool("read_life_text", {"id":"quest:7"}, state)
        self.assertEqual("Search south of Marnie's ranch", json.loads(result["text"])["description"])

    def test_long_retained_text_can_be_read_without_overflow_or_loss(self):
        content = {"id": "long", "text": "A whole conversation. " * 600}
        self.life["texts"]["long"] = content
        parts, offset = [], 0
        while offset is not None:
            result = self.harness._execute_life_tool("read_life_text", {"id": "long", "offset": offset}, STATE)
            self.assertLessEqual(len(result["text"]), 4000)
            parts.append(result["text"])
            offset = result["nextOffset"]
        self.assertEqual(content, json.loads("".join(parts)))

    def test_pickup_failure_is_an_error_even_when_bridge_input_succeeded(self):
        before = {**STATE, "inventoryCounts": {"Wood": 20}}
        after = {**before, "hudMessages": ["Inventory Full"]}
        self.assertTrue(life.pickup_failed("press", {"buttons": ["X"]}, before, after))
        self.assertFalse(life.pickup_failed("press", {"buttons": ["D"]}, before, after))
        self.assertFalse(life.pickup_failed("press", {"buttons": ["X"]}, before, {**after, "inventoryCounts": {"Wood": 20, "Parsnip": 1}}))

    def test_chosen_discovery_can_be_acted_on_before_next_notice(self):
        self.life['quest_reviewed_revision'] = STATE['questRevision']
        rod = life.pending(self.life)[0]
        life.add_notice(self.life, {"id": "door", "kind": "place", "text": "A shop", "day": 24})
        life.respond(self.life, rod['id'], 'act', 'Try fishing', 'Willy gave me a rod', '', STATE)
        self.assertFalse(life.response_due(self.life))
        self.assertIsNone(life.guard(self.life, 'travel_to', {'destination': 'Beach'}, STATE))
        life.add_notice(self.life, {"id": "festival", "kind": "hud", "text": "Flower Dance begins", "day": 24})
        self.assertTrue(life.response_due(self.life))

    def test_all_presented_conversation_pages_are_retained(self):
        notices = [dict(id=f"episode:{i}",kind="dialogue",text=text,location="Beach",day=24) for i,text in enumerate([
            "Here, have my old fishing rod.", "There is good water here.", "I will buy anything you catch."])]
        self.notebook.observe_life({**STATE, "location":"Beach", "eventUp":True, "notices":notices})
        rod = next(n for n in life.pending(self.life) if n["kind"] == "new_tool")
        life.respond(self.life, rod["id"], "act", "Listen to Willy", "He is speaking", "", STATE)
        self.notebook.observe_life({**STATE, "location":"Beach", "eventUp":False, "notices":notices})
        episode = next(n for n in self.life["texts"].values() if n['kind']=='conversation')
        for n in notices: self.assertIn(n['text'], episode['text'])
        self.assertTrue(episode["completed"])
        self.assertFalse(any(n["kind"] == "conversation" for n in life.pending(self.life)))
        self.assertIsNone(self.life.get("responding_to"))

    def test_old_pending_conversation_is_migrated_without_reopening_it(self):
        self.life["notices"]["conversation:old"] = {
            "id": "conversation:old", "kind": "conversation", "text": "Goodbye.", "status": "pending"}
        self.life["responding_to"] = "conversation:old"
        self.notebook.observe_life(STATE)
        self.assertEqual("completed", self.life["notices"]["conversation:old"]["status"])
        self.assertFalse(any(n["kind"] == "conversation" for n in life.pending(self.life)))
        self.assertIsNone(self.life.get("responding_to"))

    def test_unfamiliar_door_requires_one_choice_not_every_step(self):
        state = {**STATE, "nearbyActions":[{"kind":"Action","value":"LockedDoorWarp 4 19 SeedShop 900 1700","x":64,"y":21}]}
        self.notebook.observe_life(state)
        door = next(n for n in life.pending(self.life) if n['kind']=='place')
        life.respond(self.life, door['id'], 'act', 'Inspect the shop', 'An unfamiliar building', '', state)
        self.notebook.observe_life(state)
        self.assertFalse(any(n['id']==door['id'] for n in life.pending(self.life)))

    def test_menu_tab_uses_observed_click_target(self):
        self.harness.bridge.request.side_effect = [
            {"state":{**STATE,"menu":"GameMenu:0:InventoryPage","menuEntries":[{"label":"Tab: skills","screenX":222,"screenY":111}]}},
            {"status":"completed","state":{**STATE,"menu":"GameMenu:1:SkillsPage","menuText":["Fishing: level 0"]}}]
        result = self.harness._execute_life_tool("open_menu_tab", {"tab":"skills"}, STATE)
        self.assertEqual("completed", result['status'])
        self.harness.bridge.request.assert_called_with("click",x=222,y=111,button="left")

    def test_wrong_tab_and_failed_close_are_not_successes(self):
        state = {**STATE, "menu": "GameMenu:3:MapPage"}
        self.harness.bridge.request.return_value = {"status": "completed", "state": {**STATE, "menu": "GameMenu:2:SocialPage"}}
        result = self.harness._click_visible_menu_entry({"label": "Tab: crafting", "screenX": 400, "screenY": 50}, state)
        self.assertEqual("interrupted", result["status"])
        self.assertIn("SocialPage", result["reason"])
        self.harness.bridge.request.return_value = {"status": "completed", "state": state}
        result = self.harness._click_visible_menu_entry({"label": "Close menu", "screenX": 900, "screenY": 30}, state)
        self.assertEqual("blocked", result["status"])

    def test_overlay_prioritizes_actual_mail_and_full_inventory(self):
        overlay = OverlayState("test")
        overlay.update_files(objectives=self.harness.ledger.data, notebook=self.notebook.data)
        overlay.apply({"type":"observation","state":{**STATE,"mailCount":1}})
        self.assertEqual("Read the morning mail", overlay.snapshot()["focus"])
        self.life['inventory_blocked']=True
        overlay.apply({"type":"observation","state":STATE})
        self.assertEqual("Make room in my backpack", overlay.snapshot()["focus"])


if __name__ == "__main__": unittest.main()
