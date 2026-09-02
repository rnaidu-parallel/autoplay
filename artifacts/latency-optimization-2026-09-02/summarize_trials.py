import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from autoplay_harness.report import summarize

root = Path(__file__).resolve().parents[2]
output = {}
for label, run in (
    ("glm_previous", "b09c3d61-574f-4d89-ba4e-84628c45cef6"),
    ("luna_previous", "10508e09-2055-4a4b-9bb8-e0306aa64f86"),
    ("state_pilot", "f15aea7c-2a9f-496c-9f14-f01d91f307ef"),
    ("count_skill_pilot", "eadebe1f-a25b-43db-b466-2167cf7d449b"),
    ("final_luna", "2f7e36d4-0a7c-496e-bad5-5801154d42a1"),
):
    path = root / "harness/runs" / run / "events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    row = summarize(path)
    row["run_id"] = run
    first = next(e for e in events if e["type"] == "observation" and e["state"].get("worldReady"))
    results = [e for e in events if e["type"] == "tool_result"]
    row["ready_observation_to_completion_seconds"] = round((datetime.fromisoformat(results[-1]["at"]) - datetime.fromisoformat(first["at"])).total_seconds(), 3)
    row["actor_calls"] = [{k: e.get(k) for k in ("tool", "arguments", "model_ms", "usage", "attempts")} for e in events if e["type"] == "actor_decision"]
    row["planting_controls"] = [c for e in results for c in e.get("result", {}).get("control_timings", [])]
    assert row["verified_seed_plantings"] == 5 and row["initial_goal_evaluation"]["satisfied_at_end"]
    output[label] = row
replay = json.loads(Path(__file__).with_name("replay.json").read_text())
output["replay"] = {
    "calls": len(replay), "mean_model_ms": statistics.mean(r["model_ms"] for r in replay),
    "mean_after_first_ms": statistics.mean(r["model_ms"] for r in replay[1:]),
    "correct_intents": sum(r["correct_intent"] for r in replay),
    "cache_hit_rate": sum(r["usage"]["prompt_tokens_details"]["cached_tokens"] for r in replay) / sum(r["usage"]["prompt_tokens"] for r in replay),
    "after_first_cache_hit_rate": sum(r["usage"]["prompt_tokens_details"]["cached_tokens"] for r in replay[1:]) / sum(r["usage"]["prompt_tokens"] for r in replay[1:]),
    "cost_usd": sum(r["usage"]["cost"] for r in replay),
    "providers": dict(Counter(r["provider"] for r in replay)),
}
assert all(r["provider"] == "OpenAI" and r["correct_intent"] for r in replay)
Path(__file__).with_name("metrics.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
for label, row in output.items():
    print(label, json.dumps(row if label == "replay" else {k: row[k] for k in (
        "actor_model_ms", "actor_cycle_ms", "ready_observation_to_completion_seconds", "cost_usd", "cache_hit_rate", "cache_by_role", "planting_controls")}))
