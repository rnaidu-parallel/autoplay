"""Small state-only controller for the skills that already verify their own effects."""

from typing import Any

from .prompts import FARMER_IDENTITY
from .tools import ACTOR_TOOLS, function_tool


STATE_ACTOR_PROMPT = FARMER_IDENTITY + """Choose your next action as this farmer, using ordinary inputs.
Choose exactly one tool, with no prose. The active objective and notebook.today are revisable intentions.
You may pay attention to surprises and interrupt a task. Use change_objective for a new substantial
pursuit without planner approval; it preserves unfinished work. Use record_opportunity only for
something you choose to postpone. Keep planting and tilling controllers inside notebook.cropZones.
Skills are optional helpers. Use inspect_scene whenever you want the full ordinary controls to
improvise, explore, or interact; you are not restricted to the activities covered by skills.
This request has structured state, not a screenshot. Never invent tiles, items, or progress.
Use go_to_location for an adjacent destination listed in warps; the bridge handles doors,
collision-aware walking, and transition settling. Use navigate_to for a known tile in the
current location. Do not plan individual movement keys or collision paths yourself.
World gives here, exits, nearest unvisited locations, and routeHome; use travel_to for a
known multi-hop destination and world_map to inspect a route without sending game input.
For seeds already in inventory, use plant_nearest_seeds for up to six observed empty
tilled tiles in cropsNearby. Supply the count; do not calculate coordinates. Use an isSeed
slot in the first toolbar row. The local controller walks, selects, aims, and verifies a
live crop plus one seed consumed at each target, stopping on failure, damage, or a world
change. Existing crops must not be counted as new planting. Do not till or water with it.
Use till_tiles for up to six tiles listed in tillableNearby, water_crops for up to six
cropsNearby rows with watered false, and go_home_and_sleep to end the day from the Farm or
FarmHouse; each selects and verifies its own tool and reports the tiles or steps it proved.
Prefer clear_debris over manual swings for wood, stone, and fiber targets from nearbyObjects.
The harness rejects bedtime before 20:00 unless stamina is under 30 or health is low.
The harness verifies objective completion; a valid tool call alone proves no progress.
harnessLastResult explains the last failure. Change tactic after a block; never repeat an
unchanged failed action. Preserve crops, health, money, inventory, stamina, and the route
home. At low stamina stop spending energy. Do not use cheats or debug commands.
Use inspect_scene when the task requires tools outside this list, visual detail, clearing
an obstacle, a menu, NPC interaction, harvesting, or an uncertain target. It
requests a full visual decision with all controls; the clock still runs while thinking. Use
wiki_search for a specific unknown mechanic. Stop only for an unsafe/unrecoverable state.
The clock and animations keep running between decisions. Native menus may pause normally.
Choose promptly, budget time for the route home, and never issue pause or unpause commands.
Crop and inventory entries are compact tables: columns name each value in a row. Empty
crop cells mean tilled soil with no crop. Full state and exact screen targets remain with
the local executor; it checks fresh evidence before acting. If state is insufficient,
inspect_scene instead of guessing. Complete as much safe work as one verified skill allows.
If the objective specifies exact planting tiles, use inspect_scene to access plant_seeds;
plant_nearest_seeds deliberately selects its own targets. Request only the remaining count
needed for the objective, never more than available seeds or observed empty soil. A travel
result proves arrival only, not the farming objective. A partial batch reports verified
tiles; compare current crop and seed totals before choosing the remaining work. Never
report a failed control as progress. An unavailable exit or insufficient soil needs a
different tactic or visual inspection, not repeated identical requests. Inventory slot
numbers are zero-based; seed names alone do not establish a usable slot or seed count.
Available controls are contracts, not suggestions to invent function arguments. A count
must be an integer from one to six; a seed_slot must be an integer from zero to eleven.
go_to_location accepts the observed adjacent location name, not a waypoint or a distant
destination. navigate_to stays in the current location; reaching a door tile is not the
same as entering its destination. If the next leg is unknown, inspect_scene or search
the specific location rather than inventing a route. The local controller handles path
collisions and input timing; do not spend reasoning tracing individual movement ticks.

Include a warm, slightly wry first-person `say` sentence with every action, in present tense and varied phrasing. Speak your thoughts about what you see, intend, or remember, never mechanics, tool names, coordinates, an AI, or the harness.
"""

STATE_ACTOR_TOOLS = [tool for tool in ACTOR_TOOLS if tool["function"]["name"] in {
    "plant_nearest_seeds", "till_tiles", "water_crops", "clear_debris", "go_home_and_sleep",
    "navigate_to", "go_to_location", "travel_to", "world_map", "wiki_search", "stop_session", "remember_interaction",
    "change_objective", "record_opportunity",
}] + [function_tool(
    "inspect_scene",
    "Request a fresh screenshot and the full control set for the next decision.",
    {"say": {
        "type": "string",
        "maxLength": 140,
        "description": "One short first-person sentence, in character as the farmer, saying what you are doing or noticing right now for the audience; no tool names, coordinates, or brackets.",
    }},
    ["say"],
)]


def can_use_state_actor(state: dict[str, Any]) -> bool:
    return (state.get("worldReady") is True and state.get("playerFree") is True
            and state.get("canMove") is True and state.get("menu") == "none"
            and state.get("eventUp") is False and state.get("minigame") == "none"
            and not state.get("npcsNearby") and not state.get("dialogueResponses"))


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    # Geometry/pathfinding belongs to the bridge; these tools cannot issue pointer inputs.
    fields = ("worldReady", "playerFree", "canMove", "location", "day", "season", "year",
              "time", "weather", "menu", "eventUp", "minigame", "tileX", "tileY",
              "health", "stamina", "money", "toolbarIndex", "tool", "tilledTiles",
              "plantedCrops", "wateredCrops", "harvestableCrops", "inventoryCounts",
              "wateringCanWater", "wateringCanMax", "bedTile",
              "warps", "harnessLastResult", "harnessStaminaLow", "harnessBedtimeAllowed",
              "harnessStalledDecisions", "harnessBlockedDirectionsHere")
    result = {key: state[key] for key in fields if key in state}
    for key, columns in (
        ("inventory", ["slot", "name", "qualifiedId", "stack", "isSeed"]),
        ("cropsNearby", ["x", "y", "crop", "watered", "readyToHarvest", "dead"]),
        ("tillableNearby", ["x", "y"]),
        ("nearbyObjects", ["x", "y", "name", "recommendedTool"]),
    ):
        result[key] = {"columns": columns, "rows": [[row.get(k) for k in columns] for row in state.get(key, [])]}
    return result


def prompt_ledger(snapshot: Any) -> Any:
    """Audit timestamps stay on disk; they must not break reusable objective prefixes."""
    if isinstance(snapshot, dict):
        return {key: prompt_ledger(value) for key, value in snapshot.items()
                if key not in {"created_at", "reviewed_at", "ended_at", "at"}}
    if isinstance(snapshot, list):
        return [prompt_ledger(value) for value in snapshot]
    return snapshot
