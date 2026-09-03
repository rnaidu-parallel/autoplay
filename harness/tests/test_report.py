import json
import tempfile
import unittest
from pathlib import Path

from autoplay_harness.report import render, summarize


class ReportTests(unittest.TestCase):
    def test_final_tool_state_proves_goal_without_cross_location_crop_credit(self):
        events = [
            {"type": "session_started", "initial_objective": {"success_condition": "location is Farm, plantedCrops >= 5"}},
            {"type": "observation", "state": {"worldReady": True, "location": "FarmHouse", "plantedCrops": 0}},
            {"type": "observation", "state": {"worldReady": True, "location": "Farm", "plantedCrops": 3}},
            {"type": "tool_result", "tool": "plant_seeds", "result": {"status": "completed",
                "tiles_planted": [{"x": 60, "y": 18}, {"x": 61, "y": 18}],
                "state": {"worldReady": True, "location": "Farm", "plantedCrops": 5}}},
            {"type": "session_stopped"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps({"at": f"2026-09-02T00:00:0{i}+00:00", **event})
                                      for i, event in enumerate(events)), encoding="utf-8")
            summary = summarize(path)
        self.assertFalse(summary["initial_goal_evaluation"]["satisfied_at_start"])
        self.assertTrue(summary["initial_goal_evaluation"]["satisfied_at_end"])
        self.assertEqual(2, summary["verified_seed_plantings"])
        self.assertEqual({"FarmHouse": 0, "Farm": 2}, summary["crop_change_by_location"])
        self.assertEqual(5, summary["final_state"]["plantedCrops"])

    def test_summarizes_latency_waste_and_game_time(self) -> None:
        events = [
            {"at": "2026-09-02T00:00:00+00:00", "type": "session_started"},
            {
                "at": "2026-09-02T00:00:01+00:00",
                "type": "observation",
                "observe_ms": 400,
                "state": {"worldReady": True, "day": 8, "time": 600, "location": "FarmHouse"},
            },
            {
                "at": "2026-09-02T00:00:08+00:00",
                "type": "director_decision",
                "tool": "set_objective",
                "usage": {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 100}},
            },
            {
                "at": "2026-09-02T00:00:09+00:00",
                "type": "observation",
                "observe_ms": 300,
                "state": {"worldReady": True, "day": 8, "time": 600, "location": "FarmHouse"},
            },
            {
                "at": "2026-09-02T00:00:15+00:00",
                "type": "actor_decision",
                "tool": "hold",
                "model_ms": 5000,
                "usage": {"prompt_tokens": 1000, "completion_tokens": 50},
            },
            {"at": "2026-09-02T00:00:16+00:00", "type": "tool_result", "tool": "hold", "bridge_ms": 700, "result": {"status": "blocked"}},
            {
                "at": "2026-09-02T00:00:17+00:00",
                "type": "observation",
                "state": {"worldReady": True, "day": 9, "time": 600, "location": "Farm"},
            },
            {"at": "2026-09-02T00:00:23+00:00", "type": "actor_decision", "tool": "press", "model_ms": 7000, "usage": {}},
            {"at": "2026-09-02T00:00:24+00:00", "type": "tool_result", "tool": "press", "result": {"status": "completed"}},
            {"at": "2026-09-02T01:00:00+00:00", "type": "session_stopped"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

            summary = summarize(path)

        self.assertEqual(2, summary["actor_decisions"])
        self.assertEqual(6000, summary["actor_model_ms"]["p50"])
        self.assertEqual(6000, summary["actor_model_ms"]["mean"])
        self.assertEqual(7000, summary["director_model_ms"]["p50"])
        self.assertEqual(0.5, summary["wasted_decision_rate"])
        self.assertEqual(0.05, summary["cache_hit_rate"])
        self.assertEqual(0, summary["cache_by_role"]["actor"]["hit_rate"])
        self.assertEqual(0.1, summary["cache_by_role"]["director"]["hit_rate"])
        self.assertEqual(1.0, summary["game_days_elapsed"])
        self.assertEqual(1.0, summary["wall_hours_per_game_day"])
        self.assertEqual(700, summary["bridge_ms"]["p50"])
        self.assertIn("wasted_decision_rate", render(summary))

    def test_cache_latency_breakdown_uses_attempts_and_game_hours(self) -> None:
        events = [
            {"at": "2026-09-02T00:00:00+00:00", "type": "observation", "state": {"worldReady": True, "day": 1, "time": 600}},
            {"at": "2026-09-02T00:00:01+00:00", "type": "actor_decision", "tool": "inspect_scene", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.02, "prompt_tokens_details": {"cached_tokens": 50}}, "attempts": [{"latency_ms": 200}]},
            {"at": "2026-09-02T00:00:02+00:00", "type": "tool_result", "bridge_ms": 30, "result": {"status": "completed"}},
            {"at": "2026-09-02T00:00:03+00:00", "type": "observation", "state": {"worldReady": True, "day": 1, "time": 700}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            summary = summarize(path)
        self.assertEqual(0.5, summary["cache_and_latency"]["actor"]["cached_share"])
        self.assertEqual(200, summary["cache_and_latency"]["actor"]["model_latency_ms"]["p50"])
        self.assertEqual(1.0, summary["inspect_scene_share"])
        self.assertIn("Cache and latency", render(summary))


if __name__ == "__main__":
    unittest.main()
