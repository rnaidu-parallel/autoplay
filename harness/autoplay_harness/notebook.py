from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SLOTS = ("morning", "midday", "afternoon", "evening")
STATUSES = ("pending", "active", "done", "carried", "dropped")
PURPOSES = ("crops", "trees", "paths", "buildings", "animals", "reserve")


class Notebook:
    def __init__(self, state_directory: Path) -> None:
        self.path = state_directory / "notebook.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"farm_plan": None, "days": {}, "learned": []}
            self._save()
        self.data.setdefault("farm_plan", None)
        self.data.setdefault("days", {})
        self.data.setdefault("learned", [])
        self.current_day: int | None = None
        if self.data["days"]:
            self.current_day = int(next(reversed(self.data["days"])))

    @property
    def farm_plan(self) -> dict[str, Any] | None:
        return self.data["farm_plan"]

    def start_day(self, day: int) -> dict[str, Any]:
        key = str(day)
        if key in self.data["days"]:
            self.current_day = day
            return self.data["days"][key]

        previous_key = str(self.current_day) if self.current_day is not None else None
        carried: list[dict[str, Any]] = []
        if previous_key is not None and previous_key in self.data["days"]:
            previous = self.data["days"][previous_key]
            for item in previous["agenda"]:
                if item["status"] not in {"pending", "active", "carried"}:
                    continue
                item["status"] = "carried"
                carried.append({**item, "status": "carried", "carried_from": previous_key})

        entry = {
            "theme": "",
            "agenda": carried,
            "reflection": None,
            "learned": [],
        }
        self.data["days"][key] = entry
        self.current_day = day
        self._save()
        return entry

    def set_agenda(
        self,
        day: int,
        items: list[dict[str, Any]],
        theme: str,
        dropped: list[dict[str, str]] | None = None,
    ) -> None:
        entry = self._day(day)
        if not isinstance(theme, str) or not theme.strip():
            raise ValueError("theme is required")
        self._validate_items(items)
        dropped = dropped or []
        self._validate_dropped(dropped)

        candidates = {item["id"]: item for item in entry["agenda"] if item["status"] == "carried"}
        preserved = [item for item in entry["agenda"] if item["status"] in {"done", "dropped"}]
        planned: list[dict[str, Any]] = []
        next_number = self._next_item_number(day, preserved)

        for item in items:
            carried_id = item.get("carried_id")
            candidate = candidates.get(carried_id) if carried_id else None
            planned.append({
                "id": candidate["id"] if candidate else f"d{day}-{next_number}",
                "goal": item["goal"].strip(),
                "success_condition": item.get("success_condition"),
                "slot": item["slot"],
                "status": "pending",
                "note": candidate.get("note") if candidate else None,
                "carried_from": candidate.get("carried_from") if candidate else None,
            })
            if candidate is None:
                next_number += 1

        for drop in dropped:
            candidate = candidates.get(drop["id"])
            if candidate is None:
                continue
            planned.append({
                **candidate,
                "status": "dropped",
                "note": drop["reason"].strip(),
            })

        entry["theme"] = theme.strip()
        entry["agenda"] = preserved + planned
        self._save()

    def mark(self, item_id: str, status: str, note: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"invalid agenda status: {status}")
        for entry in reversed(list(self.data["days"].values())):
            item = next((candidate for candidate in entry["agenda"] if candidate["id"] == item_id), None)
            if item is not None:
                item["status"] = status
                if note is not None:
                    item["note"] = note
                self._save()
                return
        raise ValueError(f"unknown agenda item: {item_id}")

    def active_item(self, day: int) -> dict[str, Any] | None:
        return next(
            (item for item in self._day(day)["agenda"] if item["status"] == "active"),
            None,
        )

    def remaining(self, day: int) -> list[dict[str, Any]]:
        return [
            item for item in self._day(day)["agenda"]
            if item["status"] in {"pending", "active", "carried"}
        ]

    def reflect(self, day: int, summary: str, learned: list[str]) -> None:
        entry = self._day(day)
        facts = [fact.strip() for fact in learned if isinstance(fact, str) and fact.strip()]
        entry["reflection"] = summary.strip()
        entry["learned"] = facts
        self.data["learned"] = (self.data["learned"] + facts)[-40:]
        self._save()

    def set_farm_plan(self, zones: list[dict[str, Any]], notes: str, day: int) -> None:
        for zone in zones:
            missing = {"name", "purpose", "x1", "y1", "x2", "y2"} - set(zone)
            if missing:
                raise ValueError(f"farm zone missing {', '.join(sorted(missing))}")
            if zone["purpose"] not in PURPOSES:
                raise ValueError(f"invalid farm zone purpose: {zone['purpose']}")
            if not all(isinstance(zone[key], int) for key in ("x1", "y1", "x2", "y2")):
                raise ValueError("farm zone coordinates must be integers")
            if zone["x1"] > zone["x2"] or zone["y1"] > zone["y2"]:
                raise ValueError("farm zone bounds must be ordered")
        self.data["farm_plan"] = {
            "zones": zones,
            "notes": notes,
            "updated_day": day,
        }
        self._save()

    def in_zone(self, x: int, y: int, purpose: str) -> bool:
        plan = self.farm_plan
        if plan is None:
            return False
        return any(
            zone["purpose"] == purpose
            and zone["x1"] <= x <= zone["x2"]
            and zone["y1"] <= y <= zone["y2"]
            for zone in plan["zones"]
        )

    def context(self, day: int | None) -> dict[str, Any]:
        entry = self.data["days"].get(str(day)) if day is not None else None
        previous = None
        keys = list(self.data["days"])
        if entry is not None and str(day) in keys:
            index = keys.index(str(day))
            if index > 0:
                previous = self.data["days"][keys[index - 1]].get("reflection")
        return {
            "farmPlan": (
                {"zones": self.farm_plan["zones"], "notes": self.farm_plan["notes"]}
                if self.farm_plan is not None else None
            ),
            "today": (
                {
                    "theme": entry["theme"],
                    "agenda": [
                        {key: item[key] for key in ("id", "goal", "slot", "status")}
                        for item in entry["agenda"]
                    ],
                    "carried": [
                        {key: item[key] for key in ("id", "goal", "slot")}
                        for item in entry["agenda"] if item["status"] == "carried"
                    ],
                }
                if entry is not None else {"theme": "", "agenda": [], "carried": []}
            ),
            "yesterday": previous,
            "learned": self.data["learned"][-8:],
        }

    def _day(self, day: int) -> dict[str, Any]:
        key = str(day)
        if key not in self.data["days"]:
            raise ValueError(f"day {day} has not been started")
        return self.data["days"][key]

    @staticmethod
    def _validate_items(items: list[dict[str, Any]]) -> None:
        if not isinstance(items, list):
            raise ValueError("agenda must be a list")
        for item in items:
            missing = {"goal", "slot"} - set(item)
            if missing:
                raise ValueError(f"agenda item missing {', '.join(sorted(missing))}")
            if not isinstance(item["goal"], str) or not item["goal"].strip():
                raise ValueError("agenda goals must be non-empty strings")
            if item["slot"] not in SLOTS:
                raise ValueError(f"invalid agenda slot: {item['slot']}")
            condition = item.get("success_condition")
            if condition is not None and not isinstance(condition, str):
                raise ValueError("success_condition must be a string or null")

    @staticmethod
    def _validate_dropped(dropped: list[dict[str, str]]) -> None:
        if not isinstance(dropped, list):
            raise ValueError("dropped must be a list")
        for item in dropped:
            if not item.get("id") or not item.get("reason", "").strip():
                raise ValueError("each dropped item needs an id and reason")

    @staticmethod
    def _next_item_number(day: int, items: list[dict[str, Any]]) -> int:
        prefix = f"d{day}-"
        numbers = [
            int(item["id"][len(prefix):]) for item in items
            if item["id"].startswith(prefix) and item["id"][len(prefix):].isdigit()
        ]
        return max(numbers, default=0) + 1

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)
