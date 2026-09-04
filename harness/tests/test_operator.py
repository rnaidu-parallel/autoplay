import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness import life as life_policy
from autoplay_harness.calendar import calendar_day
from autoplay_harness.bridge import NamedPipeBridge, BridgeError
from autoplay_harness.checkpoint import Checkpoints, STATE_FILES
from autoplay_harness.control import OperatorControl, write_json
from autoplay_harness.forever import run_forever
from autoplay_harness.history import History
from autoplay_harness.openrouter import OpenRouterClient, ToolDecision
from autoplay_harness.overlay import create_server
from autoplay_harness.runner import AutoplayHarness


STATE = dict(worldReady=True, playerFree=True, canMove=True, location="FarmHouse", menu="none",
             eventUp=False, minigame="none", day=28, season="spring", year=1, time=900,
             saveId="Test_123", saveCount=0, nightActive=False, health=100, stamina=200, tileX=8, tileY=8)
MORNING = {**STATE, "day": 1, "season": "summer", "time": 600, "saveCount": 1}


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        with patch("autoplay_harness.runner.ScreenCapture"):
            self.harness = AutoplayHarness(self.root, "Explore town", "location is Town",
                                          OpenRouterClient.LUNA_MODEL, "offline", continuous=True)
        self.addCleanup(self.harness.director_executor.shutdown)
        self.harness.control.update(mode="playing", closed=False)
        self.harness.bridge = Mock()
        self.harness.bridge.observe.return_value = {"state": STATE}
        self.harness.checkpoints = Checkpoints(self.harness.state_directory, self.root / "saves")
        self.harness.notebook.start_day(28)
        self.harness.world.observe(STATE)

    def finish(self):
        self.harness.control.submit(self.harness.run_id, "finish_save")
        self.assertTrue(self.harness._poll_operator(STATE))

    def disk_save(self):
        folder = self.root / "saves" / STATE["saveId"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / STATE["saveId"]).write_text("<SaveGame><year>1</year><currentSeason>summer</currentSeason><dayOfMonth>1</dayOfMonth></SaveGame>")
        (folder / "SaveGameInfo").write_text("<Farmer />")
        return folder

    def test_finish_interrupts_task_and_allows_early_bed_without_model(self):
        self.finish()
        self.assertIsNone(self.harness.stop_reason)
        self.assertEqual("finishing", self.harness.operator_mode)
        self.assertEqual("interrupted", self.harness.ledger.data["history"][-1]["status"])
        with patch("autoplay_harness.runner.go_home_and_sleep", return_value={"status":"blocked", "controls_executed":0, "state":STATE}) as sleep:
            self.harness._execute_actor_tool(ToolDecision("go_home_and_sleep", {}, {}, None), Mock(), STATE)
            sleep.assert_called_once()
        result = self.harness._execute_actor_tool(ToolDecision("change_objective", {}, {}, None), Mock(), STATE)
        self.assertEqual("rejected", result["status"])

    def test_inflight_actor_decision_is_discarded_when_guidance_arrives(self):
        self.harness._observe = Mock(return_value=(STATE, Mock()))
        self.harness._context = Mock(return_value="{}")
        self.harness._execute_actor_tool = Mock()
        def decide(*args):
            self.harness.control.submit(self.harness.run_id, "steer", "Try the town route")
            return ToolDecision("go_to_location", {"location":"Farm"}, {}, None), 10
        self.harness._decide = decide
        self.harness._actor_step()
        self.harness._execute_actor_tool.assert_not_called()
        self.assertEqual("Try the town route", self.harness.operator_guidance)

    def test_steer_releases_a_compulsory_chore_that_owns_the_tool_list(self):
        farm = {**STATE, "location": "Farm", "mailCount": 1, "time": 630}
        self.harness.bridge.observe.return_value = {"state": farm}
        self.harness.notebook.observe_life(farm)
        life = self.harness.notebook.data["life"]
        self.assertTrue(life_policy.mail_due(life, farm))

        self.harness.control.submit(self.harness.run_id, "steer", "Stop retrying the mailbox.")
        self.assertTrue(self.harness._poll_operator(farm))

        # Steering is useless while the mailbox is still the only tool the actor is offered.
        self.assertFalse(life_policy.mail_due(life, farm))
        self.assertEqual("Stop retrying the mailbox.", self.harness.operator_guidance)

    def test_audience_demand_records_without_preempting_or_pausing_gates(self):
        farm = {**STATE, "location": "Farm", "mailCount": 1, "time": 630, "day": 25}
        self.harness.bridge.observe.return_value = {"state": farm}
        self.harness.notebook.observe_life(farm)
        life = self.harness.notebook.data["life"]

        self.harness.control.submit(self.harness.run_id, "audience", "go fishing at the beach", 12)
        # Chat is never worth a discarded actor decision, and never suppresses a compulsory chore.
        self.assertFalse(self.harness._poll_operator(farm))
        self.assertTrue(life_policy.mail_due(life, farm))

        demand = self.harness.notebook.audience_demand()
        self.assertEqual("go fishing at the beach", demand["goal"])
        self.assertEqual(12, demand["support"])
        self.assertEqual("pending", demand["status"])
        self.assertEqual("playing", self.harness.operator_mode)

    def test_operator_commands_still_preempt_alongside_an_audience_demand(self):
        self.harness.control.submit(self.harness.run_id, "audience", "chop every tree", 4)
        self.harness.control.submit(self.harness.run_id, "hold")
        self.assertTrue(self.harness._poll_operator(STATE))
        self.assertEqual("held", self.harness.operator_mode)

    def test_audience_rejects_a_stop_and_a_negative_vote_count(self):
        for kind in ("stop_session", "shutdown", ""):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.harness.control.submit(self.harness.run_id, kind, "stop the stream")
        with self.assertRaises(ValueError):
            self.harness.control.submit(self.harness.run_id, "audience", "go fishing", -3)
        with self.assertRaises(ValueError):
            self.harness.control.submit(self.harness.run_id, "audience", "   ")

    def test_binding_slot_is_required_once_then_never_crashes_the_run(self):
        day = {**STATE, "location": "Farm", "day": 25, "time": 630}
        self.harness.notebook.start_day(calendar_day(day))
        self.harness.notebook.set_audience_demand("abc123", "go fishing at the beach", 12, calendar_day(day))
        without = {"theme": "Farm chores", "agenda": [
            {"goal": "Water the potato", "slot": "morning", "category": "farming", "success_condition": "wateredCrops >= 1"}]}

        errors = self.harness._agenda_errors(without, day, "morning")
        self.assertTrue(any("go fishing at the beach" in error and "12 viewers" in error for error in errors))
        # A second attempt is accepted regardless: chat must never be able to stop the stream.
        self.assertEqual([], self.harness._agenda_errors(without, day, "morning", bind_audience=False))

        withchat = {"theme": "A day by the water", "agenda": [
            {"goal": "Take the pole to the Beach and fish", "slot": "morning", "category": "fishing",
             "success_condition": "location is Beach", "source": "chat:abc123"}]}
        self.assertEqual([], self.harness._agenda_errors(withchat, day, "morning"))

    def test_missed_and_failed_demands_are_reported_not_hidden(self):
        day = {**STATE, "location": "Farm", "day": 25, "time": 630}
        number = calendar_day(day)
        self.harness.notebook.start_day(number)
        self.harness.notebook.set_audience_demand("abc123", "go fishing", 12, number)

        self.harness._settle_audience_demand({"agenda": [
            {"goal": "Water the potato", "slot": "morning", "category": "farming"}]})
        self.assertEqual("missed", self.harness.notebook.data["audience"]["recent"][0]["status"])
        self.assertIsNone(self.harness.notebook.audience_demand())

        self.harness.notebook.set_audience_demand("def456", "visit the mines", 9, number)
        self.harness.notebook.set_agenda(number, [
            {"goal": "Walk to the mine entrance", "slot": "morning", "category": "mining",
             "success_condition": "location is Mountain", "source": "chat:def456"}], "Underground")
        self.harness._settle_audience_demand({"agenda": [{"goal": "Walk to the mine entrance", "slot": "morning",
                                                          "category": "mining", "source": "chat:def456"}]})
        self.assertEqual("bound", self.harness.notebook.audience_demand()["status"])

        item = self.harness.notebook.data["days"][str(number)]["agenda"][0]
        self.harness.notebook.mark(item["id"], "deferred", "The path was blocked.")
        self.assertIsNone(self.harness.notebook.audience_demand())
        retired = self.harness.notebook.data["audience"]["recent"][0]
        self.assertEqual("failed", retired["status"])
        self.assertEqual("The path was blocked.", retired["note"])

    def test_save_requires_event_identity_date_and_settled_world(self):
        self.finish()
        for change in ({"saveCount":0}, {"saveId":"Other"}, {"nightActive":True}, {"playerFree":False},
                       {"menu":"SaveGameMenu"}, {"location":"Farm"}, {"day":28,"season":"spring"}):
            self.assertFalse(self.harness._checkpoint_if_saved({**MORNING, **change}), change)
        folder = self.disk_save()
        self.assertTrue(self.harness._checkpoint_if_saved(MORNING))
        self.assertEqual("finish_saved", self.harness.stop_reason)
        self.assertEqual("Explore town", self.harness.ledger.snapshot()["active"]["goal"])
        manifest = self.harness.checkpoints.restore()
        self.assertEqual("summer", manifest["date"]["season"])
        original_state = (self.harness.state_directory / "objectives.json").read_bytes()
        (folder / STATE["saveId"]).write_text("changed progress")
        with self.assertRaisesRegex(ValueError, "changed after"):
            self.harness.checkpoints.restore()
        self.assertEqual(original_state, (self.harness.state_directory / "objectives.json").read_bytes())

    def test_failed_disk_verification_never_claims_checkpoint_saved(self):
        self.finish()
        self.assertTrue(self.harness._checkpoint_if_saved(MORNING))
        self.assertEqual("finish_checkpoint_failed", self.harness.stop_reason)
        self.assertEqual("finishing", self.harness.operator_mode)
        self.assertNotIn("checkpoint", self.harness.control.status())

    def test_finish_restores_open_interest_without_inventing_completion_condition(self):
        self.harness.ledger.pursue_interest("Look around the forest", "Follow an interesting path")
        self.finish()
        self.disk_save()
        self.assertTrue(self.harness._checkpoint_if_saved(MORNING))
        active = self.harness.ledger.snapshot()["active"]
        self.assertEqual("curiosity", active["kind"])
        self.assertIsNone(active["success_condition"])
        restored = self.harness.checkpoints.restore()
        self.assertEqual("summer", restored["date"]["season"])

    def test_retrying_finish_after_restart_keeps_original_intention(self):
        self.finish()
        from autoplay_harness.objectives import ObjectiveLedger
        self.harness.ledger = ObjectiveLedger(self.harness.ledger.path)
        self.harness.operator_mode = "playing"
        self.finish()
        self.assertEqual("Explore town", self.harness.finish_previous_objective["goal"])
        self.assertNotIn("resume_objective", self.harness.ledger.snapshot()["active"])
        self.disk_save()
        self.assertTrue(self.harness._checkpoint_if_saved(MORNING))
        self.assertEqual("Explore town", self.harness.ledger.snapshot()["active"]["goal"])

    def test_retrying_finish_without_a_previous_goal_leaves_no_save_goal(self):
        self.harness.ledger.complete_objective("Setup complete")
        self.finish()
        self.harness.operator_mode = "playing"
        self.finish()
        self.assertIsNone(self.harness.finish_previous_objective)
        self.disk_save()
        self.assertTrue(self.harness._checkpoint_if_saved(MORNING))
        self.assertIsNone(self.harness.ledger.snapshot()["active"])

    def test_finish_retry_limit_stops_for_assistance_without_deferring_save(self):
        self.finish()
        decision = ToolDecision("travel_to", {"destination":"FarmHouse"}, {}, None)
        for _ in range(3):
            self.harness._track_action_retries(decision, {"status":"blocked"}, STATE, STATE)
        self.assertEqual("finish_needs_attention", self.harness.stop_reason)
        self.assertIn("save", self.harness.ledger.snapshot()["active"]["goal"])

    def test_finish_does_not_make_more_model_calls_after_budget_is_spent(self):
        self.finish()
        self.harness.budget_usd = 0
        self.harness._actor_step = Mock()
        self.harness._finish_save_step({**STATE, "location":"Town"}, Mock())
        self.harness._actor_step.assert_not_called()
        self.assertEqual("budget_reached", self.harness.stop_reason)

    def test_late_director_usage_does_not_replace_saved_stop_reason(self):
        self.harness.operator_mode = "saved"
        self.harness.stop_reason = "finish_saved"
        self.harness.budget_usd = 0
        self.harness._record_decision("director", ToolDecision("continue_objective", {}, {"cost":0.1}, None), 1, applied=False)
        self.assertEqual("finish_saved", self.harness.stop_reason)
        self.assertEqual(0.1, self.harness.telemetry.summary["usage"]["cost"])

    def test_forever_does_not_restart_operator_terminal_states(self):
        for mode in ("saved", "held", "needs_attention"):
            self.harness.operator_mode = mode
            self.harness.run = Mock(return_value="operator-ended")
            self.harness.supervisor.stop_started_game = Mock()
            reason = run_forever(lambda: self.harness, stop_file=self.root / mode / "STOP",
                                 sleep=lambda _: self.fail("must not restart"))
            self.assertEqual("operator-ended", reason)
            self.harness.supervisor.stop_started_game.assert_not_called()

    def test_control_http_acknowledgements_and_stale_cross_origin_rejection(self):
        server = create_server(self.harness.run_directory / "overlay" / "state.json")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        def send(run_id, origin=None):
            request = urllib.request.Request(base + "/commands", data=json.dumps({"run_id":run_id,"kind":"finish_save"}).encode(),
                                             headers={"Content-Type":"application/json", **({"Origin":origin} if origin else {})})
            return urllib.request.urlopen(request)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            send("old-run")
        self.assertEqual(400, caught.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            send(self.harness.run_id, "https://untrusted.example")
        self.assertEqual(403, caught.exception.code)
        with send(self.harness.run_id) as response:
            self.assertEqual("queued", json.load(response)["status"])
        self.assertEqual("playing", self.harness.operator_mode)
        self.harness._poll_operator(STATE)
        with urllib.request.urlopen(base + "/control.json") as response:
            self.assertEqual("received", json.load(response)["last_command"]["status"])


class HistoryCalendarTests(unittest.TestCase):
    def test_unresponsive_pipe_has_a_wall_clock_deadline(self):
        bridge = NamedPipeBridge()
        bridge._handle = 123
        with patch("autoplay_harness.bridge._kernel32") as kernel, patch("autoplay_harness.bridge.time.monotonic", side_effect=[0, 91]):
            kernel.PeekNamedPipe.return_value = True
            with self.assertRaisesRegex(BridgeError, "90 seconds"):
                bridge._read_line()
            kernel.ReadFile.assert_not_called()
            kernel.CloseHandle.assert_called_once_with(123)
        self.assertIsNone(bridge._handle)

    def test_calendar_has_no_season_or_year_collision(self):
        self.assertEqual(calendar_day(STATE) + 1, calendar_day(MORNING))
        self.assertEqual(112, calendar_day(dict(day=28, season="winter", year=1)))
        self.assertEqual(113, calendar_day(dict(day=1, season="spring", year=2)))

    def test_history_incremental_complete_lines_bounded_and_ledger_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "runs" / "earlier"
            run.mkdir(parents=True)
            path = run / "events.jsonl"
            event = {"type":"tool_result", "tool":"travel_to", "result":{"status":"blocked","reason":"Town door"}, "at":"2026-09-03"}
            line = json.dumps(event).encode()
            path.write_bytes(line)
            history = History(root / "runs", root / "state")
            self.assertEqual([], history.search("Town"))
            with path.open("ab") as stream:
                stream.write(b"\n" + (line+b"\n")*9)
            self.assertEqual(5, len(history.search("Town")))
            results = history.search("Town", limit=2)
            self.assertEqual(results, history.search("Town", limit=2))
            self.assertTrue(all(len(row["excerpt"]) <= 600 for row in results))
            write_json(root / "state" / "objectives.json", {"active":{"id":"objective-1", "goal":"Explore Town"},"history":[]})
            self.assertEqual(1, len(history.search("Town", "objectives")))
            write_json(root / "state" / "objectives.json", {"active":None,"history":[]})
            self.assertEqual([], history.search("Town", "objectives"))
            with self.assertRaises(ValueError):
                history.search("Town", limit=6)
