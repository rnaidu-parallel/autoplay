"""The single-agent day session: objectives are his, breakers are physics, the day is one conversation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from autoplay_harness.agent import AgentHarness, STALL_DECISIONS, SAME_TARGET_LIMIT
from autoplay_harness.capture import Frame
from autoplay_harness.control import OperatorControl
from autoplay_harness.diary import Diary
from autoplay_harness.notebook import Notebook
from autoplay_harness.objectives import ObjectiveLedger
from autoplay_harness.openrouter import ToolDecision
from autoplay_harness.telemetry import Telemetry
from autoplay_harness.world import WorldMap


FARM = dict(worldReady=True, gameActive=True, saveId="save-1", day=3, season="spring", year=1, location="Farm",
            menu="none", eventUp=False, eventId=None, playerFree=True, canMove=True, health=100, stamina=200,
            pixelX=10, pixelY=10, tileX=1, tileY=1, time=900, money=500, inventoryCounts={"Parsnip Seeds": 15},
            npcsNearby=[], inventory=[], menuEntries=[])


def frame():
    return Frame("frame-1", 1280, 720, "data:image/jpeg;base64,AAAA", None)


class Scripted:
    """A model that answers with the next scripted tool call, and an executor that answers with the next result."""

    def __init__(self):
        self.calls = []
        self.results = []
        self.messages = []

    def complete_turn(self, system_prompt, messages, tools, **kwargs):
        self.messages.append(messages)
        name, arguments = self.calls.pop(0)
        return ToolDecision(name, {"say": "Right.", **arguments}, {"cost": 0.0}, "m", call_id=f"call-{len(self.messages)}")


def make(root: Path, state: dict | None = None):
    harness = object.__new__(AgentHarness)
    runs = root / "harness" / "runs"
    harness.run_id = "run-a"
    harness.repository_root = root
    harness.run_directory = runs / harness.run_id
    harness.state_directory = root / "harness" / "state"
    harness.telemetry = Telemetry(harness.run_directory)
    harness.control = OperatorControl(harness.run_directory)
    harness.control.update(mode="playing", closed=False)
    harness.ledger = ObjectiveLedger(harness.state_directory / "objectives.json", "Get my bearings", "")
    harness.notebook = Notebook(harness.state_directory)
    harness.world = WorldMap(harness.state_directory)
    harness.client = Scripted()
    harness.bridge = Mock()
    harness.blocked_movements = set()
    harness.last_action_fingerprint = None
    harness.operator_mode = "playing"
    harness.operator_guidance = ""
    harness.operator_revision = 0
    harness.finish_baseline = None
    harness.continuous = True
    harness.decisions = 0
    harness.max_decisions = 999
    harness.budget_usd = None
    harness.stop_reason = None
    harness.last_result = None
    harness.game_actions = 0
    harness.inspect_next_scene = False
    harness.dialogue_autoplay = False
    harness._init_agent()
    harness.current = dict(state or FARM)
    harness._observe = lambda: (dict(harness.current), frame())
    harness.executed = []

    def execute(decision, frame, state):
        harness.executed.append(decision.name)
        return harness.client.results.pop(0) if harness.client.results else {"status": "completed"}
    harness._execute_actor_tool = execute
    return harness


def events(harness, kind):
    lines = (harness.run_directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if json.loads(line)["type"] == kind]


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_first_step_seeds_bearings_scoped_to_the_save_and_carries_last_nights_diary(self):
        harness = make(self.root)
        Diary(harness.state_directory, "save-1").write(2, 2310, "FarmHouse", "Parsnips in, Robin was kind.", "bedtime")
        harness.client.calls = [("idle", {"ticks": 10})]
        harness._actor_step()
        active = harness.ledger.data["active"]
        self.assertEqual(("Get my bearings", "save-1", "free"), (active["goal"], active["saveId"], active["kind"]))
        first_observation = harness.client.messages[0][0]["content"][0]["text"]
        self.assertIn("Last night you wrote: Parsnips in, Robin was kind.", first_observation)
        self.assertEqual(["idle"], harness.executed)

    def test_set_objective_replaces_atomically_and_records_the_previous_outcome(self):
        harness = make(self.root)
        harness.client.calls = [("set_objective", {"kind": "mission", "goal": "Deliver the parsnips", "why": "Lewis asked",
                                                   "done_when": "Lewis has them", "check": "money >= 600",
                                                   "previous_outcome": "abandoned", "previous_note": "bearings found"})]
        harness._actor_step()
        data = harness.ledger.data
        self.assertEqual("Deliver the parsnips", data["active"]["goal"])
        self.assertEqual("money >= 600", data["active"]["check"])
        self.assertEqual(("abandoned", "bearings found"), (data["history"][-1]["status"], data["history"][-1]["evidence"]))
        self.assertEqual(1, len(events(harness, "objective_set")))  # the seed is adopted, not re-set

    def test_a_completed_claim_with_a_false_check_is_disputed_not_rejected(self):
        harness = make(self.root)
        harness.client.calls = [
            ("set_objective", {"kind": "free", "goal": "Earn 600g", "why": "seeds", "done_when": "I have 600g",
                               "check": "money >= 600", "previous_outcome": "abandoned"}),
            ("set_objective", {"kind": "free", "goal": "Wander", "why": "bored", "done_when": "later",
                               "previous_outcome": "completed", "previous_note": "rich now"}),
            ("idle", {"ticks": 5}),
        ]
        for _ in range(3):
            harness._actor_step()
        self.assertEqual("Wander", harness.ledger.data["active"]["goal"])
        self.assertEqual(1, len(events(harness, "objective_claim_disputed")))
        third_observation = harness.client.messages[2][-1]["content"][0]["text"]
        self.assertIn('completed, but its check was false at the time', third_observation)

    def test_audience_demand_binds_on_take_and_is_done_on_completion(self):
        harness = make(self.root)
        harness.notebook.start_day(3)
        harness.notebook.set_audience_demand("d1", "Pet the dog", 4, 3)
        harness.client.calls = [
            ("set_objective", {"kind": "free", "goal": "Pet the dog", "why": "chat asked", "done_when": "dog petted",
                               "source": "chat:d1", "previous_outcome": "abandoned"}),
            ("set_objective", {"kind": "free", "goal": "Water", "why": "dry", "done_when": "wet",
                               "previous_outcome": "completed"}),
        ]
        harness._actor_step()
        self.assertEqual("bound", harness.notebook.data["audience"]["demand"]["status"])
        harness._actor_step()
        self.assertIsNone(harness.notebook.data["audience"]["demand"])
        self.assertEqual("done", harness.notebook.data["audience"]["recent"][0]["status"])


class BreakerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_identical_action_with_unchanged_state_is_rejected_once(self):
        harness = make(self.root)
        harness.client.calls = [("hold", {"buttons": ["W"], "ticks": 20})] * 2
        harness.client.results = [{"status": "completed"}]
        harness._actor_step()
        harness._actor_step()
        self.assertEqual(["hold"], harness.executed)
        self.assertEqual("identical_action", events(harness, "circuit_breaker")[0]["kind"])

    def test_breakers_stand_down_while_the_fishing_bar_is_open(self):
        harness = make(self.root, {**FARM, "menu": "BobberBar", "playerFree": False})
        harness.client.calls = [("hold", {"buttons": ["C"], "ticks": 20})] * 3
        harness.client.results = [{"status": "completed"}] * 3
        for _ in range(3):
            harness._actor_step()
        self.assertEqual(["hold"] * 3, harness.executed)
        self.assertEqual([], events(harness, "circuit_breaker"))

    def test_a_repeat_purchase_is_not_an_identical_action_and_the_held_item_is_announced(self):
        harness = make(self.root, {**FARM, "menu": "ShopMenu", "money": 500})
        harness.client.calls = [("click", {"x": 0.5, "y": 0.5, "button": "left", "frame_id": "frame-1"})] * 2
        harness.client.results = [{"status": "completed"}, {"status": "completed"}]
        harness._actor_step()
        harness.current.update({"money": 450, "cursorItem": {"name": "Potato Seeds", "stack": 1}})
        harness._actor_step()
        self.assertEqual(["click", "click"], harness.executed)
        self.assertIn("holding 1 Potato Seeds on the cursor", harness.client.messages[1][-1]["content"][0]["text"])

    def test_same_target_closes_after_three_failures_and_reopens_when_the_location_changes(self):
        harness = make(self.root)
        # Alternating tool names defeat the identical-action breaker; the target is still the same place.
        harness.client.calls = [("travel_to", {"destination": "Town"}), ("go_to_location", {"location": "Town"}),
                                ("travel_to", {"destination": "Town"}), ("go_to_location", {"location": "Town"}),
                                ("travel_to", {"destination": "Town"})]
        harness.client.results = [{"status": "blocked", "reason": "no_route"}] * 3
        for _ in range(SAME_TARGET_LIMIT + 1):
            harness._actor_step()
        self.assertEqual(3, len(harness.executed))
        self.assertEqual("same_target", events(harness, "circuit_breaker")[-1]["kind"])
        harness.current["location"] = "BusStop"
        harness._actor_step()
        self.assertEqual(4, len(harness.executed))


class StallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_quiet_stretch_by_day_is_advice_only_and_his_objective_stays(self):
        harness = make(self.root)
        harness.client.calls = [("press", {"buttons": ["X"], "ticks": index}) for index in range(STALL_DECISIONS + 2)]
        harness.client.results = [{"status": "completed"}] * (STALL_DECISIONS + 2)
        harness.progress.observe(harness.current)  # the first facts are known before play starts
        for index in range(STALL_DECISIONS):
            harness.current["time"] = 900 + index * 10
            harness._actor_step()
        self.assertEqual(2, len(events(harness, "stall_advice")))
        self.assertEqual(0, len(events(harness, "scene_changed_by_harness")))
        self.assertEqual("Get my bearings", harness.ledger.data["active"]["goal"])
        advice_observation = harness.client.messages[STALL_DECISIONS - 1][-1]["content"][0]["text"]
        self.assertIn("What will you do differently?", advice_observation)

    def test_a_quiet_stretch_in_the_evening_sends_him_to_bed(self):
        harness = make(self.root)
        harness.client.calls = [("press", {"buttons": ["X"], "ticks": index}) for index in range(STALL_DECISIONS + 2)]
        harness.client.results = [{"status": "completed"}] * (STALL_DECISIONS + 2)
        harness.progress.observe(harness.current)
        for index in range(STALL_DECISIONS):
            harness.current["time"] = 2000 + index * 10
            harness._actor_step()
        self.assertEqual(1, len(events(harness, "scene_changed_by_harness")))
        self.assertEqual("Go home and sleep", harness.ledger.data["active"]["goal"])
        self.assertEqual("stalled", harness.ledger.data["history"][-1]["evidence"])

    def test_completed_waits_do_not_count_toward_the_stall_bound(self):
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 5 + index}) for index in range(STALL_DECISIONS + 4)]
        harness.client.results = [{"status": "completed"}] * (STALL_DECISIONS + 4)
        harness.progress.observe(harness.current)
        for index in range(STALL_DECISIONS + 4):
            harness.current["time"] = 900 + index * 10
            harness._actor_step()
        self.assertEqual(0, len(events(harness, "scene_changed_by_harness")))
        self.assertEqual(0, harness.progress.decisions)

    def test_wandering_into_new_ground_resets_the_clock_only_a_few_times(self):
        from autoplay_harness.agent import AgentProgress
        progress = AgentProgress()
        base = {**FARM, "location": "Town"}
        progress.observe(base)
        for step in range(1, 12):
            progress.observe({**base, "tileX": step * 8, "tileY": 1}, decision=True)
        # four resets were granted (the fourth step still counts one decision after its reset); the rest just counted
        self.assertEqual(11 - AgentProgress.AREA_RESETS + 1, progress.decisions)
        progress.observe({**base, "tileX": 200, "tileY": 1, "money": 900}, decision=True)
        self.assertEqual(0, progress.decisions)  # real evidence resets and re-arms the allowance
        self.assertEqual(0, progress.area_resets)

    def test_a_new_line_of_dialogue_is_evidence(self):
        from autoplay_harness.agent import AgentProgress
        progress = AgentProgress()
        base = {**FARM, "location": "Temp"}
        progress.observe(base)
        for _ in range(6):
            progress.observe(base, decision=True)
        self.assertEqual(6, progress.decisions)
        progress.observe({**base, "menu": "DialogueBox", "dialogueText": "Linus: Oh, hello there."}, decision=True)
        self.assertEqual(1, progress.decisions)  # reset, then this decision counted
        progress.observe({**base, "menu": "DialogueBox", "dialogueText": "Linus: Oh, hello there."}, decision=True)
        self.assertEqual(2, progress.decisions)  # the same line again is not new

    def test_no_scene_change_while_an_event_freezes_the_clock(self):
        harness = make(self.root, {**FARM, "location": "Temp", "eventUp": True, "eventCanMove": True})
        harness.client.calls = [("press", {"buttons": ["X"], "ticks": index}) for index in range(STALL_DECISIONS + 2)]
        harness.client.results = [{"status": "completed"}] * (STALL_DECISIONS + 2)
        harness.progress.observe(harness.current)
        for index in range(STALL_DECISIONS + 2):
            harness.current["time"] = 900
            harness._actor_step()
        self.assertEqual(0, len(events(harness, "scene_changed_by_harness")))
        self.assertGreaterEqual(len(events(harness, "stall_advice")), 1)

    def test_depth_follows_the_last_result_and_think_harder(self):
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 1}), ("idle", {"ticks": 2, "think_harder": True}), ("idle", {"ticks": 3}), ("idle", {"ticks": 4})]
        harness.client.results = [{"status": "completed"}, {"status": "blocked", "reason": "wall"}, {"status": "completed"}, {"status": "completed"}]
        for _ in range(4):
            harness._actor_step()
        depths = [event["depth"] for event in events(harness, "depth_selected")]
        # first call of the day; a completed result; think_harder after a blocked one; then back to low
        self.assertEqual(["medium", "low", "high", "low"], depths)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_new_day_starts_a_new_conversation_and_a_restart_rebuilds_the_old_one(self):
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 1, "diary": "Slow morning."}), ("idle", {"ticks": 2})]
        harness._actor_step()
        harness._actor_step()
        self.assertEqual(2, harness.session.bundle_count())
        harness.current["day"] = 4
        harness.client.calls = [("idle", {"ticks": 3})]
        harness._actor_step()
        self.assertEqual("save-1:4", harness.session_id)
        self.assertEqual(1, harness.session.bundle_count())
        # A second process the same day: the transcript comes back from the events on disk.
        again = make(self.root, {**FARM, "day": 4})
        again.run_id = "run-b"
        again.run_directory = self.root / "harness" / "runs" / "run-b"
        again.telemetry = Telemetry(again.run_directory)
        again.control = OperatorControl(again.run_directory)
        again.control.update(mode="playing", closed=False)
        again.client.calls = [("idle", {"ticks": 4})]
        again._actor_step()
        self.assertEqual(1, len(events(again, "transcript_rebuilt")))
        rebuilt_messages = again.client.messages[0]
        self.assertEqual(["user", "assistant", "tool", "user"], [message["role"] for message in rebuilt_messages])
        self.assertIn("back after a restart", rebuilt_messages[-1]["content"][0]["text"])

    def test_a_crash_between_action_started_and_its_result_rebuilds_as_unknown_and_is_not_replayed(self):
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 1}), ("ship_item", {"slot": 3, "count": 5})]

        def crash(decision, frame, state):
            harness.executed.append(decision.name)
            if decision.name == "ship_item":
                raise RuntimeError("process died mid-action")
            return {"status": "completed"}
        harness._execute_actor_tool = crash
        harness._actor_step()
        with self.assertRaises(RuntimeError):
            harness._actor_step()
        started = events(harness, "action_started")
        self.assertEqual(["idle", "ship_item"], [event["tool"] for event in started])
        self.assertEqual(1, len(events(harness, "tool_result")))
        again = make(self.root)
        again.run_id = "run-b"
        again.run_directory = self.root / "harness" / "runs" / "run-b"
        again.telemetry = Telemetry(again.run_directory)
        again.control = OperatorControl(again.run_directory)
        again.control.update(mode="playing", closed=False)
        again.client.calls = [("idle", {"ticks": 9})]
        again._actor_step()
        rebuilt = events(again, "transcript_rebuilt")[0]
        self.assertEqual([started[-1]["call_id"]], rebuilt["unresolved"])
        messages = again.client.messages[0]
        tool_turns = [message for message in messages if message["role"] == "tool"]
        self.assertEqual("unknown", json.loads(tool_turns[-1]["content"])["status"])
        self.assertEqual(["idle"], again.executed)  # the shipment is not replayed

    def test_max_days_stops_after_the_last_day_ends(self):
        harness = make(self.root)
        harness.max_days = 2
        harness.client.calls = [("idle", {"ticks": 1})] * 3
        harness._actor_step()
        harness.current["day"] = 4
        harness._actor_step()
        self.assertIsNone(harness.stop_reason)
        harness.current["day"] = 5
        harness._actor_step()
        self.assertEqual("max_days_reached", harness.stop_reason)
        self.assertEqual(2, len(harness.client.messages))

    def test_a_harness_placeholder_does_not_survive_into_the_next_day(self):
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 1}), ("idle", {"ticks": 2})]
        harness._actor_step()
        harness._change_scene_by_harness(harness.current)
        self.assertTrue(harness.ledger.data["active"]["by_harness"])
        harness.current["day"] = 4
        harness._actor_step()
        self.assertEqual("Get my bearings", harness.ledger.data["active"]["goal"])
        self.assertIn("set your own now", harness.client.messages[-1][-1]["content"][0]["text"])

    def test_the_journal_is_listed_at_dawn_as_passive_with_each_quests_history(self):
        harness = make(self.root)
        harness.notebook.data["life"]["quests"] = {"6": {"id": "6", "title": "Getting Started", "objectives": ["Cultivate and harvest a parsnip."],
                                                         "daysLeft": None, "reward": 100, "complete": False}}
        harness.notebook.data["life"]["active_quest_ids"] = ["6"]
        harness.ledger.data["history"].append({"goal": "Grow a parsnip", "source": "quest:6", "status": "interrupted",
                                               "evidence": "seeds in, waiting", "started": {"day": 2, "time": 900}})
        harness.client.calls = [("idle", {"ticks": 1}), ("idle", {"ticks": 2})]
        harness._actor_step()
        first = json.loads(harness.client.messages[0][-1]["content"][0]["text"].split("\n", 1)[1])
        quest = first["quests"]["open"][0]
        self.assertEqual(("Getting Started", False), (quest["title"], quest["active"]))
        self.assertEqual({"daysWorked": [2], "lastOutcome": "interrupted", "lastNote": "seeds in, waiting"}, quest["history"])
        self.assertTrue(any(note.startswith("Morning. Your journal: Getting Started") for note in first["notes"]))
        harness.current["time"] = 1300
        harness._actor_step()
        second = json.loads(harness.client.messages[1][-1]["content"][0]["text"].split("\n", 1)[1])
        self.assertFalse(any("open quests" in note for note in second["notes"]))

    def test_calling_a_quest_done_while_the_journal_lists_it_open_is_disputed(self):
        harness = make(self.root)
        harness.current["quests"] = [{"id": "12", "title": "Delivery: Shane", "objectives": ["Bring Shane a leek"], "complete": False}]
        harness.client.calls = [
            ("set_objective", {"kind": "mission", "goal": "Get Shane his leek", "why": "asked", "done_when": "delivered",
                               "source": "quest:12", "previous_outcome": "abandoned"}),
            ("set_objective", {"kind": "free", "goal": "Fish", "why": "sun", "done_when": "dusk",
                               "previous_outcome": "completed", "previous_note": "gave Shane the leek"}),
            ("idle", {"ticks": 1}),
        ]
        for _ in range(3):
            harness._actor_step()
        self.assertEqual("Fish", harness.ledger.data["active"]["goal"])
        self.assertEqual("Delivery: Shane", events(harness, "objective_claim_disputed")[-1]["journal"])
        third = harness.client.messages[2][-1]["content"][0]["text"]
        self.assertIn('the journal still lists \\"Delivery: Shane\\" as open: Bring Shane a leek.', third)

    def test_every_game_action_reports_what_it_changed(self):
        harness = make(self.root)
        after = {**harness.current, "money": 420, "inventoryCounts": {"Parsnip Seeds": 15, "Cauliflower Seeds": 1},
                 "cursorItem": "Cauliflower Seeds", "menu": "ShopMenu"}
        harness.client.calls = [("click", {"frame_id": "frame-1", "x": 0.5, "y": 0.4, "button": "left"}), ("idle", {"ticks": 1})]
        harness.client.results = [{"status": "completed", "reason": "menu_click_released", "state": after}]
        harness._actor_step()
        result = events(harness, "tool_result")[-1]["result"]
        self.assertEqual({"money": "-80", "bag": {"Cauliflower Seeds": "+1"}, "cursorItem": "Cauliflower Seeds",
                          "menu": "none -> ShopMenu"}, result["effects"])
        harness._actor_step()
        observation = json.loads(harness.client.messages[1][-1]["content"][0]["text"].split("\n", 1)[1])
        self.assertEqual("-80", observation["game"]["harnessLastResult"]["effects"]["money"])

    def test_recent_chat_reaches_him_next_to_the_agreed_request(self):
        import time as time_module
        harness = make(self.root)
        (self.root / "chat").mkdir()
        (self.root / "chat" / "state.json").write_text(json.dumps({"recent_messages": [
            {"id": "m1", "user": "viewer7", "text": "pet the dog!", "at": time_module.time() - 30},
            {"id": "m0", "user": "old", "text": "stale", "at": time_module.time() - 3600}]}), encoding="utf-8")
        harness.client.calls = [("idle", {"ticks": 1})]
        harness._actor_step()
        observation = json.loads(harness.client.messages[0][-1]["content"][0]["text"].split("\n", 1)[1])
        self.assertIsNone(observation["audience"]["request"])
        self.assertEqual([("viewer7", "pet the dog!")], [(item["user"], item["text"]) for item in observation["audience"]["chat"]])

    def test_operator_guidance_always_speaks_as_the_stream_operator(self):
        harness = make(self.root)
        harness.control.submit(harness.run_id, "steer", "Head home, the axe can wait.")
        harness.client.calls = [("idle", {"ticks": 1}), ("idle", {"ticks": 2})]
        harness._actor_step()
        harness._actor_step()
        observation = json.loads(harness.client.messages[1][-1]["content"][0]["text"].split("\n", 1)[1])
        self.assertEqual("Stream operator: Head home, the axe can wait.", observation["operator"]["guidance"])

    def test_private_words_never_reach_say_or_diary(self):
        from autoplay_harness import agent as agent_module
        harness = make(self.root)
        harness.client.calls = [("idle", {"ticks": 1, "diary": "Fair point, Rahul."})]
        harness.client.complete_turn = lambda *a, **k: ToolDecision("idle", {"say": "Fair point, Rahul. Off I go.", "ticks": 1, "diary": "Rahul said so."}, {"cost": 0.0}, "m", call_id="c1")
        original = agent_module.PRIVATE_WORDS[:]
        agent_module.PRIVATE_WORDS[:] = ["Rahul"]
        try:
            harness._actor_step()
        finally:
            agent_module.PRIVATE_WORDS[:] = original
        decision = events(harness, "actor_decision")[-1]
        self.assertEqual("Fair point, the operator. Off I go.", decision["arguments"]["say"])
        self.assertEqual("the operator said so.", harness.diary.entries(3)[-1]["text"])

    def test_events_withhold_the_walking_helpers_and_say_so(self):
        harness = make(self.root, {**FARM, "location": "Temp", "eventUp": True, "eventCanMove": True, "eventId": "festival"})
        offered = []
        def complete(system_prompt, messages, tools, **kwargs):
            offered.append([t["function"]["name"] for t in tools])
            return ToolDecision("hold", {"say": "Walking.", "buttons": ["W"], "ticks": 10}, {"cost": 0.0}, "m", call_id="c1")
        harness.client.complete_turn = complete
        harness._actor_step()
        self.assertNotIn("navigate_to", offered[0])
        self.assertIn("hold", offered[0])
        self.assertIn("set_objective", offered[0])
        observation = json.loads(harness.client.messages[0][-1]["content"][0]["text"].split("\n", 1)[1]) if harness.client.messages else None
        # the scripted client above bypasses message capture; check the note through a fresh observation instead
        text, _ = harness._observation(harness.current, frame())
        self.assertIn("walking helpers are off", text)

    def test_diary_field_on_sleep_is_the_bedtime_entry(self):
        harness = make(self.root)
        harness.client.calls = [("go_home_and_sleep", {"diary": "Long day. Tomorrow: seeds."})]
        harness._actor_step()
        entry = harness.diary.entries(3)[-1]
        self.assertEqual(("bedtime", "Long day. Tomorrow: seeds."), (entry["kind"], entry["text"]))


if __name__ == "__main__":
    unittest.main()
