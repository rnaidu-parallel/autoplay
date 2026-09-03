import tempfile
import unittest
from pathlib import Path

from autoplay_harness.telemetry import Telemetry


class TelemetryTests(unittest.TestCase):
    def test_compacts_durable_progress_and_bounds_recent_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Telemetry(Path(directory), recent_limit=2)
            telemetry.record(
                "actor_decision",
                {
                    "tool": "objective_progress",
                    "arguments": {"note": "Planted seed", "evidence": "Seed visible"},
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "cost": 0,
                        "prompt_tokens_details": {"cached_tokens": 25},
                    },
                },
            )
            telemetry.record("tool_result", {"tool": "wait", "result": {"status": "timeout", "reason": "menu"}})
            telemetry.record("session_stopped", {"reason": "test"})

            context = telemetry.context()
            first_line = (Path(directory) / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn('"step":0', first_line)
            self.assertEqual(2, len(context["recent_events"]))
            self.assertEqual("Planted seed", context["episode_summary"]["accomplishments"][0]["note"])
            self.assertEqual("wait", context["episode_summary"]["failures"][0]["tool"])
            self.assertNotIn("usage", context["episode_summary"])
            self.assertEqual(0.25, telemetry.summary["usage"]["cache_hit_rate"])
            self.assertTrue((Path(directory) / "session-summary.json").exists())

    def test_prompt_context_removes_usage_and_compacts_bridge_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Telemetry(Path(directory))
            telemetry.record(
                "actor_decision",
                {"tool": "press", "arguments": {"buttons": ["W"]}, "usage": {"prompt_tokens": 50}},
            )
            telemetry.record(
                "tool_result",
                {
                    "tool": "press",
                    "result": {
                        "status": "completed",
                        "state": {
                            "location": "Farm",
                            "pixelX": 10,
                            "pixelY": 20,
                            "inventory": [{"name": "Axe"}],
                        },
                    },
                },
            )
            recent = telemetry.context()["recent_events"]
            self.assertNotIn("usage", recent[0])
            self.assertNotIn("inventory", recent[1]["result"]["state"])

    def test_learned_is_capped_and_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Telemetry(Path(directory))
            for index in range(10):
                telemetry.record("tool_result", {"tool": "wiki_search", "result": {"results": [{"title": str(index) + "x" * 200}]}})
            learned = telemetry.context()["episode_summary"]["learned"]
            self.assertEqual(8, len(learned))
            self.assertTrue(all(isinstance(item, str) and len(item) <= 160 for item in learned))


if __name__ == "__main__":
    unittest.main()
