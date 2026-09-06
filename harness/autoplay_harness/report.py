from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .objectives import evaluate_state_condition


def summarize(events_path: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events:
        return {"events": 0}

    def percentiles(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        ordered = sorted(values)
        return {
            "n": len(ordered),
            "mean": round(statistics.mean(ordered), 1),
            "p50": round(statistics.median(ordered), 1),
            "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1),
            "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
            "max": round(ordered[-1], 1),
        }

    stamp = lambda event: datetime.fromisoformat(event["at"])  # noqa: E731
    wall_seconds = (stamp(events[-1]) - stamp(events[0])).total_seconds()
    types = Counter(event["type"] for event in events)

    latency: dict[str, list[float]] = {"actor": [], "director": []}
    previous_observation: dict[str, Any] | None = None
    for event in events:
        if event["type"] == "observation":
            previous_observation = event
        elif event["type"] in {"actor_decision", "director_decision"}:
            role = event["type"].split("_")[0]
            if "model_ms" in event:
                latency[role].append(event["model_ms"])
            elif previous_observation is not None:
                latency[role].append((stamp(event) - stamp(previous_observation)).total_seconds() * 1000)
            previous_observation = None

    results = [event for event in events if event["type"] == "tool_result"]
    statuses = Counter((event.get("result") or {}).get("status") for event in results)
    wasted = sum(count for status, count in statuses.items()
                 if status in {"rejected", "blocked", "timeout", "review_requested", "visual_review_requested"})

    usage_events = [event for event in events if event["type"] in {"actor_decision", "director_decision", "model_error"}]
    attempts = [attempt for event in usage_events for attempt in event.get("attempts", [])]
    prompt_tokens = sum((event.get("usage") or {}).get("prompt_tokens") or 0 for event in usage_events)
    cached_tokens = sum(
        ((event.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        for event in usage_events
    )
    completion_tokens = sum((event.get("usage") or {}).get("completion_tokens") or 0 for event in usage_events)
    cache_by_role = {}
    for role in ("actor", "director"):
        calls = [event for event in usage_events if event["type"] == f"{role}_decision"
                 or (event["type"] == "model_error" and event.get("role") == role)]
        def cache_totals(items):
            prompt = sum((event.get("usage") or {}).get("prompt_tokens") or 0 for event in items)
            cached = sum(((event.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for event in items)
            return prompt, cached
        prompt, cached = cache_totals(calls)
        later_prompt, later_cached = cache_totals(calls[1:])
        cache_by_role[role] = {
            "calls": len(calls), "prompt_tokens": prompt, "cached_tokens": cached,
            "cache_write_tokens": sum(((event.get("usage") or {}).get("prompt_tokens_details") or {}).get("cache_write_tokens") or 0 for event in calls),
            "hit_rate": round(cached / prompt, 3) if prompt else None,
            "after_first_call_hit_rate": round(later_cached / later_prompt, 3) if later_prompt else None,
        }

    # The last allowed action may change the world without another observation event.
    world = []
    for event in events:
        state = event.get("state") if event["type"] == "observation" else (event.get("result") or {}).get("state")
        if state and state.get("worldReady"):
            world.append(state)
    initial_objective = next((event.get("initial_objective") for event in events if event["type"] == "session_started"), None)
    goal_evaluation = None
    if initial_objective and world:
        condition = initial_objective["success_condition"]
        before = evaluate_state_condition(condition, world[0])
        after = evaluate_state_condition(condition, world[-1])
        goal_evaluation = {"condition": condition, "satisfied_at_start": before[0] if before else None,
                           "satisfied_at_end": after[0] if after else None,
                           "final_mismatches": after[1] if after else ["condition_is_not_parseable"]}
    crop_changes = {}
    for location in dict.fromkeys(state.get("location") for state in world):
        local = [state for state in world if state.get("location") == location]
        first, last = local[0].get("plantedCrops"), local[-1].get("plantedCrops")
        if first is not None and last is not None:
            crop_changes[location] = last - first
    game_days = None
    game_hours = None
    if len(world) >= 2 and world[0].get("day") is not None and world[-1].get("day") is not None:
        game_days = (world[-1]["day"] - world[0]["day"]) + ((world[-1].get("time") or 600) - (world[0].get("time") or 600)) / 2000
        start_time = world[0].get("time") or 600
        end_time = world[-1].get("time") or 600
        start_minutes = (start_time // 100) * 60 + start_time % 100
        end_minutes = (end_time // 100) * 60 + end_time % 100
        day_delta = world[-1]["day"] - world[0]["day"]
        seasons = ("spring", "summer", "fall", "winter")
        if day_delta < 0 and world[0].get("season") in seasons and world[-1].get("season") in seasons:
            day_delta += (seasons.index(world[-1]["season"]) - seasons.index(world[0]["season"])) * 28
            day_delta += (world[-1].get("year", 1) - world[0].get("year", 1)) * 112
        game_hours = max(0, day_delta * 24 + (end_minutes - start_minutes) / 60)

    cache_latency = {}
    bridge_values = [event["bridge_ms"] for event in results if "bridge_ms" in event]
    for role in ("actor", "director"):
        calls = [event for event in usage_events if event["type"] == f"{role}_decision"
                 or (event["type"] == "model_error" and event.get("role") == role)]
        prompt_values = [(event.get("usage") or {}).get("prompt_tokens") or 0 for event in calls]
        completion_values = [(event.get("usage") or {}).get("completion_tokens") or 0 for event in calls]
        cached = sum(((event.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for event in calls)
        prompt = sum(prompt_values)
        attempt_latency = [attempt["latency_ms"] for event in calls for attempt in event.get("attempts", [])
                           if isinstance(attempt.get("latency_ms"), (int, float))]
        cost = sum((event.get("usage") or {}).get("cost") or 0 for event in calls)
        cache_latency[role] = {
            "calls": len(calls),
            "prompt_median": statistics.median(prompt_values) if prompt_values else None,
            "prompt_max": max(prompt_values) if prompt_values else None,
            "cached_share": round(cached / prompt, 3) if prompt else None,
            "completion_median": statistics.median(completion_values) if completion_values else None,
            "model_latency_ms": percentiles(attempt_latency),
            "bridge_p50_ms": statistics.median(bridge_values) if role == "actor" and bridge_values else None,
            "cost_per_call": round(cost / len(calls), 6) if calls else None,
            "cost_per_game_hour": round(cost / game_hours, 6) if game_hours else None,
        }
    actor_decisions = [event for event in events if event["type"] == "actor_decision"]
    inspect_share = round(sum(event.get("tool") == "inspect_scene" for event in actor_decisions) / len(actor_decisions), 3) if actor_decisions else None

    director_gaps: list[float] = []
    since_director = 0
    for event in events:
        if event["type"] == "actor_decision":
            since_director += 1
        elif event["type"] == "director_decision":
            director_gaps.append(since_director)
            since_director = 0

    return {
        "events": len(events),
        "wall_minutes": round(wall_seconds / 60, 1),
        "actor_decisions": types.get("actor_decision", 0),
        "actor_model_calls": types.get("actor_decision", 0) + sum(event["type"] == "model_error" and event.get("role") == "actor" for event in events),
        "director_decisions": types.get("director_decision", 0),
        "providers": dict(Counter(event.get("provider") or "unreported" for event in usage_events)),
        "cost_usd": round(sum((event.get("usage") or {}).get("cost") or 0 for event in usage_events), 6),
        "recoverable_errors": types.get("recoverable_error", 0),
        "model_errors": types.get("model_error", 0),
        "physical_attempts": len(attempts),
        "attempt_outcomes": dict(Counter(attempt.get("outcome") for attempt in attempts)),
        "finish_reasons": dict(Counter(attempt.get("finish_reason") for attempt in attempts if "finish_reason" in attempt)),
        "reasoning_tokens": sum(((event.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for event in usage_events),
        "background_reviews_discarded": sum(event.get("applied") is False for event in usage_events),
        "objectives_completed": types.get("objective_completed", 0),
        "initial_goal_evaluation": goal_evaluation,
        "verified_seed_plantings": sum(len((event.get("result") or {}).get("tiles_planted") or [])
                                       for event in results if event.get("tool") in {"plant_seeds", "plant_nearest_seeds"}),
        "crop_change_by_location": crop_changes,
        "final_state": None if not world else {key: world[-1].get(key) for key in (
            "location", "day", "time", "plantedCrops", "wateredCrops", "stamina", "health", "money", "inventoryCounts")},
        "actor_model_ms": percentiles(latency["actor"]),
        "actor_cycle_ms": percentiles([event["actor_cycle_ms"] for event in results if "actor_cycle_ms" in event]),
        "director_model_ms": percentiles(latency["director"]),
        "observe_ms": percentiles([event["observe_ms"] for event in events if "observe_ms" in event]),
        "bridge_ms": percentiles([event["bridge_ms"] for event in results if "bridge_ms" in event]),
        "tool_status": dict(statuses.most_common()),
        "wasted_decision_rate": round(wasted / len(results), 3) if results else None,
        "actor_steps_between_director_reviews_p50": statistics.median(director_gaps) if director_gaps else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_rate": round(cached_tokens / prompt_tokens, 3) if prompt_tokens else None,
        "cache_by_role": cache_by_role,
        "game_days_elapsed": round(game_days, 2) if game_days is not None else None,
        "game_hours_elapsed": round(game_hours, 2) if game_hours is not None else None,
        "wall_hours_per_game_day": round(wall_seconds / 3600 / game_days, 2) if game_days else None,
        "start": None if not world else f"day {world[0].get('day')} {world[0].get('time')} {world[0].get('location')}",
        "end": None if not world else f"day {world[-1].get('day')} {world[-1].get('time')} {world[-1].get('location')}",
        "cache_and_latency": cache_latency,
        "inspect_scene_share": inspect_share,
    }


def render(summary: dict[str, Any]) -> str:
    lines = []
    for key, value in summary.items():
        if key == "cache_and_latency":
            lines.append("Cache and latency")
            for role, metrics in value.items():
                latency = metrics["model_latency_ms"] or {}
                lines.append(
                    f"{role}: calls={metrics['calls']} prompt median/max={metrics['prompt_median']}/{metrics['prompt_max']} "
                    f"cached share={metrics['cached_share']} completion median={metrics['completion_median']} "
                    f"model p50/p95={latency.get('p50')}/{latency.get('p95')} bridge p50={metrics['bridge_p50_ms'] if metrics['bridge_p50_ms'] is not None else 'n/a'} "
                    f"cost/call={metrics['cost_per_call']} cost/game-hour={metrics['cost_per_game_hour']}"
                )
            continue
        if isinstance(value, dict) and "p50" in value:
            value = f"n={value['n']} mean={value['mean']} p50={value['p50']} p90={value['p90']} max={value['max']}"
        elif isinstance(value, dict):
            value = ", ".join(f"{name}={count}" for name, count in value.items())
        lines.append(f"{key:<42} {value}")
    return "\n".join(lines)
