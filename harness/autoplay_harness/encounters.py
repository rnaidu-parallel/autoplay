"""Follow one chosen live villager with bounded ordinary controls, without another LLM call."""
import time

from .calendar import calendar_day
from .life import encounter_target
from .farming import TOOLBAR_BUTTONS


def approach_and_talk(bridge, notice, initial, operator_pending=lambda: False):
    started = time.monotonic()
    state, controls, first_input = initial, 0, None

    def result(status, reason):
        return {"status": status, "reason": reason, "state": state, "controls_executed": controls,
                "first_input_ms": first_input, "elapsed_ms": round((time.monotonic() - started) * 1000)}

    def send(kind, **arguments):
        nonlocal first_input, controls
        if first_input is None:
            first_input = round((time.monotonic() - started) * 1000)
        controls += 1
        return bridge.request(kind, **arguments)

    for _ in range(12):
        if operator_pending():
            return result("yielded", "operator_pending")
        response = bridge.observe()
        if response.get("status") != "completed":
            return result("interrupted", "observation_failed")
        state = response.get("state") or {}
        if (state.get("location") != initial.get("location") or calendar_day(state) != calendar_day(initial)
                or state.get("eventUp") or state.get("health", 0) < initial.get("health", 0)):
            return result("interrupted", "scene_changed_or_damage")
        target = encounter_target(notice, state)
        if target is None:
            return result("missed", "target_left")
        if str(state.get("menu", "")).startswith("DialogueBox") or target.get("talkedToday"):
            return result("completed", "conversation_observed")
        if state.get("menu") != "none" or not state.get("canMove") or not state.get("playerFree"):
            return result("interrupted", "player_unavailable")
        if time.monotonic() - started >= 8:
            return result("missed", "encounter_deadline")
        distance = abs(target["x"] - state["tileX"]) + abs(target["y"] - state["tileY"])
        if distance > 12:
            return result("missed", "target_out_of_range")
        if operator_pending():
            return result("yielded", "operator_pending")
        if distance <= 1:
            selected = next((item for item in state.get("inventory", []) if item.get("slot") == state.get("toolbarIndex")), None)
            if selected and not selected.get("isTool"):
                tool = next((item for item in state.get("inventory", []) if item.get("isTool")
                             and 0 <= item.get("slot", -1) < len(TOOLBAR_BUTTONS)), None)
                if tool is None:
                    return result("missed", "no_safe_toolbar_slot_for_talking")
                response = send("press", buttons=[TOOLBAR_BUTTONS[tool["slot"]]])
                state = response.get("state") or state
                if response.get("status") != "completed":
                    return result("missed", "toolbar_selection_failed")
                continue
            # The bridge resolves the identity again on the game thread before clicking.
            response = send("talk_to_npc", value=target.get("id", target["name"]))
        else:
            rows = state.get("navigationRows", [])
            ox, oy = state.get("navigationOriginX", 0), state.get("navigationOriginY", 0)
            tiles = [(target["x"] + dx, target["y"] + dy) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))]
            tiles = [(x, y) for x, y in tiles if 0 <= y-oy < len(rows)
                     and 0 <= x-ox < len(rows[y-oy]) and rows[y-oy][x-ox] == "."]
            if not tiles:
                return result("missed", "no_adjacent_tile")
            x, y = min(tiles, key=lambda p: abs(p[0]-state["tileX"]) + abs(p[1]-state["tileY"]))
            response = send("navigate", x=x, y=y, ticks=60, segmentTicks=30, noticeEncounters=False)
        state = response.get("state") or state
        if response.get("status") not in {"completed", "yielded", "timeout"}:
            return result("missed", response.get("reason") or "approach_failed")
    return result("missed", "encounter_action_limit")
