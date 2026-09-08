"""Notice when an activity has gone quiet so the character can change his mind.

This never ends a run. A live stream must keep going, so a drought of new game evidence
asks for a replan first and, if that does not help, escalates to a scene change.
"""
import json
import time

from .calendar import calendar_day


class ActivityProgress:
    REPLAN_SECONDS = 45
    REPLAN_DECISIONS = 8
    ESCALATE_SECONDS = 150
    ESCALATE_DECISIONS = 30

    def __init__(self):
        self.day = None
        self.seen = set()
        self.last_progress = time.monotonic()
        self.decisions = 0
        self.warned = False

    def reset(self):
        """A replan or a new intention starts a fresh drought clock."""
        self.last_progress = time.monotonic()
        self.decisions = 0
        self.warned = False

    def observe(self, state, decision=False):
        if not state.get("worldReady"):
            return None
        now = time.monotonic()
        day = calendar_day(state)
        if day != self.day:
            self.day, self.seen = day, set()
        # Positions, menus, prose, clock ticks, and spent stamina are not milestones.
        # Remember evidence already seen so toggling inventory/routes cannot reset us.
        facts = {(key, json.dumps(state[key], sort_keys=True)) for key in (
            "location", "inventoryCounts", "money", "seedsSown", "plantedCrops",
            "wateredCrops", "tilledTiles", "harvestableCrops", "questRevision",
            "mailCount", "saveCount", "eventId", "shippingBinCount", "minigame",
        ) if key in state}
        if state.get("eventId") is not None:
            facts.add(("eventPhase", json.dumps([state["eventId"], state.get("eventPhase")])))
        facts.update(("talked", npc.get("id", npc.get("name")))
                     for npc in state.get("npcsNearby", []) if npc.get("talkedToday"))
        if facts - self.seen:
            self.seen.update(facts)
            self.reset()
            return None
        if decision:
            self.decisions += 1
        elapsed = now - self.last_progress
        if elapsed >= self.ESCALATE_SECONDS or self.decisions >= self.ESCALATE_DECISIONS:
            self.reset()
            return "escalate"
        if not self.warned and (elapsed >= self.REPLAN_SECONDS or self.decisions >= self.REPLAN_DECISIONS):
            self.warned = True
            return "replan"
        return None
