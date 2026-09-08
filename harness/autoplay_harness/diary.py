from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


KINDS = frozenset({"entry", "note", "bedtime"})

STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her", "was", "one",
    "our", "out", "has", "had", "him", "his", "how", "its", "who", "did", "get", "let",
    "too", "any", "day", "use", "new", "now", "way", "may", "say", "she", "been", "this",
    "that", "with", "from", "have", "they", "will", "your", "what", "when", "where",
    "which", "would", "there", "their", "about", "into", "than", "then", "them", "some",
    "could", "other", "time", "just", "like", "over", "also", "after", "before",
    "because", "while",
})

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")


def game_time(value: int | None) -> str:
    if value is None:
        return "—"
    hour = (value // 100) % 24
    minute = value % 100
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD.findall(query.lower()):
        if len(word) >= 3 and word not in STOPWORDS and word not in tokens:
            tokens.append(word)
    return tokens


class Diary:
    def __init__(self, state_directory: Path, save_id: str | None = None) -> None:
        base = Path(state_directory) / "diary"
        self.directory = base / save_id if save_id else base

    def write(self, day: int, time: int | None, location: str | None, text: str, kind: str = "entry") -> dict:
        if kind not in KINDS:
            raise ValueError(f"invalid diary kind: {kind}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("diary text must not be empty")
        entry = {
            "day": day,
            "time": time,
            "location": location,
            "text": text.strip(),
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._day_path(day).open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self, day: int) -> list[dict]:
        path = self._day_path(day)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # a partial line from an interrupted write must not take the diary down with it
        return entries

    def days(self) -> list[int]:
        if not self.directory.exists():
            return []
        found = []
        for path in self.directory.glob("*.jsonl"):
            try:
                found.append(int(path.stem))
            except ValueError:
                continue
        return sorted(found)

    def last_entry(self, before_day: int) -> dict | None:
        earlier = [day for day in self.days() if day < before_day]
        if not earlier:
            return None
        day_entries = self.entries(max(earlier))
        if not day_entries:
            return None
        bedtimes = [entry for entry in day_entries if entry.get("kind") == "bedtime"]
        return bedtimes[-1] if bedtimes else day_entries[-1]

    def search(self, query: str, today: int, days: int = 7, limit: int = 6, excerpt_chars: int = 240) -> list[dict]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        start_day, end_day = today - days, today - 1
        candidates = []
        for day in self.days():
            if day < start_day or day > end_day:
                continue
            for entry in self.entries(day):
                text_lower = entry["text"].lower()
                matched = [token for token in tokens if token in text_lower]
                if matched:
                    candidates.append((entry, matched))
        candidates.sort(key=lambda pair: (-len(pair[1]), -pair[0]["day"], -(pair[0].get("time") if pair[0].get("time") is not None else -1)))
        return [
            {
                "day": entry["day"],
                "time": entry.get("time"),
                "location": entry.get("location"),
                "kind": entry.get("kind"),
                "excerpt": self._excerpt(entry["text"], matched, excerpt_chars),
            }
            for entry, matched in candidates[:limit]
        ]

    def read(self, day: int, max_chars: int = 6000) -> str:
        lines = []
        for entry in self.entries(day):
            location = entry.get("location") if entry.get("location") is not None else "—"
            lines.append(f"{game_time(entry.get('time'))} · {location} · {entry['text']}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars] + "…"

    def excerpts(self, today: int, location: str | None, names: list[str], objective_words: list[str], limit: int = 3) -> list[dict]:
        parts = []
        if location:
            parts.append(_CAMEL_BOUNDARY.sub(" ", location))
        parts.extend(names or [])
        parts.extend(objective_words or [])
        return self.search(" ".join(parts), today=today, days=14, limit=limit)

    @staticmethod
    def _excerpt(text: str, matched: list[str], excerpt_chars: int) -> str:
        text_lower = text.lower()
        token = min(matched, key=lambda candidate: text_lower.find(candidate))
        position = text_lower.find(token)
        center = position + len(token) // 2
        half = excerpt_chars // 2
        start = max(0, center - half)
        end = min(len(text), start + excerpt_chars)
        start = max(0, end - excerpt_chars)
        excerpt = text[start:end]
        if start > 0:
            excerpt = "…" + excerpt
        if end < len(text):
            excerpt = excerpt + "…"
        return excerpt

    def _day_path(self, day: int) -> Path:
        return self.directory / f"{day}.jsonl"
