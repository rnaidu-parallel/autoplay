"""Single-agent day session: one persona, one conversation per game day, a diary he owns, and a few
physics-level circuit breakers. Everything that decides *what to do* belongs to the agent.

Reuses the existing harness for observation, reflexes, verified skills, execution dispatch, operator
polling, checkpoints and telemetry; replaces the step, the context and the director entirely.
See docs/specs/one-agent-day-session.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .agent_prompt import AGENT_SYSTEM_PROMPT
from .agent_tools import AGENT_TOOLS, INFORMATIONAL

# Pathed movement and the chore helpers are refused by the game while an event (festival, cutscene) is up.
EVENT_UNAVAILABLE = {"navigate_to", "go_to_location", "travel_to", "check_mail", "plant_seeds", "plant_nearest_seeds",
                     "till_tiles", "water_crops", "clear_debris", "go_home_and_sleep", "look_at"}
EVENT_TOOLS = [tool for tool in AGENT_TOOLS if tool["function"]["name"] not in EVENT_UNAVAILABLE]
# A festival is an event he may walk around in: the bridge keeps pathed movement and the map's action tiles.
FESTIVAL_TOOLS = [tool for tool in AGENT_TOOLS if tool["function"]["name"] not in EVENT_UNAVAILABLE - {"navigate_to"}]


def walking_at_festival(state: dict[str, Any]) -> bool:
    return bool(state.get("eventUp") and state.get("festival") and state.get("eventCanMove"))

# Doors the bridge's world graph does not list (they are building doors, not warps). Until the bridge learns them,
# the observation says where they are so a day is not spent looking. Format: (from, to): how to enter.
DOORS_NOT_ON_MAP = {
    ("Town", "CommunityCenter"): "in Town, navigate_to tile 52,20 then hold W into the doors just north of it",
}
from .calendar import calendar_day
from .capture import Frame
from .diary import Diary, game_time
from .farming import refill_watering_can
from .objectives import evaluate_state_condition
from .openrouter import OpenRouterError, ToolDecision
from .progress import ActivityProgress
from .runner import AutoplayHarness
from .session import DaySession, compact_result


# effort, output cap, call timeout
DEPTHS = {"low": ("low", 2000, 60), "medium": ("medium", 6000, 90), "high": ("high", 12000, 120)}
FAILED = {"blocked", "rejected", "error", "timeout", "unknown"}
COUNTED_FAILURES = {"blocked", "rejected", "error", "timeout"}
STALL_ADVICE_DECISIONS = 8
STALL_DECISIONS = 12
STALL_SECONDS = 150  # ~12 decisions at this model's latency; 90 s fired after six while he was lawfully wandering Town
SAME_TARGET_LIMIT = 3
GUIDANCE_DECISIONS = 6
COMMON_FIELDS = ("say", "diary", "chat", "think_harder")
BEARINGS = {"kind": "free", "goal": "Get my bearings", "why": "A fresh start; I want to know where things stand.",
            "done_when": "I know what needs doing today"}
# Words that must never reach the public stream through his mouth or his diary (comma-separated env var).
PRIVATE_WORDS = [word.strip() for word in os.environ.get("AUTOPLAY_PRIVATE_WORDS", "").split(",") if word.strip()]


def redact(text: Any) -> Any:
    if not isinstance(text, str) or not PRIVATE_WORDS:
        return text
    for word in PRIVATE_WORDS:
        text = re.sub(re.escape(word), "the operator", text, flags=re.IGNORECASE)
    return text


def _held_name(state: dict[str, Any]) -> str | None:
    held = state.get("cursorItem")
    if isinstance(held, dict):
        stack = held.get("stack")
        return f"{stack} {held.get('name')}" if isinstance(stack, int) and stack > 1 else held.get("name")
    return held or None


def action_effects(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What an action actually changed, so a delivered input is never mistaken for an achieved effect.

    Raw inputs (click, hold, press) only report that the input was sent; this is the difference between
    the world before and after, in the terms he reasons in: money, bag, cursor, place, menu, dialogue."""
    effects: dict[str, Any] = {}
    if isinstance(before.get("money"), int) and isinstance(after.get("money"), int) and after["money"] != before["money"]:
        effects["money"] = f"{after['money'] - before['money']:+d}"
    counts_before = before.get("inventoryCounts") or {}
    counts_after = after.get("inventoryCounts") or {}
    bag = {name: (counts_after.get(name) or 0) - (counts_before.get(name) or 0)
           for name in set(counts_before) | set(counts_after)}
    bag = {name: f"{delta:+d}" for name, delta in sorted(bag.items()) if delta}
    if bag:
        effects["bag"] = bag
    if _held_name(before) != _held_name(after):
        effects["cursorItem"] = _held_name(after) or "put away"
    if before.get("location") != after.get("location"):
        effects["location"] = f"{before.get('location')} -> {after.get('location')}"
    elif all(isinstance(state.get(key), int) for state in (before, after) for key in ("tileX", "tileY")):
        moved = abs(after["tileX"] - before["tileX"]) + abs(after["tileY"] - before["tileY"])
        if moved:
            effects["movedTiles"] = moved
    if (before.get("menu") or "none") != (after.get("menu") or "none"):
        effects["menu"] = f"{before.get('menu') or 'none'} -> {after.get('menu') or 'none'}"
    if after.get("dialogueText") and after.get("dialogueText") != before.get("dialogueText"):
        effects["dialogue"] = str(after["dialogueText"])[:120]
    if isinstance(before.get("stamina"), int) and isinstance(after.get("stamina"), int) and after["stamina"] != before["stamina"]:
        effects["stamina"] = f"{after['stamina'] - before['stamina']:+d}"
    return effects


class AgentProgress(ActivityProgress):
    REPLAN_DECISIONS = STALL_ADVICE_DECISIONS
    REPLAN_SECONDS = 10 ** 9  # advice is by decisions; the clock only drives the scene change
    ESCALATE_DECISIONS = STALL_DECISIONS
    ESCALATE_SECONDS = STALL_SECONDS

    AREA_RESETS = 4  # new ground counts as something happening, but not forever: after this many, only real evidence does

    def observe(self, state, decision=False):
        """A curious character explores: a new part of a place, or a fish on the line, is something happening."""
        if state.get("worldReady"):
            if calendar_day(state) != self.day:
                self.day, self.seen = calendar_day(state), set()
                self.area_resets = 0
            seen_before = len(self.seen)
            extra = set()
            if isinstance(state.get("tileX"), int) and isinstance(state.get("tileY"), int):
                extra.add(("area", json.dumps([state.get("location"), state["tileX"] // 8, state["tileY"] // 8])))
            if state.get("menu") == "BobberBar":
                extra.add(("fishing", "bite"))
            if state.get("dialogueText"):
                extra.add(("dialogue", str(state["dialogueText"])[:80]))  # someone said something new: a conversation happened
            new = extra - self.seen
            self.seen.update(new)
            area_only = new and all(fact[0] == "area" for fact in new)
            if new and (not area_only or getattr(self, "area_resets", 0) < self.AREA_RESETS):
                if area_only:
                    self.area_resets = getattr(self, "area_resets", 0) + 1
                self.reset()
            action = super().observe(state, decision)
            if len(self.seen) > seen_before + len(new):  # real evidence arrived in the base set
                self.area_resets = 0
            return action
        return super().observe(state, decision)


class AgentHarness(AutoplayHarness):
    PROMPT_VERSION = hashlib.sha256(
        (AGENT_SYSTEM_PROMPT + json.dumps(AGENT_TOOLS, sort_keys=True)).encode()).hexdigest()[:16]

    def __init__(self, repository_root: Path, model: str, api_key: str, isolated_state: bool = False,
                 max_days: int | None = None, **kwargs: Any) -> None:
        ledger_path = (repository_root / "harness" / "state" / "objectives.json") if not isolated_state else None
        # A ledger that already exists keeps its objective across restarts; only a new one needs a seed.
        seed = None if ledger_path is not None and ledger_path.exists() else BEARINGS["goal"]
        super().__init__(repository_root, seed, "" if seed else None, model, api_key,
                         isolated_state=isolated_state, actor_mode="visual", **kwargs)
        self._init_agent()
        self.max_days = max_days

    def _init_agent(self) -> None:
        self.max_days: int | None = None
        self.days_played = 0
        self.diary: Diary | None = None
        self.session: DaySession | None = None
        self.session_id: str | None = None
        self.seq = 0
        self.progress = AgentProgress()
        self.failed_targets: dict[str, int] = {}
        self.target_context: tuple[Any, ...] | None = None
        self.think_harder_next = False
        self.stall_advice = False
        self.first_call_of_day = True
        self.notes_pending: list[str] = []
        self.chat_seen: set[str] = set()
        self.request_seen: str | None = None
        self.recent_actions: list[dict[str, Any]] = []
        self.guidance_decisions_left = 0
        self.guidance_seen = ""
        self.decision_seq: int | None = None

    # ---- the pieces of the old loop this design does without ----

    def _plan_day_if_needed(self, state: dict[str, Any], frame: Frame) -> None:
        day = calendar_day(state)
        if isinstance(day, int) and self.notebook.current_day != day:
            self.notebook.start_day(day)  # keeps mail, quest and audience expiry working

    def _request_agenda(self, state: dict[str, Any], frame: Frame, mode: str) -> bool:
        return True  # the demand is shown in the observation instead

    def _start_director_review(self) -> None:
        pass

    def _finish_director_review(self, wait: bool = False, apply: bool = True) -> None:
        pass

    def _director_review(self) -> None:
        pass

    def _record_actor_lessons(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _reflect_before_sleep(self, state: dict[str, Any], frame: Frame) -> None:
        pass

    def _complete_verified_objective(self, state: dict[str, Any]) -> bool:
        active = self.ledger.data.get("active")
        if active and active.get("operator_finish"):
            return super()._complete_verified_objective(state)
        return False

    # ---- stall handling: advice, then one scene change ----

    def _check_activity_progress(self, state: dict[str, Any], decision: bool = False) -> None:
        seen_before = len(self.progress.seen)
        action = self.progress.observe(state, decision)
        if len(self.progress.seen) > seen_before:
            self.stall_advice = False
        if action == "replan":
            self.stall_advice = True
            self.telemetry.record("stall_advice", {**self._ids(), "decisions": self.progress.decisions})
        elif action == "escalate":
            evening = (state.get("time") or 0) >= 2000 or (isinstance(state.get("stamina"), int) and state["stamina"] < 30)
            if evening and not state.get("eventUp") and not state.get("festival"):
                self._change_scene_by_harness(state)
            else:
                # His objective stays his. A festival freezes the clock, and by day a quiet stretch is his to
                # notice: the advice keeps coming until something new happens.
                self.stall_advice = True
                self.progress.reset()
                self.telemetry.record("stall_advice", {**self._ids(), "decisions": self.progress.decisions,
                                                       "event": bool(state.get("eventUp") or state.get("festival"))})

    def _change_scene_by_harness(self, state: dict[str, Any]) -> None:
        """The one objective the harness still sets: bed, when it is late and nothing has happened for a while."""
        objective = {"kind": "free", "goal": "Go home and sleep",
                     "why": "It is late and nothing new has happened for a while.", "done_when": "I am in bed"}
        replaced = (self.ledger.data.get("active") or {}).get("goal")
        self._set_objective({**objective, "previous_outcome": "interrupted", "previous_note": "stalled"}, state, by_harness=True)
        self.blocked_movements.clear()
        self.failed_targets = {}
        self.last_action_fingerprint = None
        self.stall_advice = False
        self.notes_pending.append(f"(It is late and nothing new had happened for a while, so the harness set \"{replaced}\" "
                                  "aside for bed; it is in your recent objectives for tomorrow.)")
        self.telemetry.record("scene_changed_by_harness", {**self._ids(), "objective": objective["goal"]})

    def _stall_deadline_remaining(self) -> float:
        return max(0.0, STALL_SECONDS - (time.monotonic() - self.progress.last_progress))

    # ---- same-target circuit breaker (physics, not judgment) ----

    def _target_key(self, decision: ToolDecision, state: dict[str, Any]) -> str:
        name = decision.name
        arguments = {key: value for key, value in decision.arguments.items() if key not in COMMON_FIELDS and key != "frame_id"}
        if name in {"go_to_location", "travel_to"}:
            name, arguments = "travel", {"destination": arguments.get("location") or arguments.get("destination")}
        return json.dumps([state.get("location"), name, arguments], sort_keys=True)

    def _target_context_key(self, state: dict[str, Any]) -> tuple[Any, ...]:
        return (state.get("location"), calendar_day(state), json.dumps(state.get("inventoryCounts") or {}, sort_keys=True),
                json.dumps([self.world.blocked_paths, self.world.unreachable_edges], sort_keys=True, default=str))

    def _same_target_blocked(self, decision: ToolDecision, state: dict[str, Any]) -> bool:
        if decision.name == "go_home_and_sleep":
            return False
        context = self._target_context_key(state)
        if context != self.target_context:
            self.target_context, self.failed_targets = context, {}
        return self.failed_targets.get(self._target_key(decision, state), 0) >= SAME_TARGET_LIMIT

    def _track_action_retries(self, decision: ToolDecision, result: dict[str, Any],
                              before: dict[str, Any], after: dict[str, Any]) -> None:
        context = self._target_context_key(before)
        if context != self.target_context:
            self.target_context, self.failed_targets = context, {}
        target = self._target_key(decision, before)
        status = result.get("status")
        if status in COUNTED_FAILURES and not result.get("circuit_breaker"):
            self.failed_targets[target] = self.failed_targets.get(target, 0) + 1
        elif status == "completed":
            self.failed_targets.pop(target, None)

    # ---- session boundary: one conversation per game day ----

    def _ids(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "session_id": self.session_id, "seq": self.seq}

    def _ensure_session(self, state: dict[str, Any]) -> None:
        day = calendar_day(state)
        if not isinstance(day, int):
            return
        save = str(state.get("saveId") or "unknown")
        session_id = f"{save}:{day}"
        if session_id == self.session_id:
            return
        if self.session_id is not None and self.max_days is not None:
            self.days_played += 1
            if self.days_played >= self.max_days:
                self.stop_reason = "max_days_reached"
                self.telemetry.record("max_days_reached", {"days": self.days_played})
                return
        self.diary = Diary(self.state_directory, save)
        runs = self.repository_root / "harness" / "runs"
        session = DaySession.rebuild(runs, session_id)
        rebuilt = bool(session.turns)
        self.session, self.session_id = session, session_id
        self.seq = 0
        self.progress = AgentProgress()
        self.failed_targets = {}
        self.stall_advice = False
        self.first_call_of_day = True
        self.recent_actions = []
        versions = self._prompt_versions(runs, session_id)
        self.telemetry.record("agent_session_started", {**self._ids(), "prompt_version": self.PROMPT_VERSION,
                                                        "rebuilt": rebuilt, "bundles": session.bundle_count(),
                                                        "unresolved": list(getattr(session, "unresolved_call_ids", []))})
        if rebuilt:
            self.telemetry.record("transcript_rebuilt", {**self._ids(), "bundles": session.bundle_count(),
                                                         "unresolved": list(getattr(session, "unresolved_call_ids", []))})
            self.notes_pending.append("(You are back after a restart; the state below is current.)")
            if versions - {self.PROMPT_VERSION}:
                self.notes_pending.append("(Your instructions changed while you were away.)")
        last = self.diary.last_entry(day)
        if last:
            self.notes_pending.append(f"(Last night you wrote: {last['text']})")
        elif not rebuilt:
            self.notes_pending.append("(Your diary is empty; this is your first day here.)")
        active = self.ledger.data.get("active")
        if active and active.get("by_harness") and active.get("saveId") == save and not rebuilt:
            self._set_objective({**BEARINGS, "previous_outcome": "interrupted", "previous_note": "the day ended"},
                                state, by_harness=True)
            self.notes_pending.append("(A new day. Yesterday ended on a placeholder objective; set your own now.)")
            active = self.ledger.data.get("active")
        if active and active.get("saveId") is None:
            # Written before save scoping existed, by the seed, or by Finish & Save: still his, just older.
            active.update({"saveId": save, "kind": active.get("kind") or "free", "why": active.get("why") or "",
                           "done_when": active.get("done_when") or active.get("milestone") or "",
                           "source": active.get("source"), "check": active.get("check"),
                           "started": active.get("started") or {"day": day, "time": state.get("time")}})
            self.ledger._save()
        elif not active or active.get("saveId") != save:
            self._set_objective({**BEARINGS, "previous_outcome": "interrupted",
                                 "previous_note": "a different save or a fresh start"}, state, by_harness=True)
        elif not active.get("by_harness") and isinstance((active.get("started") or {}).get("day"), int) \
                and active["started"]["day"] < day:
            self._carry_objective_over(state)

    def _carry_objective_over(self, state: dict[str, Any]) -> None:
        """His objective outlived its day: close yesterday's record and reopen it today, so its history counts the days."""
        data = self.ledger.data
        active = data["active"]
        data["history"].append({**active, "status": "interrupted", "evidence": "the day ended", "ended_at": self.ledger._now()})
        data["active"] = {**active, "id": f"objective-{len(data['history']) + 1}", "created_at": self.ledger._now(),
                          "started": {"day": calendar_day(state), "time": state.get("time")}}
        self.ledger._save()
        self.telemetry.record("objective_carried_over", {**self._ids(), "objective": active.get("goal"),
                                                         "since": active["started"]["day"]})

    @staticmethod
    def _prompt_versions(runs: Path, session_id: str) -> set[str]:
        versions: set[str] = set()
        for path in runs.glob("*/events.jsonl"):
            try:
                with path.open(encoding="utf-8", errors="replace") as events:
                    for line in events:
                        if '"agent_session_started"' not in line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("session_id") == session_id and event.get("prompt_version"):
                            versions.add(event["prompt_version"])
            except OSError:
                continue
        return versions

    # ---- the step ----

    def _actor_step(self) -> None:
        cycle_started = time.perf_counter()
        self.telemetry.step += 1
        state, frame = self._observe()
        self._check_activity_progress(state)
        if self.stop_reason:
            return
        self._ensure_session(state)
        if self.stop_reason or self._complete_verified_objective(state):
            return
        if self._play_dialogue(state):
            self.notes_pending.append("(A scene played out while you stood there; the words are in life.recentText.)")
            state, frame = self._observe()
        elif self._greet_in_passing(state):
            self.notes_pending.append("(You greeted someone in passing; what they said is in life.recentText.)")
            state, frame = self._observe()
        assert self.session is not None and self.diary is not None

        self.seq += 1
        text, summary = self._observation(state, frame)
        self.session.add_observation(self.seq, text, summary, frame.data_url or None)
        self.telemetry.record("agent_observation", {**self._ids(), "text": text, "summary": summary,
                                                    "frame_id": frame.frame_id}, include_recent=False)
        depth = self._depth()
        effort, cap, timeout = DEPTHS[depth]
        timeout = int(max(5, min(timeout, self._stall_deadline_remaining() + 5)))
        self.telemetry.record("depth_selected", {**self._ids(), "depth": depth, "timeout": timeout}, include_recent=False)
        self.telemetry.record("model_request", {"role": "actor", **self._ids(), "input_image": bool(frame.data_url),
                                               "context_chars": len(text), "tools_count": len(AGENT_TOOLS),
                                               "estimated_tokens": self.session.estimated_tokens()}, include_recent=False)
        started = time.perf_counter()
        tools = (FESTIVAL_TOOLS if walking_at_festival(state) else EVENT_TOOLS) if state.get("eventUp") else AGENT_TOOLS
        try:
            decision = self.client.complete_turn(AGENT_SYSTEM_PROMPT, self.session.messages(), tools,
                                                 reasoning_effort=effort, max_tokens=cap, timeout_seconds=timeout)
        except OpenRouterError as error:
            self._record_model_error("actor", error)
            raise
        model_ms = round((time.perf_counter() - started) * 1000)
        if PRIVATE_WORDS:
            cleaned = {key: (redact(value) if key in {"say", "diary", "chat"} else value) for key, value in decision.arguments.items()}
            decision = ToolDecision(decision.name, cleaned, decision.usage, decision.model, decision.content,
                                    decision.reasoning, decision.provider, decision.attempts, decision.call_id)
        self.decisions += 1
        self.first_call_of_day = False
        self.think_harder_next = bool(decision.arguments.get("think_harder"))
        call_id = decision.call_id or f"call-{self.run_id[:8]}-{self.seq}"
        self.decision_seq = self.seq
        self._record_agent_decision(decision, call_id, model_ms, depth)
        self.session.add_decision(self.seq, call_id, decision.name, decision.arguments, None)
        if self.guidance_decisions_left > 0:
            self.guidance_decisions_left -= 1

        if self._poll_operator(state):
            self.telemetry.record("operator_discarded_decision", {**self._ids(), "tool": decision.name})
            result = {"status": "interrupted", "reason": "an operator command arrived; look at the state again"}
            self._finish_step(decision, call_id, result, state, state, model_ms, 0, cycle_started)
            return
        self._check_activity_progress(state)
        if self.stop_reason:
            return

        current_state = state
        game_input = decision.name not in INFORMATIONAL and decision.name not in {"set_objective", "diary_write", "update_farm_plan"}
        if decision.name not in INFORMATIONAL:
            self.telemetry.record("action_started", {**self._ids(), "call_id": call_id, "tool": decision.name}, include_recent=False)
        diary_text = str(decision.arguments.get("diary") or "").strip()
        if diary_text:
            self._write_diary(state, diary_text, "bedtime" if decision.name == "go_home_and_sleep" else "note")
        if game_input:
            current_state, _ = self._observe()
        execute_started = time.perf_counter()
        plain = ToolDecision(decision.name, {k: v for k, v in decision.arguments.items() if k not in COMMON_FIELDS},
                             decision.usage, decision.model, call_id=call_id)
        fingerprint = self._action_fingerprint(plain, state)
        fishing = state.get("menu") == "BobberBar" or current_state.get("menu") == "BobberBar"
        if fishing:
            result = self._execute(plain, frame, current_state)
        elif game_input and self._decision_scene_changed(plain, state, current_state):
            result = {"status": "rejected", "reason": "scene_changed_while_thinking", "circuit_breaker": "scene_changed"}
        elif fingerprint == self.last_action_fingerprint:
            result = {"status": "rejected", "reason": "identical action already attempted with unchanged observable state; choose a different action",
                      "circuit_breaker": "identical_action"}
        elif game_input and self._same_target_blocked(plain, current_state):
            result = {"status": "rejected", "reason": "same_target_limit: this target failed three times; it stays closed until something relevant changes",
                      "circuit_breaker": "same_target"}
        else:
            self.last_action_fingerprint = fingerprint
            result = self._execute(plain, frame, current_state)
        if result.get("circuit_breaker"):
            self.telemetry.record("circuit_breaker", {**self._ids(), "tool": decision.name, "kind": result["circuit_breaker"]})
        if game_input and isinstance(result.get("state"), dict):
            result["effects"] = action_effects(current_state, result["state"])
        bridge_ms = round((time.perf_counter() - execute_started) * 1000)
        self._finish_step(decision, call_id, result, current_state, result.get("state") or current_state, model_ms, bridge_ms, cycle_started)

    def _finish_step(self, decision: ToolDecision, call_id: str, result: dict[str, Any], before: dict[str, Any],
                     after: dict[str, Any], model_ms: int, bridge_ms: int, cycle_started: float) -> None:
        assert self.session is not None
        self.last_result = {"tool": decision.name, "status": result.get("status")}
        for key in ("error", "warning", "reason", "effects"):
            if key in result:
                self.last_result[key] = result[key]
        self.recent_actions.append({"tool": decision.name, "status": result.get("status"),
                                    "reason": str(result.get("reason") or result.get("error") or "")[:120] or None})
        del self.recent_actions[:-5]
        self.telemetry.record("tool_result", {"tool": decision.name, **self._ids(), "call_id": call_id, "bridge_ms": bridge_ms,
                                             "result": result, "actor_cycle_ms": round((time.perf_counter() - cycle_started) * 1000)})
        self.session.add_result(self.seq, call_id, compact_result(result))
        self._print_step("agent", decision, result, after, model_ms, bridge_ms)
        finish = (decision.attempts[-1].get("finish_reason") if decision.attempts else None)
        if finish == "length":
            print(f"[{self.telemetry.step}] output truncated (finish_reason=length)", flush=True)
        if result.get("state") and self._checkpoint_if_saved(result["state"]):
            return
        self._track_action_retries(decision, result, before, after)
        intentional_wait = decision.name in {"idle", "wait"} and result.get("status") == "completed"
        self._check_activity_progress(after, decision=not intentional_wait)
        if not self.continuous and self.decisions >= self.max_decisions and not self.stop_reason:
            self.stop_reason = "max_decisions_reached"

    def _record_agent_decision(self, decision: ToolDecision, call_id: str, model_ms: int, depth: str) -> None:
        self.telemetry.record("actor_decision", {
            "tool": decision.name, "arguments": decision.arguments, "usage": decision.usage, "model": decision.model,
            "provider": decision.provider, "attempts": decision.attempts, "applied": True, "model_ms": model_ms,
            "content": (decision.content or "")[:2000] or None, "reasoning": (decision.reasoning or "")[:2000] or None,
            "role": "agent", "depth": depth, "call_id": call_id, **self._ids(),
        })
        if self.budget_usd is not None and float(self.telemetry.summary["usage"]["cost"]) >= self.budget_usd \
                and self.stop_reason != "budget_reached" and self.operator_mode not in {"saved", "held", "needs_attention"}:
            self.stop_reason = "budget_reached"
            self.telemetry.record("budget_reached", {"cumulative_cost": float(self.telemetry.summary["usage"]["cost"]),
                                                     "budget_usd": self.budget_usd})

    # ---- executing the new tools; everything else goes to the existing dispatch ----

    def _execute(self, decision: ToolDecision, frame: Frame, state: dict[str, Any]) -> dict[str, Any]:
        assert self.diary is not None
        name, arguments = decision.name, decision.arguments
        if name == "set_objective":
            if self.operator_mode == "finishing":
                return {"status": "rejected", "reason": "Operator requested saving. Return home and sleep first."}
            return self._set_objective(arguments, state)
        if name == "refill_watering_can":
            response = refill_watering_can(self.bridge, state, 30)
            self.game_actions += response.get("controls_executed", 0)
            return response
        if name == "diary_write":
            entry = self._write_diary(state, arguments["text"], arguments.get("kind") or "entry")
            return {"status": "recorded", "kind": entry["kind"]}
        if name == "diary_search":
            day = calendar_day(state)
            results = self.diary.search(arguments["query"], day, arguments.get("days") or 7) if isinstance(day, int) else []
            return {"status": "completed", "results": results}
        if name == "diary_read":
            text = self.diary.read(arguments["day"])
            return {"status": "completed", "day": arguments["day"], "text": text or "(nothing written that day)"}
        if name == "update_farm_plan":
            try:
                self._apply_farm_plan(arguments, state)
            except ValueError as error:
                return {"status": "rejected", "reason": str(error)}
            self.telemetry.record("farm_plan_set", {**self._ids()})
            return {"status": "recorded"}
        return self._execute_actor_tool(decision, frame, state)

    def _write_diary(self, state: dict[str, Any], text: str, kind: str) -> dict[str, Any]:
        assert self.diary is not None
        day = calendar_day(state)
        entry = self.diary.write(day if isinstance(day, int) else 0, state.get("time"), state.get("location"), text, kind)
        self.telemetry.record("diary_entry", {**self._ids(), **entry})
        return entry

    def _set_objective(self, arguments: dict[str, Any], state: dict[str, Any], by_harness: bool = False) -> dict[str, Any]:
        data = self.ledger.data
        active = data.get("active")
        outcome = arguments.get("previous_outcome") or "interrupted"
        note = arguments.get("previous_note")
        disputed = False
        demand = self.notebook.audience_demand()
        if active:
            check = active.get("check")
            if outcome == "completed" and check:
                evaluation = evaluate_state_condition(check, state)
                if evaluation is not None and not evaluation[0]:
                    disputed = True
                    self.notes_pending.append(f'You marked "{active.get("goal")}" completed, but its check was false at the time.')
                    self.telemetry.record("objective_claim_disputed", {**self._ids(), "objective": active.get("goal"), "check": check})
            open_quest = self._open_quest(str(active.get("source") or ""), state) if outcome == "completed" else None
            if open_quest:
                disputed = True
                self.notes_pending.append(f'You marked "{active.get("goal")}" completed, but the journal still lists '
                                          f'"{open_quest.get("title")}" as open: {"; ".join(open_quest.get("objectives") or [])}.')
                self.telemetry.record("objective_claim_disputed", {**self._ids(), "objective": active.get("goal"),
                                                                   "journal": open_quest.get("title")})
            data["history"].append({**active, "status": outcome, "evidence": note, "ended_at": self.ledger._now()})
            source = str(active.get("source") or "")
            if demand and source == f"chat:{demand['id']}":
                self.notebook.mark_audience("done" if outcome == "completed" else "failed", note)
                demand = None
        check = str(arguments.get("check") or "").strip() or None
        warning = None
        if check and evaluate_state_condition(check, {}) is None:
            warning = "check ignored: it must be a structured game-state comparison"
            check = None
        objective = {
            "id": f"objective-{len(data['history']) + 1}",
            "kind": arguments.get("kind") or "free",
            "goal": arguments["goal"],
            "why": arguments.get("why") or "",
            "done_when": arguments.get("done_when") or "",
            "source": arguments.get("source") or None,
            "check": check,
            "success_condition": check or "",
            "milestone": arguments.get("done_when") or "",
            "status": "active",
            "created_at": self.ledger._now(),
            "started": {"day": calendar_day(state), "time": state.get("time")},
            "saveId": state.get("saveId"),
            "by_harness": by_harness,
        }
        data["active"] = objective
        self.ledger._save()
        source = str(objective["source"] or "")
        if demand and source == f"chat:{demand['id']}" and demand.get("status") == "pending":
            self.notebook.mark_audience("bound", objective["goal"])
        self.guidance_decisions_left = 0
        self.telemetry.record("objective_set", {**self._ids(), "objective": objective, "by_harness": by_harness,
                                               "previous": {"id": (active or {}).get("id"), "goal": (active or {}).get("goal"),
                                                            "outcome": outcome, "note": note}})
        result = {"status": "completed", "objective": {"kind": objective["kind"], "goal": objective["goal"]}}
        if disputed:
            result["disputed"] = True
        if warning:
            result["warning"] = warning
        return result

    @staticmethod
    def _open_quest(source: str, state: dict[str, Any]) -> dict[str, Any] | None:
        if not source.startswith("quest:"):
            return None
        for quest in state.get("quests") or []:
            if isinstance(quest, dict) and str(quest.get("id")) == source[6:] and not quest.get("complete"):
                return quest
        return None

    def _quest_history(self, quest_id: Any) -> dict[str, Any] | None:
        """What became of this quest on earlier days, from his own objective records."""
        items = [item for item in self.ledger.data.get("history", []) if str(item.get("source") or "") == f"quest:{quest_id}"]
        if not items:
            return None
        days = sorted({(item.get("started") or {}).get("day") for item in items
                       if (item.get("started") or {}).get("day") is not None})
        last = items[-1]
        return {"daysWorked": days, "lastOutcome": last.get("status"), "lastNote": last.get("evidence")}

    # ---- what the agent sees ----

    def _depth(self) -> str:
        if self.think_harder_next:
            self.think_harder_next = False
            return "high"
        if self.first_call_of_day or self.stall_advice or (self.last_result or {}).get("status") in FAILED:
            return "medium"
        return "low"

    def _observation(self, state: dict[str, Any], frame: Frame) -> tuple[str, str]:
        assert self.diary is not None
        day = calendar_day(state)
        blocked_here = sorted(
            "+".join(buttons) for location, pixel_x, pixel_y, buttons in self.blocked_movements
            if (location, pixel_x, pixel_y) == (state.get("location"), state.get("pixelX"), state.get("pixelY")))
        game = {key: value for key, value in state.items()
                if key not in {"notices", "quests", "navigationRows", "farmLayout", "diagnostics", "warps"}}
        game["menuEntries"] = [{**entry, "index": index} for index, entry in enumerate(state.get("menuEntries", []))]
        game["harnessBlockedDirectionsHere"] = blocked_here
        game["harnessLastResult"] = self.last_result
        game["harnessStaminaLow"] = (state.get("stamina") or 0) < 30 if state.get("worldReady") else False
        game["harnessBedtimeAllowed"] = self.operator_mode == "finishing" or self._bedtime_allowed(state)
        if isinstance(state.get("curiosities"), list):
            tried = self.notebook.data["life"].get("curiosities", {})
            game["curiosities"] = [{**item, "tried": tried.get(f"{state.get('location')}:{item.get('id')}", {}).get("day") == day}
                                   for item in state["curiosities"]]
        active = self.ledger.data.get("active") or {}
        check = active.get("check")
        evaluation = evaluate_state_condition(check, state) if check else None
        objective = {
            "current": {key: active.get(key) for key in ("kind", "goal", "why", "done_when", "source", "check", "started")},
            "checkHolds": evaluation[0] if evaluation else None,
            "recent": [{"goal": item.get("goal"), "outcome": item.get("status"), "note": item.get("evidence")}
                       for item in self.ledger.data.get("history", [])[-3:]],
        }
        life = self.notebook.life_context(state)
        life["retrievedText"] = getattr(self, "latest_life_text", None)
        open_quests = [q for q in (life.get("quests") or []) if isinstance(q, dict) and not q.get("complete")]
        quests = {
            "open": [{"id": q.get("id"), "title": q.get("title"), "objectives": q.get("objectives"),
                      "daysLeft": q.get("daysLeft"), "reward": q.get("reward"),
                      "active": str(active.get("source") or "") == f"quest:{q.get('id')}",
                      "history": self._quest_history(q.get("id"))} for q in open_quests],
            "crops": {"planted": state.get("plantedCrops"), "watered": state.get("wateredCrops"),
                      "harvestable": state.get("harvestableCrops"), "seedsSown": state.get("seedsSown")},
        }
        names = [npc.get("name") for npc in state.get("npcsNearby", []) if npc.get("name")]
        words = [word for word in str(active.get("goal") or "").split() if len(word) > 3]
        notes = list(self.notes_pending)
        self.notes_pending = []
        if open_quests and self.first_call_of_day:
            titles = "; ".join(f"{q.get('title')} ({'; '.join(q.get('objectives') or [])})" for q in open_quests[:6])
            notes.append(f"Morning. Your journal: {titles}. All of it is passive until you make one entry your objective; "
                         "quests.open shows what each one has had from you on earlier days.")
        if self._bedtime_due(state):
            notes.append("It is past 11:30 PM. Go home and sleep; write the day's diary entry on the way.")
        elif (state.get("time") or 0) >= 2200 and state.get("location") != "FarmHouse":
            notes.append("The day is ending. Think about the diary entry before bed.")
        held = state.get("cursorItem")
        if held:
            name = held.get("name") if isinstance(held, dict) else held
            stack = held.get("stack") if isinstance(held, dict) else None
            notes.append(f"You are holding {stack or ''} {name} on the cursor, not in your bag. Click an empty bag slot to stow it, "
                         "or close_menu, which puts it away for you. Buying more while holding it stacks onto the cursor.")
        if (str(state.get("menu") or "").startswith("GameMenu") and state.get("menuReadyToClose") is False
                and not held and state.get("inventoryFreeSlots") == 0):
            notes.append("This menu will not close: something you just crafted or picked up is on the cursor (the state cannot "
                         "show it) and your bag has no free slot. Free one slot with inventory_trash or ship_item, then close_menu.")
        if isinstance(day, int) and (state.get("time") or 0) >= 1300 and not self.diary.entries(day):
            notes.append("You have not written anything in your diary today.")
        if self.stall_advice:
            notes.append(f"Nothing new has happened for {self.progress.decisions} decisions and "
                         f"{int(time.monotonic() - self.progress.last_progress)} seconds. Waiting counts as nothing happening. "
                         "What will you do differently? Answer with a different action, not a reworded one.")
        if not frame.data_url:
            notes.append("No screenshot is available right now; act from the structured state and avoid pointer tools.")
        if walking_at_festival(state):
            notes.append("A festival is on. `navigate_to` walks to any tile here, and `nearbyActions` lists the festival's "
                         "spots with their tiles (LuauSoup is the soup pot; IceFishing the hole): walk to the tile just "
                         "below one, tap W to face it, then press X. `npcsNearby` has tiles for everyone. The clock is paused.")
        elif state.get("eventUp") and state.get("eventCanMove"):
            notes.append("An event is on (a scene), so the walking helpers are off: move with hold and "
                         "control_sequence, talk with X, and use the screenshot for where people stand. The clock is paused.")
        audience, fresh_lines, fresh_request = self._audience()
        if fresh_lines:
            quoted = "; ".join(f'{user}: "{text}"' for user, text in fresh_lines)
            names = ", ".join(dict.fromkeys(user for user, _ in fresh_lines))
            notes.append(f"New in chat: {quoted}. Answer {names} by name with `chat` on this action, and if they ask for "
                         "something that fits the game and does no harm, start doing it now.")
        if fresh_request:
            notes.append(f'Chat agreed on a request: "{fresh_request["goal"]}". Take it now with '
                         f'set_objective(source="chat:{fresh_request["id"]}") if it fits and does no harm, and say so; '
                         "otherwise tell them why not.")
        if self.operator_mode == "finishing":
            notes.append("The operator asked to finish and save: go home and sleep now.")
        packet: dict[str, Any] = {
            "game": game,
            "objective": objective,
            "quests": quests,
            "life": life,
            "world": {**self.world.summary(state.get("location"), state.get("time")),
                      "doorsNotOnMap": {f"{here} -> {there}": how for (here, there), how in DOORS_NOT_ON_MAP.items()
                                        if here == state.get("location")} or None},
            "audience": audience,
            "operator": {"guidance": self.operator_guidance} if self.guidance_decisions_left > 0 and self.operator_guidance else None,
            "progress": {"decisionsSinceEvidence": self.progress.decisions,
                         "secondsSinceEvidence": int(time.monotonic() - self.progress.last_progress),
                         "recent": list(self.recent_actions)},
            "diary": {"excerpts": self.diary.excerpts(day, state.get("location"), names, words) if isinstance(day, int) else []},
            "frame": {"frame_id": frame.frame_id, "width": frame.width, "height": frame.height} if frame.data_url else {"unavailable": True},
            "notes": notes,
        }
        text = "Observation:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"), default=str)
        season = str(state.get("season") or "").capitalize()
        summary = (f"{season} {state.get('day')} · {game_time(state.get('time'))} · {state.get('location')} · "
                   f"result: {(self.last_result or {}).get('status') or 'none'}")
        return text, summary

    def _audience(self) -> tuple[dict[str, Any] | None, list[tuple[str, str]], dict[str, Any] | None]:
        """The one request chat agreed on, and the last few things chat said, in untrusted words; with the lines
        and the request he has not been shown before, so the observation can point them out once."""
        request = self.notebook.audience_demand()
        fresh_request = None
        if request and request.get("id") != self.request_seen:
            self.request_seen, fresh_request = request.get("id"), request
        chat: list[dict[str, Any]] = []
        fresh_lines: list[tuple[str, str]] = []
        try:
            recent = json.loads((self.repository_root / "chat" / "state.json").read_text(encoding="utf-8")).get("recent_messages") or []
        except (OSError, ValueError, AttributeError):
            recent = []
        now = time.time()
        for item in recent:
            if isinstance(item, dict) and now - float(item.get("at") or 0) <= 600:
                line = {"user": redact(str(item.get("user") or "viewer")), "text": redact(str(item.get("text") or ""))[:200],
                        "secondsAgo": max(0, int(now - float(item.get("at") or now)))}
                key = str(item.get("id") or f"{line['user']}:{line['text']}:{item.get('at')}")
                if key not in self.chat_seen:  # a line is shown once; shown again, he answers it again
                    self.chat_seen.add(key)
                    chat.append(line)
                    fresh_lines.append((line["user"], line["text"]))
        chat = chat[-6:]
        if not request and not chat:
            return None, fresh_lines, fresh_request
        return {"request": request, "chat": chat or None}, fresh_lines, fresh_request

    @staticmethod
    def _action_fingerprint(decision: ToolDecision, state: dict[str, Any]) -> str:
        base = AutoplayHarness._action_fingerprint(decision, state)
        held = state.get("cursorItem")
        return base + "|" + json.dumps([state.get("money"), held.get("name") if isinstance(held, dict) else held,
                                        held.get("stack") if isinstance(held, dict) else None])

    def _poll_operator(self, state: dict[str, Any]) -> bool:
        discarded = super()._poll_operator(state)
        if self.operator_guidance and self.operator_guidance != self.guidance_seen:
            if not self.operator_guidance.lower().startswith("stream operator"):
                self.operator_guidance = "Stream operator: " + redact(self.operator_guidance)
            self.guidance_seen = self.operator_guidance
            self.guidance_decisions_left = GUIDANCE_DECISIONS
        return discarded

