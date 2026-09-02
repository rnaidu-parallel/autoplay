from __future__ import annotations

from typing import Any
import time

from .bridge import NamedPipeBridge


TOOLBAR_BUTTONS = [f"D{i}" for i in range(1, 10)] + ["D0", "OemMinus", "OemPlus"]
FACING_KEYS = {(0, -1): ("W", 0), (1, 0): ("D", 1), (0, 1): ("S", 2), (-1, 0): ("A", 3)}


class _Controls:
    """Bounded ordinary-input executor shared by the verified day-loop skills."""

    def __init__(self, bridge: NamedPipeBridge, state: dict[str, Any], action_budget: int) -> None:
        self.bridge = bridge
        self.state = state
        self.action_budget = action_budget
        self.origin = state.get("location")
        self.executed = 0
        self.timings: list[dict[str, Any]] = []

    def send(self, kind: str, **arguments: Any) -> str | None:
        """One bridge control with only the action cap enforced."""
        if self.executed >= self.action_budget:
            return "action_budget_reached"
        started = time.perf_counter()
        response = self.bridge.request(kind, **arguments)
        self.timings.append({"control": kind, "elapsed_ms": round((time.perf_counter() - started) * 1000),
                             "status": response.get("status")})
        self.executed += 1
        self.state = response.get("state") or {}
        if response.get("status") != "completed":
            return response.get("error") or response.get("reason") or response.get("status") or "control_failed"
        return None

    def guard(self, previous_health: int) -> str | None:
        if (self.state.get("location") != self.origin or self.state.get("menu") != "none"
                or self.state.get("eventUp") or self.state.get("health", 0) < previous_health):
            return "world_changed_or_damage_taken"
        return None

    def control(self, kind: str, **arguments: Any) -> str | None:
        """One bridge control that refuses to act on a changed world and stops on damage."""
        if (self.state.get("location") != self.origin or self.state.get("menu") != "none"
                or not self.state.get("worldReady") or not self.state.get("playerFree")
                or not self.state.get("canMove") or self.state.get("eventUp")):
            return "world_changed_or_player_unavailable"
        previous_health = self.state.get("health", 0)
        return self.send(kind, **arguments) or self.guard(previous_health)

    def select_tool(self, slot: int, tool_name: str) -> str | None:
        """A toolbar key that selected the wrong tool is reported, never pressed again."""
        if self.state.get("tool") == tool_name:
            return None
        error = self.control("press", buttons=[TOOLBAR_BUTTONS[slot]])
        if error:
            return error
        return None if self.state.get("tool") == tool_name else "tool_selection_failed"

    def face(self, delta: tuple[int, int]) -> str | None:
        """A one-tick direction press turns without moving; the game aims tools at the faced tile."""
        button, facing = FACING_KEYS[delta]
        if self.state.get("facing") == facing:
            return None
        error = self.control("press", buttons=[button])
        if error:
            return error
        return None if self.state.get("facing") == facing else "facing_change_failed"

    def use_tool(self, screen_x: int, screen_y: int) -> str | None:
        """Swing at the aimed tile; canMove stays false until the animation ends, so the wait skips it."""
        previous_health = self.state.get("health", 0)
        error = self.control("click", x=screen_x, y=screen_y, button="left")
        if error:
            return error
        return self.send("wait", field="using_tool", value="false", ticks=180) or self.guard(previous_health)

    def stand_beside(self, target: dict[str, int]) -> tuple[tuple[int, int] | None, str | None]:
        """Walk to a passable tile next to the target, because a swing hits the faced tile."""
        here = (self.state.get("tileX"), self.state.get("tileY"))
        options = [(target["x"] + dx, target["y"] + dy) for dx, dy in FACING_KEYS]
        reachable = [tile for tile in options if tile == here or _passable(self.state, tile)]
        if not reachable:
            return None, "no_passable_tile_next_to_target"
        stand = min(reachable, key=lambda tile: (abs(tile[0] - here[0]) + abs(tile[1] - here[1]), tile))
        if stand == here:
            return stand, None
        error = self.control("navigate", x=stand[0], y=stand[1], ticks=600)
        if error:
            return None, error
        if (self.state.get("tileX"), self.state.get("tileY")) != stand:
            return None, "navigation_did_not_reach_target"
        return stand, None


def _tool_slot(state: dict[str, Any], tool_name: str) -> int | None:
    entry = next((item for item in state.get("inventory", [])
                  if item.get("name") == tool_name and 0 <= item.get("slot", -1) <= 11), None)
    return entry["slot"] if entry else None


def _soil(state: dict[str, Any], target: dict[str, int]) -> dict[str, Any] | None:
    return next((crop for crop in state.get("cropsNearby", [])
                 if (crop["x"], crop["y"]) == (target["x"], target["y"])), None)


def _distinct_tiles(tiles: list[dict[str, int]]) -> bool:
    return 1 <= len(tiles) <= 6 and len({(tile["x"], tile["y"]) for tile in tiles}) == len(tiles)


def _passable(state: dict[str, Any], tile: tuple[int, int]) -> bool:
    rows = state.get("navigationRows") or []
    origin_x, origin_y = state.get("navigationOriginX"), state.get("navigationOriginY")
    if origin_x is None or origin_y is None:
        return False
    row, column = tile[1] - origin_y, tile[0] - origin_x
    return 0 <= row < len(rows) and 0 <= column < len(rows[row]) and rows[row][column] == "."


def _on_screen(state: dict[str, Any], entry: dict[str, Any]) -> bool:
    return (0 <= entry["screenX"] < state.get("viewportWidth", 0)
            and 0 <= entry["screenY"] < state.get("viewportHeight", 0))


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


def water_crops(bridge: NamedPipeBridge, state: dict[str, Any], tiles: list[dict[str, int]],
                action_budget: int) -> dict[str, Any]:
    """Water observed dry crops with ordinary inputs, verifying each tile before continuing."""
    runner = _Controls(bridge, state, action_budget)
    watered: list[dict[str, Any]] = []

    def finish(status: str, reason: str | None = None) -> dict[str, Any]:
        return {"status": status, "reason": reason, "state": runner.state,
                "controls_executed": runner.executed, "tiles_watered": watered,
                "control_timings": runner.timings}

    if not _distinct_tiles(tiles):
        return finish("rejected", "supply_one_to_six_distinct_tiles")
    slot = _tool_slot(state, "Watering Can")
    if slot is None:
        return finish("rejected", "watering_can_must_be_in_the_first_toolbar_row")
    if state.get("wateringCanWater") is None:
        return finish("rejected", "watering_can_water_is_unavailable")
    if any((_soil(state, tile) or {}).get("crop") is None or (_soil(state, tile) or {}).get("watered")
           for tile in tiles):
        return finish("rejected", "targets_must_be_observed_unwatered_crops")

    for target in tiles:
        if runner.executed + 4 + (runner.state.get("tool") != "Watering Can") > action_budget:
            return finish("partial", "action_budget_reached")
        water_before = runner.state.get("wateringCanWater") or 0
        if water_before <= 0:
            return finish("blocked", "watering_can_empty")
        stand, error = runner.stand_beside(target)
        if stand is None:
            return finish("blocked", error)
        error = runner.select_tool(slot, "Watering Can")
        if error:
            return finish("blocked", error)
        error = runner.face((target["x"] - stand[0], target["y"] - stand[1]))
        if error:
            return finish("blocked", error)
        soil = _soil(runner.state, target)
        if soil is None or soil.get("crop") is None or soil.get("watered"):
            return finish("blocked", "target_crop_changed")
        if not _on_screen(runner.state, soil):
            return finish("blocked", "target_not_on_screen")
        error = runner.use_tool(soil["screenX"], soil["screenY"])
        if error:
            return finish("blocked", error)
        soil_after = _soil(runner.state, target)
        water_after = runner.state.get("wateringCanWater")
        if soil_after is None or not soil_after.get("watered") or water_after != water_before - 1:
            return finish("blocked", "watering_not_verified_by_watered_state_and_water_delta")
        watered.append({"x": target["x"], "y": target["y"], "watered": True, "water_left": water_after})
    return finish("completed")


def till_tiles(bridge: NamedPipeBridge, state: dict[str, Any], tiles: list[dict[str, int]],
               action_budget: int) -> dict[str, Any]:
    """Till observed diggable ground with the hoe, verifying new empty soil on each tile."""
    runner = _Controls(bridge, state, action_budget)
    tilled: list[dict[str, Any]] = []

    def finish(status: str, reason: str | None = None) -> dict[str, Any]:
        return {"status": status, "reason": reason, "state": runner.state,
                "controls_executed": runner.executed, "tiles_tilled": tilled,
                "control_timings": runner.timings}

    def ground(source: dict[str, Any], target: dict[str, int]) -> dict[str, Any] | None:
        return next((entry for entry in source.get("tillableNearby", [])
                     if (entry["x"], entry["y"]) == (target["x"], target["y"])), None)

    if not _distinct_tiles(tiles):
        return finish("rejected", "supply_one_to_six_distinct_tiles")
    slot = _tool_slot(state, "Hoe")
    if slot is None:
        return finish("rejected", "hoe_must_be_in_the_first_toolbar_row")
    if any(ground(state, tile) is None for tile in tiles):
        return finish("rejected", "targets_must_be_observed_tillable_tiles")

    for target in tiles:
        if runner.executed + 4 + (runner.state.get("tool") != "Hoe") > action_budget:
            return finish("partial", "action_budget_reached")
        stand, error = runner.stand_beside(target)
        if stand is None:
            return finish("blocked", error)
        error = runner.select_tool(slot, "Hoe")
        if error:
            return finish("blocked", error)
        error = runner.face((target["x"] - stand[0], target["y"] - stand[1]))
        if error:
            return finish("blocked", error)
        entry = ground(runner.state, target)
        if entry is None:
            return finish("blocked", "target_is_no_longer_tillable")
        if not _on_screen(runner.state, entry):
            return finish("blocked", "target_not_on_screen")
        error = runner.use_tool(entry["screenX"], entry["screenY"])
        if error:
            return finish("blocked", error)
        soil = _soil(runner.state, target)
        if soil is None or soil.get("crop") is not None:
            return finish("blocked", "tilling_not_verified_by_new_empty_soil")
        tilled.append({"x": target["x"], "y": target["y"], "tilled": True})
    return finish("completed")


def go_home_and_sleep(bridge: NamedPipeBridge, state: dict[str, Any], action_budget: int) -> dict[str, Any]:
    """Walk into the farmhouse, answer the bed prompt, and prove the night advanced the day."""
    runner = _Controls(bridge, state, action_budget)
    steps: list[dict[str, Any]] = []
    fields = ("day", "time", "stamina", "money")
    before = {key: state.get(key) for key in fields}
    start_health = state.get("health") or 0

    def finish(status: str, reason: str | None = None) -> dict[str, Any]:
        return {"status": status, "reason": reason, "state": runner.state,
                "controls_executed": runner.executed, "steps": steps, "before": before,
                "after": {key: runner.state.get(key) for key in fields},
                "control_timings": runner.timings}

    def step(kind: str, **arguments: Any) -> str | None:
        # The location and menu change on purpose here, so only damage and events end the skill.
        error = runner.send(kind, **arguments)
        if error:
            return error
        if (runner.state.get("health") or 0) < start_health or runner.state.get("eventUp"):
            return "world_changed_or_damage_taken"
        return None

    def slept() -> bool:
        return (runner.state.get("day") == (before["day"] or 0) + 1
                and runner.state.get("worldReady") is True and runner.state.get("playerFree") is True
                and (runner.state.get("menu") or "none") == "none"
                and runner.state.get("location") == "FarmHouse")

    if not (state.get("worldReady") and state.get("playerFree") and state.get("menu") == "none"
            and before["day"] is not None):
        return finish("rejected", "player_must_be_free_in_a_loaded_world_outside_menus")
    if state.get("location") not in {"Farm", "FarmHouse"}:
        return finish("blocked", "not_on_farm")

    if state.get("location") == "Farm":
        # The door warp can land on the same tick the navigator gives up, so the observed
        # location and control availability are the proof of arrival, not the transit status.
        error = step("go_to_location", location="FarmHouse", ticks=600)
        settle = step("wait", field="location", value="FarmHouse", ticks=180)
        if runner.state.get("location") != "FarmHouse":
            return finish("blocked", error or settle or "did_not_reach_farmhouse")
        error = step("wait", field="can_move", value="true", ticks=180)
        if error:
            return finish("blocked", error)
        steps.append({"step": "enter_farmhouse", "location": runner.state.get("location")})

    bed = runner.state.get("bedTile")
    if not bed:
        return finish("blocked", "bed_tile_unavailable")
    error = step("navigate", x=bed["x"], y=bed["y"], ticks=600)
    if error and not runner.state.get("dialogueResponses"):
        return finish("blocked", error)
    steps.append({"step": "reach_bed", "bed": bed, "menu": runner.state.get("menu"),
                  "tile": [runner.state.get("tileX"), runner.state.get("tileY")]})

    if not runner.state.get("dialogueResponses"):
        error = step("press", buttons=["X"])
        if error:
            return finish("blocked", error)
    # The question box only builds the clickable responses that the bridge invokes once its
    # opening animation has finished, so give it a bounded settle before choosing.
    error = step("idle", ticks=60)
    if error:
        return finish("blocked", error)
    responses = runner.state.get("dialogueResponses") or []
    index = next((entry["index"] for entry in responses if entry.get("key") == "Yes"), None)
    if index is None:
        return finish("blocked", "sleep_prompt_did_not_appear")
    error = step("choose_dialogue_response", index=index)
    if error:
        return finish("blocked", error)
    steps.append({"step": "answer_sleep_prompt", "index": index,
                  "responses": [entry.get("text") for entry in responses]})

    # The answered box stays on screen for several ticks while the fade starts, so only the new
    # day proves the night ran. Escape would cancel the sleep, so a lingering question gets the
    # Yes hotkey the box binds itself, once.
    retried = False
    while not slept() and runner.executed + 2 <= action_budget:
        error = step("idle", ticks=120)
        if error:
            return finish("blocked", error)
        menu = runner.state.get("menu") or "none"
        if slept() or menu == "none":
            continue
        if runner.state.get("dialogueResponses"):
            if retried:
                return finish("blocked", "sleep_prompt_not_answered")
            retried = True
            error = step("press", buttons=["Y"])
        elif menu.startswith("DialogueBox"):
            continue
        else:
            error = step("press", buttons=["Escape"])
        if error:
            return finish("blocked", error)
    if not slept():
        return finish("partial", "night_transition_did_not_finish")
    steps.append({"step": "new_day", "day": runner.state.get("day"), "time": runner.state.get("time"),
                  "location": runner.state.get("location")})
    return finish("completed")
