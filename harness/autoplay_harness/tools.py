from __future__ import annotations

from typing import Any
from copy import deepcopy


AGENT_BUTTONS = [
    "W",
    "A",
    "S",
    "D",
    "LeftShift",
    "X",
    "C",
    "E",
    "Escape",
    "F",
    "M",
    "Y",
    "N",
    "Tab",
    "D0",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "OemMinus",
    "OemPlus",
]


def function_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


ACTOR_TOOLS = [
    function_tool("read_life_text", "Read retained game text in pages of up to 4000 characters. Use a notice/text ID from life or quest:<id> for a journal quest. Continue at nextOffset until null. Does not open or dismiss a game screen.",
                  {"id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}, ["id"]),
    function_tool("open_menu_tab", "Open a named game menu tab through normal controls. Inspect inventory capacity and slots, skill levels, social relationships, map labels, crafting recipes or collections. This is a menu inspection, not a pause command.",
                  {"tab": {"type": "string", "enum": ["inventory", "skills", "social", "map", "crafting", "collections", "options"]}}, ["tab"]),
    function_tool("pursue_interest", "Follow an observed curiosity without inventing a completion predicate. Preserve prior work. Use for looking around, a conversation or a place you want to understand; concrete delivery/work tasks still use change_objective with real evidence.",
                  {"goal": {"type": "string", "minLength": 1, "maxLength": 180}, "reason": {"type": "string", "minLength": 1, "maxLength": 180}}, ["goal", "reason"]),
    function_tool("consider_interest", "Finish considering your current open-ended interest after an observed interaction. Records considered, never claims a quest/task completed. Explain what you learned or why you are moving on.",
                  {"evidence": {"type": "string", "maxLength": 200}}, ["evidence"]),
    function_tool("check_mail", "On the Farm, walk beside the observed nearby mailbox and open one unread letter using normal controls. Read the letter and use menuEntries to accept or collect its contents before closing it. Can yield during walking.", {}, []),
    function_tool("click_menu_entry", "Click an exact currently visible menuEntries control: journal, letter, game tabs, crafting, inventory or chest. Use its zero-based index. canAccept=false means make room before taking that item. Verify the resulting page, quest or inventory change. Never discard a tool or quest item to make space.",
                  {"index": {"type": "integer", "minimum": 0}}, ["index"]),
    function_tool(
        "change_objective",
        "Choose a different pursuit now, without director approval. Preserve the current objective as interrupted and its agenda item as pending. For spontaneous interactions you can simply act; use this only when your substantial intention changes. Supply a currently false structured success condition. Use agenda_id to return to an unfinished agenda item. Retry limits still apply.",
        {"goal": {"type": "string", "minLength": 1, "maxLength": 400},
         "success_condition": {"type": "string", "minLength": 1, "maxLength": 300},
         "milestone": {"type": "string", "minLength": 1, "maxLength": 400},
         "reason": {"type": "string", "minLength": 1, "maxLength": 300},
         "agenda_id": {"type": "string", "maxLength": 40}},
        ["goal", "success_condition", "milestone", "reason"],
    ),
    function_tool(
        "remember_interaction",
        "Remember an attempted interaction and your own reaction. Requires recent_interaction from an actual action; does not send input. Use unknown for an unclear effect or an unreached target, and choose your preference independently. You decide what is meaningful and when your tastes change. A completed control alone does not prove an interaction worked.",
        {"subject": {"type": "string", "minLength": 1, "maxLength": 80},
         "interaction": {"type": "string", "minLength": 1, "maxLength": 80},
         "outcome": {"type": "string", "enum": ["possible", "unavailable", "unknown"]},
         "preference": {"type": "string", "enum": ["liked", "disliked", "neutral", "undecided"]},
         "revisit_when": {"type": "string", "maxLength": 160, "description": "Optional real state condition for remembering to return, e.g. money >= 300 or location is Town. No invented facts."},
         "note": {"type": "string", "minLength": 1, "maxLength": 180,
                  "description": "Observed response and conditions, plus why you felt this way. Keep facts separate from your personal reaction."}},
        ["subject", "interaction", "outcome", "preference", "note"],
    ),
    function_tool(
        "plant_nearest_seeds",
        "Plant a count of seeds on the nearest observed empty tilled tiles. The local controller selects a short walking order and verifies every crop and seed decrement. Use when exact target tiles do not matter. Stops on failure or world changes; does not till or water.",
        {"seed_slot": {"type": "integer", "minimum": 0, "maximum": 11},
         "count": {"type": "integer", "minimum": 1, "maximum": 6}},
        ["seed_slot", "count"],
    ),
    function_tool(
        "plant_seeds",
        "Plant seeds on 1 to 6 distinct empty tilled tiles from cropsNearby in one call. Stands beside each tile, selects seeds, faces it, aims using fresh screen coordinates, right-clicks, and verifies the crop and seed decrement. Stops on failure or world changes. Supply a seed slot from the first 12 inventory slots (isSeed=true). Does not till or water.",
        {"seed_slot": {"type": "integer", "minimum": 0, "maximum": 11},
         "tiles": {"type": "array", "minItems": 1, "maxItems": 6,
                   "items": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                             "required": ["x", "y"], "additionalProperties": False}}},
        ["seed_slot", "tiles"],
    ),
    function_tool(
        "water_crops",
        "Water 1 to 6 distinct planted tiles from cropsNearby that are still watered=false in one call. Walks beside each tile, selects the Watering Can, faces the tile, swings, and verifies the watered state and one unit of can water spent. Stops on failure or world changes. Returns blocked with watering_can_empty when the can runs dry; it does not refill.",
        {"tiles": {"type": "array", "minItems": 1, "maxItems": 6,
                   "items": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                             "required": ["x", "y"], "additionalProperties": False}}},
        ["tiles"],
    ),
    function_tool(
        "till_tiles",
        "Till 1 to 6 distinct tiles from tillableNearby in one call. Walks beside each tile, selects the Hoe, faces the tile, swings, and verifies the tile became empty tilled soil in cropsNearby. Stops on failure or world changes. Only tiles listed in tillableNearby are diggable and unoccupied; it does not plant or water.",
        {"tiles": {"type": "array", "minItems": 1, "maxItems": 6,
                   "items": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                             "required": ["x", "y"], "additionalProperties": False}}},
        ["tiles"],
    ),
    function_tool(
        "clear_debris",
        "Clear 1 to 6 distinct wood, stone, or fiber obstacles from nearbyObjects in one call. Walks beside each target, selects its recommended Axe, Pickaxe, or Scythe (including upgraded tools), faces it, and swings until the object disappears or a verified stop condition occurs.",
        {"targets": {"type": "array", "minItems": 1, "maxItems": 6,
                     "items": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                               "required": ["x", "y"], "additionalProperties": False}}},
        ["targets"],
    ),
    function_tool(
        "go_home_and_sleep",
        "End the day from the Farm or FarmHouse in one call. Enters the farmhouse, walks onto bedTile, answers the sleep question, waits out the nightly event and save, and dismisses the end-of-day summary. Verifies the next day with the player free in the FarmHouse and reports the day, time, stamina, and money before and after.",
        {},
        [],
    ),
    function_tool(
        "navigate_to",
        "Walk to any walkable tile in the current location using only W/A/S/D. The game bridge plans a path with the game's real collision rules and stops on a location change, menu, cutscene, or unexpected block. Prefer this over manual holds for any trip longer than one tile.",
        {
            "tile_x": {"type": "integer"},
            "tile_y": {"type": "integer"},
        },
        ["tile_x", "tile_y"],
    ),
    function_tool(
        "go_to_location",
        "Walk to the exit that leads to the named adjacent location (a name from `warps` or a door in `nearbyActions`) and pass through it using ordinary movement and the action key. One hop only; chain calls for longer trips.",
        {"location": {"type": "string", "maxLength": 80}},
        ["location"],
    ),
    function_tool(
        "travel_to",
        "Travel through multiple known locations in one decision. The local controller chooses the shortest world-map route, performs and verifies each adjacent hop, and stops on closed doors, events, dialogue, menus, damage, or failed movement.",
        {"destination": {"type": "string", "maxLength": 80}},
        ["destination"],
    ),
    function_tool(
        "world_map",
        "Inspect the shortest route to a known destination and the compact world summary without sending game input. The clock continues running while you think.",
        {"destination": {"type": "string", "maxLength": 80}},
        ["destination"],
    ),
    function_tool(
        "control_sequence",
        "Execute 1 to 6 safe keyboard steps without another model call. Use for known navigation or repeated tool work; execution stops early on a block, menu change, or location change.",
        {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "buttons": {
                            "type": "array",
                            "items": {"type": "string", "enum": AGENT_BUTTONS},
                            "minItems": 1,
                        },
                        "ticks": {"type": "integer", "minimum": 1, "maximum": 120},
                    },
                    "required": ["buttons", "ticks"],
                    "additionalProperties": False,
                },
            }
        },
        ["steps"],
    ),
    function_tool(
        "press",
        "Press one or more allowed keyboard buttons for one game tick.",
        {"buttons": {"type": "array", "items": {"type": "string", "enum": AGENT_BUTTONS}, "minItems": 1}},
        ["buttons"],
    ),
    function_tool(
        "hold",
        "Hold allowed keyboard buttons for a bounded number of game ticks.",
        {
            "buttons": {"type": "array", "items": {"type": "string", "enum": AGENT_BUTTONS}, "minItems": 1},
            "ticks": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        ["buttons", "ticks"],
    ),
    function_tool(
        "move_cursor",
        "Move the cursor to normalized coordinates in the current screenshot without clicking.",
        {
            "frame_id": {"type": "string"},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
        },
        ["frame_id", "x", "y"],
    ),
    function_tool(
        "click",
        "Click at normalized coordinates in the current screenshot.",
        {
            "frame_id": {"type": "string"},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "button": {"type": "string", "enum": ["left", "right"]},
        },
        ["frame_id", "x", "y", "button"],
    ),
    function_tool(
        "close_menu",
        "Close the current menu, letter or journal. Returns any item held on the cursor to the bag first. Does not answer a question dialogue.",
        {}, [],
    ),
    function_tool(
        "inventory_move",
        "Move or swap an inventory stack between two zero-based slots (0-11 are the toolbar row). Stacks of the same item merge.",
        {"from_slot": {"type": "integer", "minimum": 0, "maximum": 35}, "to_slot": {"type": "integer", "minimum": 0, "maximum": 35}},
        ["from_slot", "to_slot"],
    ),
    function_tool(
        "inventory_trash",
        "Throw away the whole stack in a zero-based inventory slot. Tools and quest items are refused. Use for worthless surplus when the bag is full.",
        {"slot": {"type": "integer", "minimum": 0, "maximum": 35}}, ["slot"],
    ),
    function_tool(
        "ship_item",
        "Put a stack (count 0 = all) from a zero-based inventory slot into the shipping bin for sale overnight. Stand within two tiles of the shipping bin, or have its menu open. Money arrives the next morning.",
        {"slot": {"type": "integer", "minimum": 0, "maximum": 35}, "count": {"type": "integer", "minimum": 0, "maximum": 999}},
        ["slot", "count"],
    ),
    function_tool(
        "scroll",
        "Scroll the mouse wheel a bounded number of steps.",
        {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "steps": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        ["direction", "steps"],
    ),
    function_tool(
        "wait",
        "Wait until a structured game-state field equals a value or the timeout expires.",
        {
            "field": {
                "type": "string",
                "enum": ["world_ready", "game_active", "player_free", "can_move", "using_tool", "event_up", "menu", "location", "minigame"],
            },
            "value": {"type": ["string", "boolean"]},
            "timeout_ticks": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        ["field", "value", "timeout_ticks"],
    ),
    function_tool(
        "idle",
        "Wait for a bounded number of ticks without pressing any key; the game continues running afterward. Use for intentional time passage or ambient waiting.",
        {"ticks": {"type": "integer", "minimum": 1, "maximum": 600}},
        ["ticks"],
    ),
    function_tool(
        "choose_dialogue_response",
        "Choose one visible question response by its index from dialogueResponses using the game's real clickable component.",
        {"index": {"type": "integer", "minimum": 0, "maximum": 20}},
        ["index"],
    ),
    function_tool(
        "objective_progress",
        "Record concrete progress toward the active objective without changing it.",
        {"note": {"type": "string"}, "evidence": {"type": "string"}},
        ["note", "evidence"],
    ),
    function_tool(
        "record_opportunity",
        "Record an unrelated opportunity for a later director review.",
        {"note": {"type": "string"}, "reason": {"type": "string"}},
        ["note", "reason"],
    ),
    function_tool(
        "wiki_search",
        "Search the Stardew Valley Wiki for a specific unknown fact. Use a concise canonical topic or likely page title, not a full question.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    function_tool(
        "stop_session",
        "Stop autonomous play because the state is unsafe or the configured objective is finished.",
        {"reason": {"type": "string"}},
        ["reason"],
    ),
]


HISTORY_TOOL = function_tool(
    "search_history",
    "Search recorded actions and objectives across earlier runs. Returns up to five compact excerpts with source references; no game input.",
    {"query": {"type": "string", "minLength": 1, "maxLength": 120},
     "kind": {"type": "string", "enum": ["all", "actions", "objectives"]},
     "limit": {"type": "integer", "minimum": 1, "maximum": 5}},
    ["query"],
)
ACTOR_TOOLS.append(deepcopy(HISTORY_TOOL))

for _actor_tool in ACTOR_TOOLS:
    _parameters = _actor_tool["function"]["parameters"]
    _parameters["properties"]["say"] = {
        "type": "string",
        "maxLength": 200,
        "description": "What Neon is thinking right now, in her own voice, for the people watching: one or two short sentences, present tense. React to what just happened before saying what is next. Never tool names, coordinates, brackets, or anything about being an AI or a program.",
    }
    _parameters["required"].append("say")


PLAN_DAY_TOOL = function_tool(
    "plan_day",
    "Review ongoing intentions in light of actual overnight changes, discovered quests and needs. Zero new items is valid; unchanged intentions persist automatically. No daily theme or activity quota. carried_id refers to an existing unfinished intention; omit for new discoveries. Give a reason for anything explicitly dropped.",
    {
        "theme": {"type": "string", "maxLength": 200},
        "agenda": {
            "type": "array",
            "minItems": 0,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "maxLength": 400},
                    "success_condition": {"type": "string", "maxLength": 300},
                    "slot": {"type": "string", "enum": ["morning", "midday", "afternoon", "evening"]},
                    "category": {"type": "string", "enum": ["farming", "clearing", "exploring", "social", "shopping", "fishing", "mining", "foraging", "crafting", "event", "home"]},
                    "carried_id": {"type": "string", "maxLength": 40},
                    "source": {"type": "string", "maxLength": 100, "description": "Observed quest ID, encounter, memory or practical need."},
                    "reason": {"type": "string", "maxLength": 180},
                },
                "required": ["goal", "slot", "category"],
                "additionalProperties": False,
            },
        },
        "dropped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "maxLength": 40},
                    "reason": {"type": "string", "maxLength": 300},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    ["theme", "agenda", "dropped"],
)

UPDATE_FARM_PLAN_TOOL = function_tool(
    "update_farm_plan",
    "Set farm-use zones from farmLayout. Keep crop plots near water or the house, trees at edges, and paths clear.",
    {
        "zones": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "purpose": {"type": "string", "enum": ["crops", "trees", "paths", "buildings", "animals", "reserve"]},
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                },
                "required": ["name", "purpose", "x1", "y1", "x2", "y2"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string", "maxLength": 1000},
    },
    ["zones", "notes"],
)

REFLECT_TOOL = function_tool(
    "reflect",
    "Record a short end-of-day reflection and durable facts learned today.",
    {
        "summary": {"type": "string", "maxLength": 1000},
        "learned": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 300},
        },
        "lessons": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
    },
    ["summary", "learned", "lessons"],
)


DIRECTOR_TOOLS = [
    HISTORY_TOOL,
    PLAN_DAY_TOOL,
    UPDATE_FARM_PLAN_TOOL,
    REFLECT_TOOL,
    function_tool(
        "continue_objective",
        "Keep the active objective and set its next concrete milestone in one or two sentences.",
        {"milestone": {"type": "string", "maxLength": 400}, "reason": {"type": "string", "maxLength": 400}},
        ["milestone", "reason"],
    ),
    function_tool(
        "block_objective",
        "Block the active objective only when a concrete prerequisite makes progress impossible.",
        {"evidence": {"type": "string", "maxLength": 600}},
        ["evidence"],
    ),
    function_tool(
        "set_objective",
        "Set a new objective only when no objective is active. The harness completes it automatically once success_condition is verified against structured state.",
        {
            "goal": {"type": "string", "maxLength": 400},
            "success_condition": {"type": "string", "maxLength": 300},
            "milestone": {"type": "string", "maxLength": 400},
            "agenda_id": {"type": "string", "maxLength": 40},
        },
        ["goal", "success_condition", "milestone"],
    ),
]
