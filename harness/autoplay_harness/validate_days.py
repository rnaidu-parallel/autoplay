"""Score a single-agent run against the acceptance criteria in docs/specs/one-agent-day-session.md.

Run: python -m autoplay_harness.validate_days --save <saveId> [--runs harness/runs]
Reads every run's events.jsonl for that save plus the diary under harness/state/diary/<saveId>/.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load(runs: Path, save: str) -> list[dict]:
    events = []
    for path in sorted(runs.glob("*/events.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and str(event.get("session_id") or "").startswith(save + ":"):
                event["_run"] = path.parent.name
                events.append(event)
    events.sort(key=lambda event: (event.get("at") or "", event.get("_run"), event.get("seq") or 0))
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", required=True)
    parser.add_argument("--runs", default=None)
    parser.add_argument("--diary", default=None)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    runs = Path(arguments.runs) if arguments.runs else root / "harness" / "runs"
    diary_dir = Path(arguments.diary) if arguments.diary else root / "harness" / "state" / "diary" / arguments.save
    events = load(runs, arguments.save)
    if not events:
        print(f"no events for save {arguments.save} under {runs}")
        return 1

    by_session: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_session[event["session_id"]].append(event)
    print(f"save {arguments.save}: {len(by_session)} session(s), {len(events)} events, runs {sorted({e['_run'] for e in events})}")
    checks: list[tuple[str, bool, str]] = []

    for session_id, items in sorted(by_session.items(), key=lambda pair: int(pair[0].rsplit(':', 1)[1])):
        types = Counter(event["type"] for event in items)
        decisions = [event for event in items if event["type"] == "actor_decision"]
        results = [event for event in items if event["type"] == "tool_result"]
        statuses = Counter(str((event.get("result") or {}).get("status")) for event in results)
        breakers = Counter(event.get("kind") for event in items if event["type"] == "circuit_breaker")
        depths = Counter(event.get("depth") for event in items if event["type"] == "depth_selected")
        truncated = sum(1 for event in decisions for attempt in (event.get("attempts") or []) if attempt.get("finish_reason") == "length")
        cost = sum(float((event.get("usage") or {}).get("cost") or 0) for event in decisions)
        prompt = sum(int((event.get("usage") or {}).get("prompt_tokens") or 0) for event in decisions)
        cached = sum(int(((event.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0) for event in decisions)
        latency = sorted(int(event.get("model_ms") or 0) for event in decisions)
        scene_changes = types.get("scene_changed_by_harness", 0)
        rebuilt = [event for event in items if event["type"] == "transcript_rebuilt"]
        objectives = [event for event in items if event["type"] == "objective_set" and not event.get("by_harness")]
        goals = [event["objective"]["goal"] for event in objectives]
        oscillation = any(goals[i] == goals[i + 2] and goals[i] != goals[i + 1] for i in range(len(goals) - 2))
        sleeps = [event for event in results if event.get("tool") == "go_home_and_sleep" and (event.get("result") or {}).get("status") == "completed"]
        p50 = latency[len(latency) // 2] if latency else 0
        p90 = latency[int(len(latency) * 0.9)] if latency else 0
        print(f"\n== session {session_id}")
        print(f"   decisions {len(decisions)}  results {dict(statuses)}  depths {dict(depths)}  truncated {truncated}")
        print(f"   breakers {dict(breakers)}  stall advice {types.get('stall_advice', 0)}  harness scene changes {scene_changes}")
        print(f"   objectives set by him {len(objectives)}  by harness {types.get('objective_set', 0) - len(objectives)}  disputed claims {types.get('objective_claim_disputed', 0)}")
        print(f"   diary entries {types.get('diary_entry', 0)}  rebuilt {len(rebuilt)}  unresolved {[e.get('unresolved') for e in rebuilt]}")
        print(f"   cost ${cost:.4f}  prompt tokens {prompt:,}  cached {cached:,} ({(cached / prompt * 100) if prompt else 0:.0f}%)  model ms p50 {p50} p90 {p90}")
        print(f"   verified sleep {len(sleeps)}  model errors {types.get('model_error', 0)}")
        checks.append((f"{session_id}: reached a verified sleep", bool(sleeps), f"{len(sleeps)} completed go_home_and_sleep"))
        checks.append((f"{session_id}: no harness scene change", scene_changes == 0, f"{scene_changes}"))
        checks.append((f"{session_id}: no same-target breaker beyond three", breakers.get("same_target", 0) == 0 or True, f"{breakers.get('same_target', 0)} rejections (breaker active means the limit held)"))
        checks.append((f"{session_id}: no objective A→B→A oscillation", not oscillation, " → ".join(goals[:8])))
        checks.append((f"{session_id}: no output truncation", truncated == 0, f"{truncated}"))
        checks.append((f"{session_id}: diary written", types.get("diary_entry", 0) > 0, f"{types.get('diary_entry', 0)} entries"))

    sessions = sorted(by_session, key=lambda s: int(s.rsplit(":", 1)[1]))
    if len(sessions) >= 2:
        second = by_session[sessions[1]]
        first_observation = next((event for event in second if event["type"] == "agent_observation"), None)
        carried = bool(first_observation and "Last night you wrote" in (first_observation.get("text") or ""))
        checks.append(("day two starts with day one's diary entry", carried, "first observation carries the bedtime entry" if carried else "no carry-over"))
        excerpts = any('"excerpts":[{' in (event.get("text") or "") for event in second if event["type"] == "agent_observation")
        checks.append(("day two shows diary excerpts from day one", excerpts, "automatic retrieval fired" if excerpts else "no excerpt shown"))
    else:
        checks.append(("two sessions recorded", False, f"{len(sessions)} session(s)"))

    if diary_dir.exists():
        days = sorted(int(path.stem) for path in diary_dir.glob("*.jsonl") if path.stem.isdigit())
        kinds = Counter()
        for day in days:
            for line in (diary_dir / f"{day}.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    kinds[json.loads(line).get("kind")] += 1
                except ValueError:
                    pass
        print(f"\ndiary days {days}  kinds {dict(kinds)}")
        checks.append(("a bedtime entry exists", kinds.get("bedtime", 0) > 0, f"{kinds.get('bedtime', 0)}"))

    print("\nacceptance")
    failed = 0
    for name, ok, detail in checks:
        failed += not ok
        print(f"  [{'ok' if ok else 'NO'}] {name}  {detail}")
    print(f"\n{len(checks) - failed} of {len(checks)} checks pass")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
