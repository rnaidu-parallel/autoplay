from __future__ import annotations

from typing import Any
import time

from .bridge import NamedPipeBridge


def nearest_empty_tiles(state: dict[str, Any], count: int) -> list[dict[str, int]]:
    remaining = {(crop["x"], crop["y"]) for crop in state.get("cropsNearby", []) if crop.get("crop") is None}
    if len(remaining) < count or not 1 <= count <= 6:
        return []
    x, y = state["tileX"], state["tileY"]
    targets = []
    for _ in range(count):
        x, y = min(remaining, key=lambda tile: (abs(tile[0] - x) + abs(tile[1] - y), tile))
        remaining.remove((x, y))
        targets.append({"x": x, "y": y})
    return targets


def plant_seeds(bridge: NamedPipeBridge, state: dict[str, Any], seed_slot: int,
                tiles: list[dict[str, int]], action_budget: int) -> dict[str, Any]:
    """Plant observed empty soil using ordinary inputs, verifying each seed before continuing."""
    origin = state.get("location")
    controls = 0
    planted: list[dict[str, int]] = []
    timings: list[dict[str, Any]] = []

    def finish(status: str, reason: str | None = None) -> dict[str, Any]:
        return {"status": status, "reason": reason, "state": state,
                "controls_executed": controls, "tiles_planted": planted, "control_timings": timings}

    def item() -> dict[str, Any]:
        return next((entry for entry in state.get("inventory", []) if entry["slot"] == seed_slot), {})

    def soil(target: dict[str, int]) -> dict[str, Any] | None:
        return next((crop for crop in state.get("cropsNearby", [])
                     if (crop["x"], crop["y"]) == (target["x"], target["y"])), None)

    seed = item()
    if not seed.get("isSeed") or not 0 <= seed_slot <= 11:
        return finish("rejected", "seed_slot_must_contain_seeds_in_the_first_toolbar_row")
    seed_id = seed["qualifiedId"]
    if not 1 <= len(tiles) <= 6 or len({(tile["x"], tile["y"]) for tile in tiles}) != len(tiles):
        return finish("rejected", "supply_one_to_six_distinct_tiles")
    if any(soil(tile) is None or soil(tile).get("crop") is not None for tile in tiles):
        return finish("rejected", "targets_must_be_observed_empty_tilled_soil")

    def control(kind: str, **arguments: Any) -> str | None:
        nonlocal state, controls
        if controls >= action_budget:
            return "action_budget_reached"
        if (state.get("location") != origin or state.get("menu") != "none"
                or not state.get("worldReady") or not state.get("playerFree")
                or not state.get("canMove") or state.get("eventUp")):
            return "world_changed_or_player_unavailable"
        previous_health = state.get("health", 0)
        started = time.perf_counter()
        response = bridge.request(kind, **arguments)
        timings.append({"control": kind, "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "status": response.get("status")})
        controls += 1
        state = response.get("state") or {}
        if response.get("status") != "completed":
            return response.get("error") or response.get("reason") or response.get("status") or "control_failed"
        if (state.get("location") != origin or state.get("menu") != "none"
                or state.get("eventUp") or state.get("health", 0) < previous_health):
            return "world_changed_or_damage_taken"
        return None

    for target in tiles:
        if controls + 2 + (state.get("toolbarIndex") != seed_slot) > action_budget:
            return finish("partial", "action_budget_reached")
        if item().get("qualifiedId") != seed_id or (item().get("stack") or 0) < 1:
            return finish("partial", "seeds_exhausted_or_changed")
        error = control("navigate", x=target["x"], y=target["y"], ticks=600)
        if error:
            return finish("blocked", error)
        if (state.get("tileX"), state.get("tileY")) != (target["x"], target["y"]):
            return finish("blocked", "navigation_did_not_reach_target")
        if state.get("toolbarIndex") != seed_slot:
            button = ([f"D{i}" for i in range(1, 10)] + ["D0", "OemMinus", "OemPlus"])[seed_slot]
            error = control("press", buttons=[button])
            if error or state.get("toolbarIndex") != seed_slot:
                return finish("blocked", error or "seed_selection_failed")
        target_soil = soil(target)
        if target_soil is None or target_soil.get("crop") is not None or item().get("qualifiedId") != seed_id:
            return finish("blocked", "target_or_seed_changed")
        x, y = target_soil["screenX"], target_soil["screenY"]
        if not (0 <= x < state["viewportWidth"] and 0 <= y < state["viewportHeight"]):
            return finish("blocked", "target_not_on_screen")
        count_before = item()["stack"]
        error = control("click", x=x, y=y, button="right")
        crop_after = soil(target)
        if error:
            return finish("blocked", error)
        if (crop_after is None or crop_after.get("crop") is None or crop_after.get("dead")
                or item().get("stack", 0) != count_before - 1):
            return finish("blocked", "planting_not_verified_by_crop_and_seed_delta")
        planted.append(target)
    return finish("completed")
