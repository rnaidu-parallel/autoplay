"""Monotonic in-game day numbers; legacy Spring/year-one keys remain compatible."""

from typing import Any


def calendar_day(state: dict[str, Any]) -> int | None:
    day = state.get("day")
    if not isinstance(day, int):
        return None
    season = str(state.get("season") or "spring").casefold()
    return (int(state.get("year") or 1) - 1) * 112 + ("spring", "summer", "fall", "winter").index(season) * 28 + day
