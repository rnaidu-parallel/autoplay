from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Telemetry:
    def __init__(self, run_directory: Path, recent_limit: int = 12) -> None:
        run_directory.mkdir(parents=True, exist_ok=True)
        self.events_path = run_directory / "events.jsonl"
        self.summary_path = run_directory / "session-summary.json"
        self.recent: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self.step = 0
        self.summary: dict[str, Any] = {
            "accomplishments": [],
            "failures": [],
            "learned": [],
            "unresolved": [],
            "usage": {
                "calls": 0,
                "prompt_tokens": 0,
                "cached_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
            },
        }

    def record(self, event_type: str, payload: dict[str, Any], include_recent: bool = True) -> None:
        event = {"at": datetime.now(timezone.utc).isoformat(), "step": self.step, "type": event_type, **payload}
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        if include_recent and event.get("applied") is not False:
            self.recent.append(event)
        if self._compact(event):
            self.save_summary()

    def context(self) -> dict[str, Any]:
        episode_summary = {key: value for key, value in self.summary.items() if key != "usage"}
        return {
            "recent_events": [self._prompt_event(event) for event in self.recent],
            "episode_summary": episode_summary,
        }

    def save_summary(self) -> None:
        self.summary_path.write_text(json.dumps(self.summary, indent=2, ensure_ascii=False), encoding="utf-8")

    def _compact(self, event: dict[str, Any]) -> bool:
        event_type = event["type"]
        changed = self._compact_usage(event)
        if event_type == "actor_decision":
            tool = event.get("tool")
            arguments = event.get("arguments", {})
            if tool == "objective_progress":
                return self._remember(
                    "accomplishments",
                    {"note": arguments.get("note", ""), "evidence": arguments.get("evidence", "")},
                ) or changed
            if tool == "record_opportunity":
                return self._remember(
                    "unresolved",
                    {"note": arguments.get("note", ""), "reason": arguments.get("reason", "")},
                ) or changed
        if event_type == "objective_completed":
            return self._remember("accomplishments", {"objective_evidence": event.get("evidence", "")}) or changed
        if event_type == "director_decision" and event.get("applied") is not False:
            tool = event.get("tool")
            arguments = event.get("arguments", {})
            if tool == "block_objective":
                return self._remember("failures", {"objective_blocker": arguments.get("evidence", "")}) or changed
        if event_type == "tool_result":
            result = event.get("result", {})
            status = result.get("status") if isinstance(result, dict) else None
            if status in {"error", "timeout"}:
                return self._remember(
                    "failures",
                    {"tool": event.get("tool", ""), "status": status, "reason": result.get("reason", "")},
                ) or changed
            if event.get("tool") == "wiki_search" and isinstance(result, dict):
                titles = [item.get("title", "") for item in result.get("results", [])]
                for title in titles:
                    changed = self._remember("learned", title) or changed
                return changed
        return changed

    def _compact_usage(self, event: dict[str, Any]) -> bool:
        if event.get("type") not in {"actor_decision", "director_decision", "model_error"}:
            return False
        usage = event.get("usage") or {}
        totals = self.summary["usage"]
        totals["calls"] += 1
        totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        totals["cached_tokens"] += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        totals["cost"] += float(usage.get("cost") or 0)
        prompt_tokens = totals["prompt_tokens"]
        totals["cache_hit_rate"] = totals["cached_tokens"] / prompt_tokens if prompt_tokens else 0
        return True

    @staticmethod
    def _prompt_event(event: dict[str, Any]) -> dict[str, Any]:
        compact = {"type": event.get("type")}
        if "tool" in event:
            compact["tool"] = event["tool"]
        if "arguments" in event:
            compact["arguments"] = event["arguments"]
        result = event.get("result")
        if isinstance(result, dict):
            compact_result = {
                key: result[key]
                for key in ("status", "error", "warning", "reason", "movement_changed")
                if key in result
            }
            state = result.get("state")
            if isinstance(state, dict):
                compact_result["state"] = {
                    key: state[key]
                    for key in (
                        "worldReady",
                        "playerFree",
                        "menu",
                        "dialogueText",
                        "location",
                        "tileX",
                        "tileY",
                        "pixelX",
                        "pixelY",
                        "facing",
                        "toolbarIndex",
                        "tool",
                        "usingTool",
                        "stamina",
                        "time",
                        "day",
                    )
                    if key in state
                }
            compact["result"] = compact_result
        for key in ("reason", "evidence"):
            if key in event:
                compact[key] = event[key]
        return compact

    def _remember(self, category: str, item: dict[str, Any] | str) -> bool:
        if category == "learned":
            item = str(item)[:160]
        if item in self.summary[category]:
            return False
        self.summary[category].append(item)
        del self.summary[category][:-(8 if category == "learned" else 12)]
        return True
