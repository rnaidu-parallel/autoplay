from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ObjectiveError(RuntimeError):
    pass


class ObjectiveLedger:
    def __init__(
        self,
        path: Path,
        initial_goal: str | None = None,
        initial_success_condition: str | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            active = self.data.get("active")
            if initial_goal and active is not None and active.get("goal") != initial_goal:
                active["status"] = "superseded"
                active["evidence"] = "Replaced by the explicit objective supplied when the harness started."
                active["ended_at"] = self._now()
                self.data["history"].append(active)
                self.data["active"] = self._new_objective(initial_goal, initial_success_condition)
                self._save()
        else:
            if not initial_goal:
                raise ObjectiveError("An initial objective is required for a new session.")
            self.data = {
                "version": 1,
                "active": self._new_objective(initial_goal, initial_success_condition),
                "history": [],
                "progress": [],
                "opportunities": [],
            }
            self._save()

    def snapshot(self) -> dict[str, Any]:
        active = self.data.get("active")
        compact_active = dict(active) if active else None
        if compact_active:
            compact_active.pop("last_review_reason", None)
            if len(compact_active.get("milestone", "")) > 1200:
                compact_active["milestone"] = compact_active["milestone"][:1200] + "…"

        history = []
        for objective in self.data.get("history", [])[-3:]:
            history.append(
                {
                    key: objective[key]
                    for key in ("id", "goal", "status", "evidence", "ended_at")
                    if key in objective
                }
            )

        return {
            "version": self.data.get("version", 1),
            "active": compact_active,
            "history": history,
            "progress": self.data.get("progress", [])[-4:],
            "opportunities": self.data.get("opportunities", [])[-4:],
        }

    def record_progress(self, note: str, evidence: str) -> None:
        self.data["progress"].append({"note": note, "evidence": evidence, "at": self._now()})
        self._save()

    def record_opportunity(self, note: str, reason: str) -> None:
        self.data["opportunities"].append({"note": note, "reason": reason, "at": self._now()})
        self._save()

    def continue_objective(self, milestone: str, reason: str) -> None:
        active = self._require_active()
        active["milestone"] = milestone
        active["last_review_reason"] = reason
        active["reviewed_at"] = self._now()
        self._save()

    def complete_objective(self, evidence: str) -> None:
        active = self._require_active()
        active["status"] = "completed"
        active["evidence"] = evidence
        active["ended_at"] = self._now()
        self.data["history"].append(active)
        self.data["active"] = None
        self._save()

    def block_objective(self, evidence: str) -> None:
        active = self._require_active()
        active["status"] = "blocked"
        active["evidence"] = evidence
        active["ended_at"] = self._now()
        self.data["history"].append(active)
        self.data["active"] = None
        self._save()

    def set_objective(
        self,
        goal: str,
        success_condition: str,
        milestone: str,
        agenda_id: str | None = None,
        interruption_reason: str | None = None,
    ) -> None:
        active = self.data.get("active")
        if active is not None and interruption_reason is None:
            raise ObjectiveError("The active objective must be completed or blocked before setting another.")
        if evaluate_state_condition(success_condition, {}) is None:
            raise ObjectiveError(
                "The success condition must contain only structured game-state comparisons."
            )
        if active is not None:
            self.data["history"].append({**active, "status": "interrupted",
                                         "evidence": interruption_reason, "ended_at": self._now()})
        next_id = len(self.data["history"]) + 1
        self.data["active"] = {
            "id": f"objective-{next_id}",
            "goal": goal,
            "success_condition": success_condition,
            "milestone": milestone,
            "status": "active",
            "created_at": self._now(),
        }
        if agenda_id is not None:
            self.data["active"]["agenda_id"] = agenda_id
        self._save()

    def _require_active(self) -> dict[str, Any]:
        active = self.data.get("active")
        if active is None:
            raise ObjectiveError("There is no active objective.")
        return active

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def _new_objective(self, goal: str, success_condition: str | None) -> dict[str, Any]:
        return {
            "id": f"objective-{len(self.data.get('history', [])) + 1}" if hasattr(self, "data") else "objective-1",
            "goal": goal,
            "success_condition": success_condition or "The requested objective is visibly complete.",
            "milestone": "Observe the current game state and choose the first concrete step.",
            "status": "active",
            "created_at": self._now(),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


def evaluate_state_condition(condition: str, state: dict[str, Any]) -> tuple[bool, list[str]] | None:
    clauses = [
        clause.strip()
        for clause in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", condition, flags=re.IGNORECASE)
        if clause.strip()
    ]
    parsed: list[tuple[str, str, str]] = []
    for clause in clauses:
        match = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_' -]+?)?)\s*(is|==|=|!=|>=|<=|>|<)\s*(.+)",
            clause,
        )
        if match is None:
            return None
        parsed.append((match.group(1).strip(), match.group(2), match.group(3).strip().strip("'\"")))

    mismatches: list[str] = []
    for requested_key, operator, expected_text in parsed:
        key, actual = _lookup_state(state, requested_key)
        if key is None:
            mismatches.append(f"{requested_key} is unavailable")
            continue
        expected_folded = expected_text.casefold()
        if expected_folded in {"true", "false"}:
            expected: Any = expected_folded == "true"
        elif expected_folded == "null" or (expected_folded == "none" and actual is None):
            expected = None
        elif expected_folded == "none" and isinstance(actual, str):
            expected = "none"
        else:
            try:
                expected = float(expected_text) if "." in expected_text else int(expected_text)
            except ValueError:
                expected = expected_text

        if operator in {"is", "=", "==", "!="}:
            matches = (
                actual.casefold() == expected.casefold()
                if isinstance(actual, str) and isinstance(expected, str)
                else actual == expected
            )
            if operator == "!=":
                matches = not matches
        elif isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            matches = {
                ">": actual > expected,
                ">=": actual >= expected,
                "<": actual < expected,
                "<=": actual <= expected,
            }[operator]
        else:
            matches = False
        if not matches:
            if operator in {"is", "=", "=="}:
                mismatches.append(f"{key} expected {expected!r} but observed {actual!r}")
            else:
                mismatches.append(f"{key} expected {operator} {expected!r} but observed {actual!r}")
    return not mismatches, mismatches


def _lookup_state(state: dict[str, Any], requested_key: str) -> tuple[str | None, Any]:
    """Resolve `field` or `inventory.Item Name` against structured state, case-insensitively.

    `inventory.<name>` reads `inventoryCounts` and yields 0 for an item the player does not hold,
    so `inventory.Parsnip Seeds >= 20` is a valid, currently false condition.
    """
    base, _, child = requested_key.partition(".")
    if base.casefold() == "inventory" and child:
        base = "inventoryCounts"
    state_keys = {key.casefold(): key for key in state}
    key = state_keys.get(base.casefold())
    if key is None:
        return None, None
    value = state[key]
    if not child:
        return key, value
    if not isinstance(value, dict):
        return None, None
    child_keys = {name.casefold(): name for name in value}
    child_key = child_keys.get(child.strip().casefold())
    if child_key is None:
        return (f"{key}.{child.strip()}", 0) if key == "inventoryCounts" else (None, None)
    return f"{key}.{child_key}", value[child_key]
