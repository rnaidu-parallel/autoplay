"""Tool surface for the single-agent day session: the kept actor tools plus objective and diary tools."""
from __future__ import annotations

import copy

from .tools import ACTOR_TOOLS, UPDATE_FARM_PLAN_TOOL, function_tool


KEPT = {
    "press", "hold", "control_sequence", "move_cursor", "click", "scroll", "open_menu_tab", "click_menu_entry",
    "close_menu", "inventory_move", "inventory_trash", "ship_item", "wait", "idle", "choose_dialogue_response",
    "navigate_to", "go_to_location", "travel_to", "world_map", "plant_seeds", "plant_nearest_seeds",
    "water_crops", "till_tiles", "clear_debris", "check_mail", "look_at", "go_home_and_sleep",
    "read_life_text", "wiki_search", "search_history", "stop_session",
}

NEW_TOOLS = [
    function_tool(
        "set_objective",
        "Replace your objective in one move and record what became of the old one. There is always exactly one "
        "objective. kind: mission (from the journal), event (a festival or a timed happening), or free (what you "
        "feel like). done_when is your own words; you judge it. source: quest:<id>, event:<name>, letter:<id>, "
        "chat:<id> or operator when the idea came from there. check: optional structured comparison such as "
        "\"inventory.Parsnip Seeds >= 15\" or \"location is Town\"; the state reports whether it holds.",
        {
            "kind": {"type": "string", "enum": ["mission", "event", "free"]},
            "goal": {"type": "string", "minLength": 1, "maxLength": 160},
            "why": {"type": "string", "minLength": 1, "maxLength": 400},
            "done_when": {"type": "string", "minLength": 1, "maxLength": 300},
            "source": {"type": "string", "maxLength": 60},
            "check": {"type": "string", "maxLength": 200},
            "previous_outcome": {"type": "string", "enum": ["completed", "abandoned", "interrupted"]},
            "previous_note": {"type": "string", "maxLength": 200},
        },
        ["kind", "goal", "why", "done_when", "previous_outcome"],
    ),
    function_tool(
        "refill_watering_can",
        "Walk to the nearest farm pond edge, face the water and fill the watering can. Verified by the water count. "
        "water_crops does this by itself when the can is empty.",
        {},
        [],
    ),
    function_tool(
        "diary_write",
        "Write a proper diary entry in your own words: what you tried, what worked, what you liked or disliked, "
        "who said what, what to remember tomorrow. kind bedtime marks the day's closing entry.",
        {"text": {"type": "string", "minLength": 1, "maxLength": 1500},
         "kind": {"type": "string", "enum": ["entry", "bedtime"]}},
        ["text"],
    ),
    function_tool(
        "diary_search",
        "Search your diary from earlier days for a person, place, thing or feeling. Returns dated excerpts.",
        {"query": {"type": "string", "minLength": 1, "maxLength": 120},
         "days": {"type": "integer", "minimum": 1, "maximum": 60}},
        ["query"],
    ),
    function_tool(
        "diary_read",
        "Read one earlier day of your diary in full. day is the calendar day number shown in diary excerpts.",
        {"day": {"type": "integer", "minimum": 1}},
        ["day"],
    ),
    UPDATE_FARM_PLAN_TOOL,
]

COMMON_FIELDS = {
    "say": {
        "type": "string", "minLength": 1, "maxLength": 320,
        "description": "Neon's voice for the people watching, when there is something to say: a reaction to what just "
                       "happened, a choice, a discovery, a setback, a promise, a question to chat. One to three short "
                       "sentences, present tense. Leave it out when nothing new happened since the last line. Never tool "
                       "names, coordinates, brackets, the words menu, tooltip, cursor, click, tab, scroll or tile, or "
                       "anything about being an AI or a program.",
    },
    "chat": {
        "type": "string", "minLength": 1, "maxLength": 200,
        "description": "A line posted to the chat as Neon, when he answers a viewer or asks them something. "
                       "Plain words, no names of anyone off the stream.",
    },
    "diary": {
        "type": "string", "maxLength": 300,
        "description": "Optional short diary note to keep alongside this action, in your own words. Never mentions the "
                       "stream or being watched.",
    },
    "think_harder": {
        "type": "boolean",
        "description": "Set true when the next decision deserves more thought than usual.",
    },
}


def _with_common_fields(tool: dict) -> dict:
    tool = copy.deepcopy(tool)
    parameters = tool["function"]["parameters"]
    parameters["properties"].update(copy.deepcopy(COMMON_FIELDS))
    return tool


AGENT_TOOLS = [_with_common_fields(tool) for tool in ACTOR_TOOLS if tool["function"]["name"] in KEPT] + [
    _with_common_fields(tool) for tool in NEW_TOOLS
]

AGENT_TOOL_NAMES = [tool["function"]["name"] for tool in AGENT_TOOLS]

# Tools that send game input or change durable state; the loop writes action_started before running them.
INFORMATIONAL = {"world_map", "read_life_text", "wiki_search", "search_history", "diary_search", "diary_read"}
