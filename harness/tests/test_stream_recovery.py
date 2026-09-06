import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness import life
from autoplay_harness.chat import ChatAgent
from autoplay_harness.encounters import approach_and_talk
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.openrouter import OpenRouterError, ToolDecision
from autoplay_harness.progress import ActivityProgress
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.state_actor import STATE_ACTOR_PROMPT
from autoplay_harness.wiki import StardewWiki


STATE = dict(worldReady=True, day=13, year=1, season="spring", location="Town", menu="none",
             eventUp=False, eventId=None, eventPhase=None, health=100, time=1000,
             tileX=1, tileY=1, canMove=True, playerFree=True, inventoryCounts={},
             navigationRows=["......."] * 7, navigationOriginX=0, navigationOriginY=0,
             quests=[], inventory=[])
NPC = dict(id="Caroline", name="Caroline", kind="Villager", x=4, y=1, talkedToday=False)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h = object.__new__(AutoplayHarness)
        self.h.notebook = Notebook(Path(self.temp.name))
        self.h.notebook.start_day(13)
        self.h.ledger = ObjectiveLedger(Path(self.temp.name) / "objectives.json", "Find the axe", "money >= 1000")
        self.h.telemetry = Mock(step=0)
        self.h.operator_mode = "playing"
        self.h.stop_reason = None
        self.h.game_actions = 0

    def notice(self):
        self.h.notebook.observe_life({**STATE, "npcsNearby": [NPC]})
        return life.pending(self.h.notebook.data["life"])[0]

    def test_stale_caroline_and_haley_cannot_be_selected(self):
        for name in ("Caroline", "Haley"):
            with self.subTest(name=name):
                data = {"notices": {"npc:" + name + ":13": dict(id="npc:" + name + ":13", kind="encounter",
                        status="pending", location="Town", day=13, target_id=name)}}
                with self.assertRaises(ValueError):
                    life.respond(data, "npc:" + name + ":13", "act", "Greet", "Nearby", "",
                                 {**STATE, "npcsNearby": [{**NPC, "id": "Lewis", "name": "Lewis"}]})
                self.assertEqual("expired", next(iter(data["notices"].values()))["status"])

    def test_bookseller_day11_expires_but_tools_and_quests_survive(self):
        data = self.h.notebook.data["life"]
        for kind in ("hud", "new_tool"):
            life.add_notice(data, dict(id=kind, kind=kind, text=kind, day=11, season="spring", year=1))
        self.h.notebook.observe_life({**STATE, "day": 14})
        self.assertEqual("expired", data["notices"]["hud"]["status"])
        self.assertEqual("pending", data["notices"]["new_tool"]["status"])

    def test_encounter_expires_on_location_or_day_change(self):
        notice = self.notice()
        life.expire_notices(self.h.notebook.data["life"], {**STATE, "location": "Forest", "npcsNearby": [NPC]})
        self.assertEqual("expired", notice["status"])

    def test_deferred_quest_condition_triggers_once(self):
        data = self.h.notebook.data["life"]
        data["quest_review"] = {"deferred": [{"revisit_when": "time >= 1000"}]}
        self.h.notebook.observe_life(STATE)
        data["quest_reviewed_revision"] = "reviewed"
        self.h.notebook.observe_life(STATE)
        self.assertEqual("reviewed", data["quest_reviewed_revision"])

    def test_event_exit_expires_guidance_goal_and_retrieval_not_unrelated_agenda(self):
        festival = {**STATE, "eventUp": True, "eventId": "egg13", "eventPhase": "10"}
        self.h.operator_guidance = "The hunt has started"
        self.h.operator_guidance_scope = self.h._scene_scope(festival)
        self.h.last_scene_scope = self.h._scene_scope(festival)
        self.h.latest_life_text = {"text": "Festival instructions"}
        self.h.ledger.pursue_interest("Join hunt", "At the festival")
        self.h._scope_event_objective(festival)
        agenda = self.h.notebook.data["days"]["13"]["agenda"]
        agenda.extend([dict(id="event", category="event", status="pending", scope=self.h._scene_scope(festival)),
                       dict(id="axe", category="exploring", status="pending")])
        self.h._expire_scene_work({**STATE, "eventUp": True, "eventId": "community-center"})
        self.assertEqual("", self.h.operator_guidance)
        self.assertIsNone(self.h.latest_life_text)
        self.assertIsNone(self.h.ledger.snapshot()["active"])
        self.assertEqual(["dropped", "pending"], [item["status"] for item in agenda])

    def test_next_day_drops_legacy_event_agenda_only(self):
        self.h.notebook.data["days"]["13"]["agenda"] = [
            dict(id="egg", category="event", status="pending"), dict(id="axe", category="exploring", status="pending")]
        self.assertEqual(["axe"], [item["id"] for item in self.h.notebook.start_day(14)["agenda"]])

    def test_wandering_menu_toggles_and_new_goal_text_cannot_reset_deadline(self):
        self.h.blocked_movements = set()
        self.h.last_action_fingerprint = None
        self.h.stalled_decisions = 0
        with patch("autoplay_harness.progress.time.monotonic", return_value=0) as clock:
            self.h._check_activity_progress(STATE)
            for index in range(1, 31):
                clock.return_value = index * 5
                self.h._check_activity_progress({**STATE, "tileX": index, "menu": str(index % 2),
                    "dialogueText": "Repeated exchange " + str(index), "goal": str(index)}, decision=True)
            # Live play is never ended by the harness; the intention is abandoned and a different one demanded.
            self.assertIsNone(self.h.stop_reason)
            self.assertTrue(self.h.stall_review_requested)
            self.assertIsNone(self.h.ledger.snapshot()["active"])
            self.assertIn("clearly different activity", self.h.director_feedback)

    def test_real_crop_and_event_phase_milestones_allow_continuation(self):
        with patch("autoplay_harness.progress.time.monotonic", return_value=0) as clock:
            progress = ActivityProgress()
            progress.observe({**STATE, "wateredCrops": 0})
            for index in range(1, 30):
                clock.return_value = index * 20
                self.assertIsNone(progress.observe({**STATE, "wateredCrops": index}, decision=True))
            clock.return_value += 100
            self.assertIsNone(progress.observe({**STATE, "wateredCrops": 29, "eventId": "festival", "eventPhase": "next"}))

    def test_revisiting_routes_or_inventory_does_not_count_as_new_progress(self):
        with patch("autoplay_harness.progress.time.monotonic", return_value=0) as clock:
            progress = ActivityProgress()
            for index in range(40):
                clock.return_value = index * 5
                status = progress.observe({**STATE, "location": ["Town", "Forest"][index % 2],
                                            "inventoryCounts": {"Wood": index % 2}}, decision=True)
                if status == "escalate":
                    break
            self.assertEqual("escalate", status)
            self.assertLess(index, 40)

    def test_long_drought_abandons_the_intention_before_the_next_model_call(self):
        self.h.blocked_movements = set()
        self.h.last_action_fingerprint = None
        self.h.stalled_decisions = 0
        self.h._check_activity_progress(STATE)
        self.h.activity_progress.last_progress -= 151
        self.h._check_activity_progress(STATE)
        self.assertIsNone(self.h.stop_reason)
        self.assertIsNone(self.h.ledger.snapshot()["active"])
        self.assertTrue(self.h.stall_review_requested)

    def test_recorded_festival_failures_stop_at_first_sample_after_deadline(self):
        cases = json.loads((Path(__file__).parent / "fixtures/festival-stall-replay.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(run=case["run"]), patch("autoplay_harness.progress.time.monotonic", return_value=0) as clock:
                progress = ActivityProgress()
                statuses = []
                for sample in case["samples"]:
                    clock.return_value = sample["seconds"]
                    statuses.append(progress.observe(sample["state"], decision=True))
                # The recorded festival drought asks for a replan first, then a scene change; it never stops the run.
                self.assertIn("replan", statuses)
                self.assertNotIn("stop", statuses)

    def test_event_change_invalidates_old_input_and_director_scene(self):
        before = {**STATE, "eventUp": True, "eventId": "egg13", "eventPhase": "2"}
        after = {**before, "eventId": "community-center"}
        self.assertTrue(self.h._decision_scene_changed(ToolDecision("hold", {"buttons": ["W"]}, {}, None), before, after))
        self.assertNotEqual(self.h._director_state_key(before), self.h._director_state_key(after))

    def test_greeting_reflex_follows_a_moving_target_without_a_model_call(self):
        bridge = Mock()
        nearby = {**STATE, "tileX": 3, "npcsNearby": [NPC]}
        moved = {**STATE, "tileX": 3, "npcsNearby": [{**NPC, "x": 5}]}
        adjacent = {**STATE, "tileX": 4, "npcsNearby": [{**NPC, "x": 5}]}
        dialogue = {**adjacent, "menu": "DialogueBox"}
        bridge.observe.side_effect = [{"status": "completed", "state": s} for s in (moved, adjacent, dialogue, dialogue)]
        bridge.request.side_effect = [{"status": "completed", "state": adjacent}, {"status": "completed", "state": dialogue},
                                      {"status": "completed", "reason": "dialogue_finished", "state": STATE}]
        self.h.bridge = bridge
        self.assertTrue(self.h._greet_in_passing(nearby))
        self.assertEqual(["navigate", "talk_to_npc", "watch_dialogue"], [c.args[0] for c in bridge.request.call_args_list])
        self.assertEqual("Caroline", bridge.request.call_args_list[1].kwargs["value"])

    def actor_tools(self, state):
        self.h._observe = Mock(return_value=(state, None))
        self.h._context = Mock(return_value="context")
        self.h.inspect_next_scene = False
        self.h.decisions = 0
        self.h._decide = Mock(side_effect=RuntimeError("captured decision boundary"))
        with self.assertRaisesRegex(RuntimeError, "captured decision boundary"):
            self.h._actor_step()
        return {tool["function"]["name"] for tool in self.h._decide.call_args.args[3]}

    def test_festival_exposes_controls_and_lookup_without_impossible_navigation(self):
        tools = self.actor_tools({**STATE, "eventUp": True, "eventId": "egg13", "eventCanMove": True})
        self.assertTrue({"hold", "wiki_search", "choose_dialogue_response"}.issubset(tools))
        self.assertFalse({"navigate_to", "check_journal", "review_quests", "travel_to"} & tools)

    def test_target_leaving_or_operator_input_stops_continuation(self):
        notice = self.notice()
        bridge = Mock()
        bridge.observe.return_value = {"status": "completed", "state": {**STATE, "npcsNearby": []}}
        self.assertEqual("missed", approach_and_talk(bridge, notice, STATE)["status"])
        bridge.request.assert_not_called()
        self.assertEqual("yielded", approach_and_talk(bridge, notice, STATE, lambda: True)["status"])

    def test_failed_wiki_lookup_does_not_crash_or_repeat_without_progress(self):
        self.h._check_activity_progress(STATE)
        self.h.bridge = Mock()
        self.h.wiki = Mock()
        self.h.wiki.search.side_effect = TimeoutError("offline")
        decision = ToolDecision("wiki_search", {"query": "Egg Festival"}, {}, None)
        self.assertEqual("blocked", self.h._execute_actor_action(decision, None, STATE)["status"])
        self.assertEqual("rejected", self.h._execute_actor_action(decision, None, STATE)["status"])
        self.h.wiki.search.assert_called_once()

    def test_wiki_empty_search_has_only_one_fallback(self):
        wiki = StardewWiki()
        wiki._search_pages = Mock(return_value=[])
        self.assertEqual([], wiki.search("how to start the egg hunt festival"))
        self.assertEqual(2, wiki._search_pages.call_count)

    def test_chat_provider_failure_does_not_end_reader(self):
        async def scenario():
            class Source:
                async def messages(self):
                    await asyncio.Event().wait()
                    yield
            agent = ChatAgent(Path(self.temp.name), Mock(), window_seconds=0.001)
            calls = 0
            async def process(messages):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OpenRouterError("provider exhausted")
                raise asyncio.CancelledError()
            agent.process_window = process
            with self.assertRaises(asyncio.CancelledError):
                await agent.run(Source())
            self.assertEqual(2, calls)
            self.assertIn("provider exhausted", agent.state["last_error"])
        asyncio.run(scenario())
