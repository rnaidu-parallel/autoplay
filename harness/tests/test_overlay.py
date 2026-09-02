import json
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from autoplay_harness.overlay import OverlayState, action_summary, atomic_write_json, build_state, create_server


class OverlayStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = "2026-09-03T10:00:00+00:00"
        self.now = datetime(2026, 9, 3, 10, 0, 5, tzinfo=timezone.utc)

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
            "arguments": {"tiles": [[1, 2], [2, 2], [3, 2], [4, 2]]},
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
        self.assertEqual("blocked: door closed until 9:00 AM", running["actions"][0]["outcome"])
        self.assertEqual("7:10 AM", running["game"]["time"])
        self.assertEqual(1, running["stats"]["decisions"])
        self.assertEqual(0.4, running["stats"]["cacheHitRate"])
        self.assertEqual(0.0123, running["stats"]["cost"])

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

    def test_named_tools_have_short_human_summaries(self) -> None:
        self.assertEqual("Walking to Mountain", action_summary("travel_to", {"destination": "Mountain"}))
        self.assertEqual("Heading home to sleep", action_summary("go_home_and_sleep"))
        self.assertEqual("Planning the day: Spring cleanup", action_summary("plan_day", {"theme": "Spring cleanup"}))
        self.assertEqual("New goal: Meet Robin", action_summary("set_objective", {"goal": "Meet Robin"}))
        self.assertEqual("Taking a closer look", action_summary("inspect_scene"))


class OverlayIOTests(unittest.TestCase):
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
