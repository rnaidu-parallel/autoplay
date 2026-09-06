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
            # Walk whole segments; villagers seen on the way are greeted by the reflex at the next boundary.
            arguments.update(segmentTicks=900, noticeEncounters=False)
        response = self.bridge.request(kind, **arguments)
        self.controls += 1
        self.state = response.get("state") or self.state
        if response.get("status") == "yielded":
            raise AttentionYield(response.get("reason", "movement_segment_finished"), self.state, self.controls)
        if self.pending() and not self.state.get("nightActive"):
            raise AttentionYield("operator_pending", self.state, self.controls)
        if not self.urgent and self.state.get("location") != self.location:
            raise AttentionYield("arrived_in_new_area", self.state, self.controls)
        return response

    def __getattr__(self, name):
        return getattr(self.bridge, name)
