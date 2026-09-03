"""Bound ordinary controls and return to the actor at real decision points."""

import time
from typing import Any, Callable


class AttentionYield(Exception):
    def __init__(self, reason: str, state: dict[str, Any], controls: int):
        self.result = {"status": "yielded", "reason": reason, "state": state, "controls_executed": controls}
        super().__init__(reason)


class AttentiveBridge:
    def __init__(self, bridge, state: dict[str, Any], pending: Callable[[], bool], urgent: bool = False):
        self.bridge = bridge
        self.state = state
        self.pending = pending
        self.urgent = urgent
        self.controls = 0
        self.started = time.monotonic()
        self.location = state.get("location")
        self.quest_revision = state.get("questRevision")

    def request(self, kind: str, **arguments: Any) -> dict[str, Any]:
        if self.pending() and not self.state.get("nightActive"):
            raise AttentionYield("operator_pending", self.state, self.controls)
        if kind in {"navigate", "go_to_location"}:
            arguments.update(segmentTicks=300, noticeEncounters=not self.urgent)
        response = self.bridge.request(kind, **arguments)
        self.controls += 1
        self.state = response.get("state") or self.state
        if response.get("status") == "yielded":
            raise AttentionYield(response.get("reason", "movement_segment_finished"), self.state, self.controls)
        if self.pending() and not self.state.get("nightActive"):
            raise AttentionYield("operator_pending", self.state, self.controls)
        if not self.urgent:
            if self.state.get("location") != self.location:
                raise AttentionYield("arrived_in_new_area", self.state, self.controls)
            if self.state.get("questRevision") != self.quest_revision:
                raise AttentionYield("journal_changed", self.state, self.controls)
            if time.monotonic() - self.started >= 8:
                raise AttentionYield("time_to_look_around", self.state, self.controls)
        return response

    def __getattr__(self, name):
        return getattr(self.bridge, name)
