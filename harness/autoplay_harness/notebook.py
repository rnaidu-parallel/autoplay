from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


SLOTS = ("morning", "midday", "afternoon", "evening")
STATUSES = ("pending", "active", "done", "carried", "dropped", "deferred")
PURPOSES = ("crops", "trees", "paths", "buildings", "animals", "reserve")
CATEGORIES = (
    "farming", "clearing", "exploring", "social", "shopping", "fishing",
    "mining", "foraging", "crafting", "event", "home",
)


def variety_errors(day: dict[str, Any] | None, items: list[dict[str, Any]], theme: str, mode: str) -> list[str]:
    """Return planning-variety errors without changing notebook state."""
    history = day or {}
    errors: list[str] = []
    for item in items:
        category = item.get("category")
        if category not in CATEGORIES:
            errors.append(f"choose a valid category for {item.get('goal', 'agenda item')}")
    counts = Counter(item.get("category") for item in items)
    yesterday = history.get("previous_category_mix", {})
    top_two = [category for category, _count in sorted(yesterday.items(), key=lambda pair: (-pair[1], pair[0]))[:2]]
    if mode == "morning":
        normalized_theme = theme.strip().casefold()
        if normalized_theme and any(
            normalized_theme == str(value).strip().casefold()
            for value in history.get("recent_themes", [])[-3:]
        ):
            errors.append("choose a theme different from each of the last 3 days")
        for category, count in counts.items():
            if category and count > 3:
                errors.append(f"use no more than 3 {category} items")
        if top_two and len(
            {category for category in counts if category and category not in top_two}
        ) < 2:
            errors.append("add at least 2 categories outside yesterday's top 2 categories")
    if mode == "refill" and top_two and counts[top_two[0]] > 1:
        errors.append(f"keep yesterday's dominant {top_two[0]} category to 1 refill item")
    return errors


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
        self.data.setdefault("lessons", [])
        self.data.setdefault("weeks", [])
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
                if item["status"] not in {"pending", "active", "carried", "deferred"}:
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
        preserved = [item for item in entry["agenda"] if item["status"] in {"done", "dropped", "deferred"}]
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
                "category": item["category"],
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

    def add_lesson(self, key: str, text: str, kind: str, day: int) -> None:
        if kind not in {"auto", "reflection"}:
            raise ValueError("lesson kind must be auto or reflection")
        key, text = key.strip(), text.strip()
        if not key or not text:
            raise ValueError("lesson key and text are required")
        if (existing := next((lesson for lesson in self.data["lessons"] if lesson["key"] == key), None)) is not None:
            existing["count"] += 1
            existing["last_day"] = day
            existing["text"] = text
            self._save()
            return
        if len(self.data["lessons"]) >= 40:
            auto = [lesson for lesson in self.data["lessons"] if lesson["kind"] == "auto"]
            if not auto:
                return
            self.data["lessons"].remove(min(auto, key=lambda lesson: (lesson["count"], lesson["first_day"], lesson["id"])))
        numeric_ids = [int(lesson["id"][1:]) for lesson in self.data["lessons"] if str(lesson.get("id", "")).startswith("l") and str(lesson["id"])[1:].isdigit()]
        self.data["lessons"].append({"id": f"l{max(numeric_ids, default=0) + 1}", "text": text, "kind": kind,
            "count": 1, "first_day": day, "last_day": day, "key": key})
        self._save()

    def top_lessons(self, n: int) -> list[str]:
        return [lesson["text"] for lesson in sorted(self.data["lessons"], key=lambda lesson: (-lesson["count"], -lesson["last_day"], lesson["id"]))[:n]]

    def category_mix(self, day: int) -> dict[str, int]:
        return dict(Counter(item.get("category") for item in self._day(day)["agenda"] if item.get("category")))

    def recent_themes(self, n: int) -> list[str]:
        themes = self.data.get("rolled_themes", []) + [
            entry["theme"] for entry in self.data["days"].values() if entry.get("theme")
        ]
        return themes[-n:] if n > 0 else []

    def variety_errors(self, day: int, items: list[dict[str, Any]], theme: str, mode: str) -> list[str]:
        keys = list(self.data["days"])
        previous = int(keys[keys.index(str(day)) - 1]) if str(day) in keys and keys.index(str(day)) > 0 else None
        prior_themes = self.data.get("rolled_themes", []) + [
            self.data["days"][key].get("theme", "")
            for key in keys if key != str(day) and self.data["days"][key].get("theme")
        ]
        return variety_errors({"recent_themes": prior_themes[-3:], "previous_category_mix": self.category_mix(previous) if previous else {}}, items, theme, mode)

    def rollup_week(self) -> None:
        if self.current_day is None:
            return
        old_keys = sorted((key for key in self.data["days"] if int(key) < self.current_day), key=int)
        if len(old_keys) < 7:
            return
        entries = [self.data["days"][key] for key in old_keys]
        summary = " ".join(entry["reflection"].strip() for entry in entries if entry.get("reflection"))[:600]
        categories = Counter(item["category"] for entry in entries for item in entry["agenda"] if item.get("category"))
        self.data["weeks"].append({"days": [int(old_keys[0]), int(old_keys[-1])], "summary": summary, "categories": dict(categories)})
        self.data["rolled_themes"] = [entry["theme"] for entry in entries if entry.get("theme")][-3:]
        for key in old_keys:
            del self.data["days"][key]
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

    def context(self, day: int | None, role: str = "director") -> dict[str, Any]:
        entry = self.data["days"].get(str(day)) if day is not None else None
        today = entry or {"theme": "", "agenda": []}
        agenda = today["agenda"]
        if role == "actor":
            zones = [] if self.farm_plan is None else [f"{zone['x1']},{zone['y1']}-{zone['x2']},{zone['y2']}" for zone in self.farm_plan["zones"] if zone["purpose"] == "crops"]
            return {"today": {"theme": today["theme"], "agenda": [f"{item['id']}|{item['slot']}|{item['status']}|{item['goal'][:70]}" for item in agenda]}, "cropZones": zones, "lessons": self.top_lessons(3)}
        keys = list(self.data["days"])
        previous = None
        previous_mix: dict[str, int] = {}
        if entry is not None and str(day) in keys and keys.index(str(day)) > 0:
            previous_key = keys[keys.index(str(day)) - 1]
            previous = self.data["days"][previous_key].get("reflection")
            previous_mix = self.category_mix(int(previous_key))
        return {"farmPlan": ({"zones": self.farm_plan["zones"], "notes": self.farm_plan["notes"]} if self.farm_plan is not None else None),
            "today": {"theme": today["theme"], "agenda": [{key: item.get(key) for key in ("id", "goal", "slot", "status", "category")} for item in agenda], "carried": [{key: item.get(key) for key in ("id", "goal", "slot")} for item in agenda if item["status"] == "carried"]},
            "yesterday": previous[:400] if previous else previous, "lessons": self.top_lessons(6), "learned": self.data["learned"][-8:], "categoryMixYesterday": previous_mix, "recentThemes": self.recent_themes(3), "week": self.data["weeks"][-1]["summary"][:300] if self.data["weeks"] else None}

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
            missing = {"goal", "slot", "category"} - set(item)
            if missing:
                raise ValueError(f"agenda item missing {', '.join(sorted(missing))}")
            if not isinstance(item["goal"], str) or not item["goal"].strip():
                raise ValueError("agenda goals must be non-empty strings")
            if item["slot"] not in SLOTS:
                raise ValueError(f"invalid agenda slot: {item['slot']}")
            if item["category"] not in CATEGORIES:
                raise ValueError(f"invalid agenda category: {item['category']}")
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
