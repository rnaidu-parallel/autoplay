import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from autoplay_harness.report import summarize


root = Path(__file__).resolve().parents[2]
output = {}
for label, run_id, provider in (
    ("gemini_3_7_flash_low", "d3de4020-bfa6-421a-9b9b-5c020a9df6b1", "Google AI Studio"),
    ("gpt_5_6_luna_low", "10508e09-2055-4a4b-9bb8-e0306aa64f86", "OpenAI"),
):
    path = root / "harness/runs" / run_id / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    row = summarize(path)
    row["run_id"] = run_id
    start = next(e for e in events if e["type"] == "director_review_started")
    results = [e for e in events if e["type"] == "tool_result"]
    row["autonomous_seconds"] = round((datetime.fromisoformat(results[-1]["at"]) - datetime.fromisoformat(start["at"])).total_seconds(), 3)
    attempts = [a for e in events for a in e.get("attempts", [])]
    row["attempt_providers"] = dict(Counter(a.get("provider") for a in attempts))
    assert attempts and all(a.get("provider") == provider for a in attempts)
    assert row["verified_seed_plantings"] == 5
    assert row["initial_goal_evaluation"]["satisfied_at_start"] is False
    assert row["initial_goal_evaluation"]["satisfied_at_end"] is True
    first = next(e["state"] for e in events if e["type"] == "observation" and e["state"].get("worldReady"))
    row["initial_state"] = {k: first.get(k) for k in ("location", "day", "time", "stamina", "health", "money", "inventoryCounts")}
    row["planting_results"] = [{"status": e["result"]["status"], "reason": e["result"].get("reason"), "tiles": e["result"].get("tiles_planted"), "controls": e["result"].get("controls_executed"), "bridge_ms": e["bridge_ms"]} for e in results if e["tool"] == "plant_seeds"]
    row["bootstrap_model_calls"] = sum(e["type"] in {"actor_decision", "director_decision", "model_error"} for e in events[:events.index(start)])
    output[label] = row
assert len({json.dumps(row["initial_state"], sort_keys=True) for row in output.values()}) == 1
Path(__file__).with_name("metrics.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
for label, row in output.items():
    print(label, json.dumps({k: row[k] for k in ("run_id", "autonomous_seconds", "actor_model_ms", "cost_usd", "cache_hit_rate", "attempt_providers", "physical_attempts", "final_state", "planting_results")}))
