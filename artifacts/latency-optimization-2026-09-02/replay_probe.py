"""Eight official-OpenAI decisions against recorded states; never executes controls."""
import json
import os
import statistics
from pathlib import Path
from types import SimpleNamespace

from autoplay_harness.capture import Frame
from autoplay_harness.openrouter import OpenRouterClient
from autoplay_harness.runner import AutoplayHarness
from autoplay_harness.state_actor import STATE_ACTOR_PROMPT, STATE_ACTOR_TOOLS

root = Path(__file__).resolve().parents[2]
key = os.environ.get("OPENROUTER_API_KEY")
if not key:
    for line in (root / ".env").read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "OPENROUTER_API_KEY":
            key = value.strip().strip('"').strip("'")
events = [json.loads(line) for line in (root / "harness/runs/10508e09-2055-4a4b-9bb8-e0306aa64f86/events.jsonl").read_text().splitlines()]
objective = next(e["initial_objective"] for e in events if e["type"] == "session_started")
states = [next(e["state"] for e in events if e["type"] == "observation" and e["state"].get("location") == location)
          for location in ("FarmHouse", "Farm")]
harness = object.__new__(AutoplayHarness)
harness.ledger = SimpleNamespace(snapshot=lambda: {"version": 1, "active": objective, "history": [], "progress": [], "opportunities": []})
harness.telemetry = SimpleNamespace(context=lambda: {"recent_events": [], "episode_summary": {"accomplishments": [], "failures": [], "learned": [], "unresolved": []}})
harness.latest_wiki_results = []
harness.blocked_movements = set()
harness.last_result = None
harness.director_feedback = None
harness.continuous = False
harness.game_actions = 0
harness.max_actions = 12
harness.max_decisions = 8
harness.client = OpenRouterClient(key or "", OpenRouterClient.LUNA_MODEL, "latency-replay-2026-09-02", reasoning_effort="low")
rows = []
for index in range(8):
    harness.decisions = index
    state = states[index % 2]
    frame = Frame(str(index), 1920, 1080, "", None)
    context = harness._context("actor", state, frame, state_only=True)
    decision, elapsed = harness._choose(STATE_ACTOR_PROMPT, context, frame, STATE_ACTOR_TOOLS, "actor")
    expected = "go_to_location" if state["location"] == "FarmHouse" else "plant_nearest_seeds"
    correct = (decision.name == expected and
               (decision.arguments == {"location": "Farm"} if expected == "go_to_location"
                else decision.arguments == {"seed_slot": 8, "count": 5}))
    row = {"sample": index + 1, "location": state["location"], "model_ms": elapsed,
           "tool": decision.name, "arguments": decision.arguments, "correct_intent": correct,
           "usage": decision.usage, "provider": decision.provider, "attempts": decision.attempts}
    rows.append(row)
    Path(__file__).with_name("replay.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps({k: row[k] for k in ("sample", "model_ms", "tool", "correct_intent", "usage")}), flush=True)
print(json.dumps({"mean_ms": statistics.mean(row["model_ms"] for row in rows),
                  "correct_intents": sum(row["correct_intent"] for row in rows)}), flush=True)
