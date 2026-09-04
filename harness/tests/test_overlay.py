import json
import threading
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from autoplay_harness.overlay import OverlayState, _state_directory, action_summary, argument_gist, atomic_write_json, build_state, create_server
from autoplay_harness.openrouter import OpenRouterClient
from autoplay_harness.telemetry import Telemetry


class OverlayStateTests(unittest.TestCase):
    def test_provider_reported_cost_counts_retries_cache_and_fallback_once(self):
        # A rejected DeepInfra response still costs money; NextBit supplies the accepted retry.
        # Reported charges already include cache discounts. Never recalculate at one provider's rate.
        retries = OpenRouterClient._merge_usage({}, {"cost": 0.00008125, "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 500}, "completion_tokens": 100})
        retries = OpenRouterClient._merge_usage(retries, {"cost": 0.000062, "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 900}, "completion_tokens": 40})
        events = [
            {"type": "actor_decision", "tool": "press", "usage": retries,
             "attempts": [{"provider": "DeepInfra", "usage": {"cost": 0.00008125}}, {"provider": "NextBit", "usage": {"cost": 0.000062}}]},
            {"type": "director_decision", "applied": False, "usage": {"cost": 0.00001975}},
            {"type": "model_error", "usage": {"cost": 0.000004}},
        ]
        overlay = OverlayState("cost-test")
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Telemetry(Path(directory))
            for event in events:
                telemetry.record(event["type"], event)
                overlay.apply(event)
            self.assertAlmostEqual(0.000167, overlay.snapshot()["stats"]["cost"], places=10)
            self.assertEqual(telemetry.summary["usage"]["cost"], overlay.snapshot()["stats"]["cost"])
            self.assertEqual(0.7, overlay.snapshot()["stats"]["cacheHitRate"])

    def setUp(self) -> None:
        self.start = "2026-09-03T10:00:00+00:00"
        self.now = datetime(2026, 9, 3, 10, 0, 5, tzinfo=timezone.utc)

    def test_audience_panel_reports_the_outcome_after_the_demand_retires(self) -> None:
        overlay = OverlayState("audience-test")
        self.assertIsNone(overlay.snapshot()["audience"])

        overlay.update_files(notebook={"audience": {"demand": {
            "id": "abc123", "goal": "go fishing at the beach", "support": 12,
            "day": 25, "status": "pending", "note": None}, "recent": []}})
        panel = overlay.snapshot()["audience"]
        self.assertEqual(("go fishing at the beach", 12, "pending"),
                         (panel["goal"], panel["support"], panel["status"]))

        # A retired demand still shows: chat is told what became of what it asked for.
        overlay.update_files(notebook={"audience": {"demand": None, "recent": [{
            "id": "abc123", "goal": "go fishing at the beach", "support": 12,
            "day": 25, "status": "failed", "note": "The path was blocked."}]}})
        panel = overlay.snapshot()["audience"]
        self.assertEqual(("failed", "The path was blocked."), (panel["status"], panel["note"]))

    def test_thinking_acting_running_and_action_outcome(self) -> None:
        state = OverlayState("run-1")
        state.apply({"at": self.start, "step": 0, "type": "session_started", "model": "test-model"})
        state.apply({"at": "2026-09-03T10:00:01+00:00", "step": 1, "type": "model_request", "role": "actor"})
        thinking = state.snapshot(now=self.now)
        self.assertEqual("thinking", thinking["status"])
        self.assertEqual("actor", thinking["thinking"]["role"])
        self.assertEqual(4000, thinking["thinking"]["sinceMs"])

        state.apply({
            "at": "2026-09-03T10:00:02+00:00",
            "step": 1,
            "type": "actor_decision",
            "tool": "water_crops",
            "arguments": {"tiles": [[1, 2], [2, 2], [3, 2], [4, 2]], "say": "I am giving these thirsty sprouts a good drink."},
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 40},
                "cost": 0.012345,
            },
        })
        self.assertEqual("acting", state.snapshot(now=self.now)["status"])

        state.apply({
            "at": "2026-09-03T10:00:03+00:00",
            "step": 1,
            "type": "tool_result",
            "tool": "water_crops",
            "result": {
                "status": "blocked",
                "reason": "door_closed_until_900",
                "state": {"day": 18, "time": 710, "location": "Farm", "stamina": 250, "money": 80},
            },
        })
        running = state.snapshot(now=self.now)
        self.assertEqual("running", running["status"])
        self.assertEqual("Watering 4 crops", running["actions"][0]["summary"])
        self.assertEqual("water_crops", running["actions"][0]["toolName"])
        self.assertEqual("I am giving these thirsty sprouts a good drink.", running["actions"][0]["say"])
        self.assertEqual("I am giving these thirsty sprouts a good drink.", running["speech"]["say"])
        self.assertEqual("blocked: door closed until 9:00 AM", running["actions"][0]["outcome"])
        self.assertEqual("7:10 AM", running["game"]["time"])
        self.assertEqual(1, running["stats"]["decisions"])
        self.assertEqual(0.4, running["stats"]["cacheHitRate"])
        self.assertEqual(0.012345, running["stats"]["cost"])

    def test_fatal_exit_clears_pending_work_and_shows_stopped(self) -> None:
        state = OverlayState("run-1")
        state.apply({"at": self.start, "type": "model_request", "role": "actor"})
        state.apply({"at": self.start, "type": "fatal_error", "error": "context overflow"})
        snapshot = state.snapshot(now=self.now)
        self.assertEqual("stopped", snapshot["status"])
        self.assertEqual({}, state.pending_requests)
        self.assertEqual("fatal error", state.stop_reason)

    def test_action_keeps_observed_game_time_when_result_advances_clock(self) -> None:
        state = OverlayState("run-1")
        state.apply({"type": "observation", "state": {"time": 850}})
        state.apply({"type": "actor_decision", "step": 1, "tool": "travel_to"})
        state.apply({"type": "tool_result", "step": 1, "tool": "travel_to",
                     "result": {"status": "completed", "state": {"time": 910}}})
        snapshot = state.snapshot(now=self.now)
        self.assertEqual("9:10 AM", snapshot["game"]["time"])
        self.assertEqual("8:50 AM", snapshot["actorActions"][0]["gameTime"])
        self.assertEqual("8:50 AM", snapshot["actions"][0]["gameTime"])

    def test_session_stats_include_discarded_director_and_failed_calls(self) -> None:
        state = OverlayState("run-1")
        state.apply({"at": self.start, "type": "session_started"})
        state.apply({"type": "actor_decision", "tool": "inspect_scene",
                     "usage": {"prompt_tokens": 100, "cost": 0.01,
                               "prompt_tokens_details": {"cached_tokens": 40}}})
        state.apply({"type": "director_decision", "applied": False,
                     "usage": {"prompt_tokens": 80, "cost": 0.02,
                               "prompt_tokens_details": {"cached_tokens": 60}}})
        state.apply({"type": "model_error", "role": "actor",
                     "usage": {"prompt_tokens": 20, "cost": 0.005}})
        state.apply({"type": "model_request", "role": "actor", "at": self.start})
        state.apply({"type": "session_stopped", "at": "2026-09-03T10:00:03+00:00", "reason": "done"})
        stats = state.snapshot(now=self.now)["stats"]
        self.assertEqual(3, stats["modelCalls"])
        self.assertEqual(200, stats["inputTokens"])
        self.assertEqual(0.5, stats["cacheHitRate"])
        self.assertAlmostEqual(0.035, stats["cost"])
        self.assertEqual(3, stats["uptimeSeconds"])
        self.assertEqual(0, OverlayState("new-run").snapshot(now=self.now)["stats"]["modelCalls"])

    def test_speech_survives_director_updates_and_resets_for_a_new_run(self) -> None:
        state = OverlayState("run-1")
        for line in ("The river looks peaceful.", "I wonder who lives up this path."):
            state.apply({"type": "actor_decision", "tool": "inspect_scene", "arguments": {"say": line}})
        for _ in range(12):
            state.apply({"type": "director_decision", "tool": "continue_objective", "arguments": {"milestone": "Explore"}})
        state.apply({"type": "actor_decision", "tool": "inspect_scene", "applied": False,
                     "arguments": {"say": "Discarded line."}})
        snapshot = state.snapshot(now=self.now)
        self.assertEqual(8, len(snapshot["actions"]))
        self.assertEqual({"say": "I wonder who lives up this path.", "previous": "The river looks peaceful."}, snapshot["speech"])
        self.assertEqual({"say": None, "previous": None}, OverlayState("run-2").snapshot(now=self.now)["speech"])

    def test_build_state_maps_notebook_objective_world_and_lessons(self) -> None:
        notebook = {
            "days": {
                "17": {"theme": "Yesterday", "agenda": [], "reflection": "Paths need clearing."},
                "18": {
                    "theme": "Plant and explore",
                    "agenda": [{"id": "d18-1", "goal": "Water crops", "slot": "morning", "status": "active"}],
                    "reflection": None,
                },
            },
            "learned": ["Old fact", "Newest fact"],
        }
        objectives = {"active": {
            "goal": "Grow the farm", "milestone": "Water four crops",
            "success_condition": "wateredCrops >= 4", "agenda_id": "d18-1",
        }}
        world = {"visited": {"Farm": 1, "Town": 17}, "last_location": "Town", "totalCount": 87,
                 "unvisitedNearby": ["Mountain", "Forest", "Beach", "Mine", "Desert"],
                 "routeHome": ["BusStop", "Farm", "FarmHouse"]}
        overlay = build_state(
            [{"at": self.start, "step": 0, "type": "observation", "state": {"day": 18, "location": "Farm"}}],
            run_id="synthetic", objectives=objectives, notebook=notebook, world=world, now=self.now,
        )
        self.assertEqual("Plant and explore", overlay["agenda"]["theme"])
        self.assertEqual("d18-1", overlay["agenda"]["items"][0]["id"])
        self.assertEqual("Paths need clearing.", overlay["agenda"]["reflectionYesterday"])
        self.assertEqual("Grow the farm", overlay["objective"]["goal"])
        self.assertEqual(["Newest fact", "Old fact"], overlay["lessons"])
        self.assertEqual(2, overlay["world"]["visitedCount"])
        self.assertEqual(4, len(overlay["world"]["unvisitedNearby"]))

    def test_latest_actions_keep_outcomes_after_director_updates_and_operator_finish(self) -> None:
        state = OverlayState("run-1")
        for step in (1, 2, 3):
            state.apply({"type": "actor_decision", "step": step, "tool": "navigate_to",
                         "arguments": {"say": f"Visit {step}"}})
        for step in range(4, 14):
            state.apply({"type": "director_decision", "step": step, "tool": "continue_objective"})
        state.apply({"type": "tool_result", "step": 3, "tool": "navigate_to", "result": {"status": "completed"}})
        actions = state.snapshot(now=self.now)["actorActions"]
        self.assertEqual(["Visit 3", "Visit 2", "Visit 1"], [a["say"] for a in actions])
        self.assertEqual("completed", actions[0]["outcome"])
        state.apply({"type": "tool_result", "step": 14, "source": "operator_finish",
                     "tool": "go_home_and_sleep", "result": {"status": "completed"}})
        self.assertEqual("operator", state.snapshot(now=self.now)["actorActions"][0]["role"])
        self.assertEqual([], OverlayState("new-run").snapshot(now=self.now)["actorActions"])

    def test_named_tools_have_short_human_summaries(self) -> None:
        self.assertEqual("Walking to Mountain", action_summary("travel_to", {"destination": "Mountain"}))
        self.assertEqual("Heading home to sleep", action_summary("go_home_and_sleep"))
        self.assertEqual("Planning the day: Spring cleanup", action_summary("plan_day", {"theme": "Spring cleanup"}))
        self.assertEqual("New goal: Meet Robin", action_summary("set_objective", {"goal": "Meet Robin"}))
        self.assertEqual("Taking a closer look", action_summary("inspect_scene"))

    def test_argument_gist_and_director_card(self) -> None:
        self.assertEqual("(1,2) (3,4) (5,6) +1", argument_gist({"tiles": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}, {"x": 7, "y": 8}]}))
        self.assertEqual("(61,20)", argument_gist({"tile_x": 61, "tile_y": 20}))
        self.assertEqual("W+LeftShift", argument_gist({"buttons": ["W", "LeftShift"]}))
        self.assertEqual("×4", argument_gist({"count": 4}))
        overlay = build_state([{
            "at": self.start, "step": 1, "type": "director_decision", "tool": "set_objective",
            "arguments": {"goal": "Meet Robin"},
        }], now=self.now)
        self.assertEqual("Director: Meet Robin", overlay["actions"][0]["summary"])


class OverlayIOTests(unittest.TestCase):
    def test_farmer_name_is_exposed_to_overlay(self) -> None:
        state = build_state([{"type": "observation", "state": {"playerName": "Neon"}}])
        self.assertEqual("Neon", state["game"]["playerName"])

    def test_isolated_run_state_overrides_shared_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            isolated = run / "state"
            shared = root / "shared"
            isolated.mkdir(parents=True)
            shared.mkdir()
            self.assertEqual(shared, _state_directory(run, shared))
            for name in ("objectives", "notebook", "world"):
                (isolated / f"{name}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(isolated, _state_directory(run, shared))

    def test_atomic_write_is_valid_json(self) -> None:
        path = Path(__file__).resolve().parents[1] / f".overlay-state-{uuid.uuid4()}.json"
        try:
            atomic_write_json(path, {"status": "thinking", "actions": []})
            atomic_write_json(path, {"status": "running", "actions": [{"tool": "wait"}]})
            self.assertEqual("running", json.loads(path.read_text(encoding="utf-8"))["status"])
            self.assertFalse(path.with_suffix(".tmp").exists())
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)

    def test_http_handler_serves_state_and_page_on_localhost(self) -> None:
        root = Path(__file__).resolve().parents[1]
        token = uuid.uuid4()
        state_path = root / f".overlay-http-{token}.json"
        index_path = root / f".overlay-http-{token}.html"
        server = None
        thread = None
        try:
            atomic_write_json(state_path, {"status": "running", "session": {"runId": "http-test"}})
            index_path.write_text("<!doctype html><title>Overlay test</title>", encoding="utf-8")
            server = create_server(state_path, 0, index_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/state.json", timeout=2) as response:
                self.assertEqual("http-test", json.load(response)["session"]["runId"])
                self.assertEqual("application/json; charset=utf-8", response.headers["Content-Type"])
            with urlopen(f"{base}/", timeout=2) as response:
                self.assertIn(b"Overlay test", response.read())
                self.assertEqual("text/html; charset=utf-8", response.headers["Content-Type"])
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
            state_path.unlink(missing_ok=True)
            state_path.with_suffix(".tmp").unlink(missing_ok=True)
            index_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
