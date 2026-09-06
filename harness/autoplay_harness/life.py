"""Persistent player-visible information and explicit responses to important changes."""
import hashlib
import time
from typing import Any

from .calendar import calendar_day
from .objectives import evaluate_state_condition


def _id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def add_notice(life: dict, notice: dict) -> None:
    notices = life.setdefault("notices", {})
    if any(n["text"] == notice["text"] and n.get("day") == notice.get("day")
           and n["kind"] in {"hud", "new_tool"} and notice["kind"] in {"hud", "new_tool"} for n in notices.values()):
        return
    if notice["id"] not in notices:
        notices[notice["id"]] = {**notice, "status": "pending"}
        if notice["kind"] == "hud":
            life.pop("responding_to", None)


def observe(life: dict, state: dict) -> None:
    day = calendar_day(state)
    notices = life.setdefault("notices", {})
    expire_notices(life, state)
    for notice in notices.values():
        if notice.get("kind") == "conversation":
            notice.update(status="completed", completed=True)
    responding_to = life.get("responding_to")
    if responding_to in notices and notices[responding_to].get("kind") == "conversation":
        life.pop("responding_to", None)
    if any(str(text).strip().casefold() == "inventory full" for text in state.get("hudMessages", [])):
        life["inventory_blocked"] = True
    if state.get("menu") == "LetterViewerMenu" and any(entry.get("canAccept") is False for entry in state.get("menuEntries", [])):
        life["inventory_blocked"] = True
    texts = life.setdefault("texts", {})
    for notice in state.get("notices", []):
        if notice["id"] in texts:
            continue
        texts[notice["id"]] = notice
        if notice["kind"] in {"hud", "new_tool", "level_up"}:
            if notice["text"].strip().casefold() == "inventory full":
                life["inventory_blocked"] = True
            else:
                # Repeated display of the same message on one day is one decision.
                key = _id(str([notice.get(k) for k in ("year", "season", "day", "kind", "text")]))
                add_notice(life, {**notice, "id": key})
        if notice["kind"] == "dialogue":
            life.setdefault("conversation", []).append(notice["id"])
    if life.get("conversation") and not state.get("eventUp") and not str(state.get("menu", "")).startswith("DialogueBox"):
        conversation = [texts[key] for key in life.pop("conversation")]
        content = "\n".join(dict.fromkeys(item["text"] for item in conversation))
        if not (state.get("location") == "FarmHouse" and "sleep" in content.casefold()):
            identity = "conversation:" + conversation[-1]["id"]
            completed = {**conversation[-1], "id": identity, "kind": "conversation", "text": content,
                         "status": "completed", "completed": True}
            notices.setdefault(identity, completed)
            texts[identity] = completed
        # The dialogue itself fulfilled the encounter response. Do not reopen it to
        # acknowledge a transcript that was retained after the exchange closed.
        life.pop("responding_to", None)
    if "quests" in state:
        current = {quest["id"]: quest for quest in state["quests"]}
        for key, quest in current.items():
            life["quests"][key] = quest
        life["active_quest_ids"] = list(current)
    if state.get("location") == "Farm" and state.get("mailCount") == 0:
        life["mail_checked_day"] = day
    if state.get("inventoryFreeSlots", 1) > 0:
        life["inventory_blocked"] = False
    # Migration: a special tool acquired before notice capture must not vanish from attention.
    known_tools = life.setdefault("known_tools", [])
    for item in state.get("inventory", []):
        if item.get("isTool") and item["qualifiedId"] not in known_tools:
            known_tools.append(item["qualifiedId"])
            if item["name"] not in {"Axe", "Hoe", "Watering Can", "Pickaxe", "Scythe"}:
                add_notice(life, {"id": "tool:" + item["qualifiedId"], "kind": "new_tool", "text": item["name"],
                                  "location": state.get("location"), "day": state.get("day")})
    # A changed condition makes a deferred decision due again. Do not re-announce unchanged scenery.
    for notice in life.setdefault("notices", {}).values():
        if notice.get("status") == "deferred" and (evaluate_state_condition(notice.get("revisit_when"), state) or (False,))[0]:
            notice["status"] = "pending"
    for item in (life.get("quest_review") or {}).get("deferred", []):
        if not item.get("triggered") and (evaluate_state_condition(item.get("revisit_when"), state) or (False,))[0]:
            item["triggered"] = True
            life["quest_reviewed_revision"] = None
    if state.get("menu") == "none" and not state.get("eventUp"):
        here = state.get("location")
        targets = [(f"npc:{npc.get('id', npc['name'])}:{day}", "encounter", f"{npc['name']} nearby; talked today: {npc.get('talkedToday')}")
                   for npc in state.get("npcsNearby", []) if npc.get("kind") == "Villager" and not npc.get("talkedToday")
                   and abs(npc['x'] - state.get('tileX', 0)) + abs(npc['y'] - state.get('tileY', 0)) <= 6]
        targets += [(f"place:{here}:{action['x']}:{action['y']}:{action['value']}", "place", action['value'])
                    for action in state.get("nearbyActions", []) if action.get("kind") == "Action"
                    and not str(action.get("value", "")).startswith("Warp")
                    and abs(action['x'] - state.get('tileX', 0)) + abs(action['y'] - state.get('tileY', 0)) <= 3]
        # One local discovery at a time. The rest remains visible on the next observation.
        for key, kind, text in targets:
            if key not in life["notices"]:
                add_notice(life, {"id": key, "kind": kind, "text": text, "location": here,
                                  "day": state.get("day"), "season": state.get("season"), "year": state.get("year"),
                                  "time": state.get("time"), "seen_at": time.time(),
                                  "target_id": key.split(":")[1] if kind == "encounter" else None})
                break


def encounter_target(notice: dict, state: dict) -> dict | None:
    identity = notice.get("target_id") or notice["id"].split(":")[1]
    return next((npc for npc in state.get("npcsNearby", [])
                 if npc.get("id", npc.get("name")) == identity), None)


def expire_notices(life: dict, state: dict) -> None:
    if not state.get("worldReady"):
        return
    for notice in life.get("notices", {}).values():
        if notice.get("status") not in {"pending", "deferred", "acted"}:
            continue
        kind = notice.get("kind")
        # HUD announcements are dated opportunities; tools and quests persist.
        wrong_day = notice.get("day") != state.get("day") or any(
            notice.get(key) is not None and notice[key] != state.get(key) for key in ("season", "year"))
        reason = None
        if kind in {"hud", "encounter", "place"} and wrong_day:
            reason = "The notice belongs to a previous game day."
        elif kind in {"encounter", "place"} and notice.get("location") != state.get("location"):
            reason = "The player has left this location."
        elif kind == "encounter":
            target = encounter_target(notice, state)
            if target is None or state.get("eventUp"):
                reason = "The target is no longer available in the current scene."
            elif target.get("talkedToday"):
                notice.update(status="completed", completed=True)
        if reason:
            notice.update(status="expired", expiry_reason=reason)
        if notice.get("status") in {"expired", "completed"} and life.get("responding_to") == notice["id"]:
            life.pop("responding_to", None)


def mail_due(life: dict, state: dict) -> bool:
    """Unread mail, or a mailbox not yet looked at today. Information for the farmer, never a gate."""
    return (state.get("location") in {"Farm", "FarmHouse"} and isinstance(state.get("mailCount"), int)
            and (state["mailCount"] > 0 or life.get("mail_checked_day") != calendar_day(state)))


def pending(life: dict) -> list[dict]:
    priority = {"encounter": 0, "new_tool": 1, "hud": 2, "conversation": 3, "level_up": 4, "place": 5}
    return sorted([n for n in life.get("notices", {}).values() if n.get("status") == "pending"],
                  key=lambda n: priority.get(n["kind"], 6))


def response_due(life: dict, state: dict) -> bool:
    # Let an accepted next step happen before another discovery replaces it.
    return bool(pending(life)) and not life.get("responding_to")


def respond(life: dict, notice_id: str, choice: str, next_step: str, reason: str, revisit_when: str, state: dict) -> dict:
    expire_notices(life, state)
    notice = life.get("notices", {}).get(notice_id)
    if not notice or notice.get("status") != "pending":
        raise ValueError("Choose an outstanding notice ID from life.pending.")
    if choice not in {"act", "defer"} or not reason.strip() or not next_step.strip():
        raise ValueError("Choose act or defer with a concrete next step and reason.")
    if choice == "defer":
        condition = evaluate_state_condition(revisit_when, state)
        if not condition or condition[0] or any("unavailable" in error for error in condition[1]):
            raise ValueError("A deferral needs a currently false condition using available state, e.g. time >= 1700 or inventoryFreeSlots > 0.")
    notice.update(status="acted" if choice == "act" else "deferred", next_step=next_step, reason=reason,
                  revisit_when=revisit_when if choice == "defer" else None)
    if choice == "act":
        life["responding_to"] = notice_id
    return notice


def context(life: dict, state: dict) -> dict[str, Any]:
    quests = [life["quests"][key] for key in life.get("active_quest_ids", life.get("quests", {})) if key in life["quests"]]
    queued = pending(life)
    return {"unreadMail": not state.get("eventUp") and mail_due(life, state),
            "inventoryBlocked": life.get("inventory_blocked", False),
            "quests": [{k: q.get(k) for k in ("id", "title", "objectives", "daysLeft", "reward", "complete")} for q in quests],
            "pending": [{**{k: n.get(k) for k in ("id", "kind", "location", "day", "time")}, "text": n["text"][:220]} for n in queued[:4]],
            "otherPendingCount": max(0, len(queued) - 4),
            "recentText": [{"id": n["id"], "kind": n["kind"], "day": n.get("day"), "text": n["text"][:180]}
                           for n in list(life.get("texts", {}).values())[-3:]
                           if n.get("kind") not in {"hud", "dialogue"} or calendar_day(n) == calendar_day(state)],
            "questReview": life.get("quest_review"),
            "respondingTo": life.get("responding_to"),
            "availableMenuTabs": ["inventory", "skills", "social", "map", "crafting", "collections", "options"],
            "retrieval": "read_life_text returns a full notice or quest description by ID; unabridged text persists in the notebook."}


def guard(life: dict, name: str, args: dict, state: dict, urgent: bool = False) -> str | None:
    if urgent or state.get("eventUp") or state.get("nightActive"):
        return None
    if name == "click_menu_entry":
        entries = state.get("menuEntries", [])
        index = args.get("index", -1)
        if isinstance(index, int) and 0 <= index < len(entries) and entries[index].get("canAccept") is False:
            return "Inventory cannot accept this item. Store or sell suitable items first; do not discard tools or quest items."
    if state.get("menu") != "none":
        return None
    full = state.get("inventoryFreeSlots") == 0
    if full and name == "clear_debris":
        return "Inventory is full. Make room before gathering more materials."
    if full and name in {"press", "hold", "click"}:
        buttons = args.get("buttons", [])
        collecting = bool(set(buttons) & {"X", "C"}) or (name == "click" and args.get("button") == "right")
        # Still allow storage, shops and conversations which can solve the problem.
        nearby = lambda obj: abs(obj['x']-state.get('tileX', 0))+abs(obj['y']-state.get('tileY', 0)) <= 1
        storage = any("chest" in str(obj.get("name", "")).casefold() and nearby(obj) for obj in state.get("nearbyObjects", []))
        crop = next((crop for crop in state.get("cropsNearby", []) if crop.get("readyToHarvest") and nearby(crop)), None)
        crop_fits = crop and any(item.get("name") == crop.get("crop") and item.get("stack", 0) < item.get("maxStack", 0) for item in state.get("inventory", []))
        blocked = life.get("inventory_blocked") or (crop and not crop_fits)
        if collecting and blocked and not storage and not any(nearby(npc) for npc in state.get("npcsNearby", [])) and not any(nearby(action) for action in state.get("nearbyActions", [])):
            return "An Inventory Full attempt already failed. Make room before repeating collection."
    return None


def pickup_failed(name: str, args: dict, before: dict, after: dict) -> bool:
    picking = (name in {"press", "hold"} and "X" in args.get("buttons", [])) or (name == "click" and args.get("button") == "right")
    return bool(picking and before.get("menu") == after.get("menu") == "none"
                and before.get("inventoryCounts") == after.get("inventoryCounts")
                and any(str(text).strip().casefold() == "inventory full" for text in after.get("hudMessages", [])))
