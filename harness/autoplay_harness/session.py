from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 4
IMAGE_TOKENS = 1000

UNKNOWN_RESULT = {
    "status": "unknown",
    "reason": "the harness restarted before the result was recorded; the state below is current",
}

REBUILD_TYPES = frozenset(
    {"agent_observation", "agent_note", "agent_decision", "actor_decision", "action_started", "tool_result"}
)


class DaySession:
    """The transcript of one game day, as a list of turns that renders to chat messages."""

    def __init__(self, session_id: str, max_tokens: int = 120_000, full_result_bundles: int = 4) -> None:
        self.session_id = session_id
        self.max_tokens = max_tokens
        self.full_result_bundles = full_result_bundles
        self.turns: list[dict[str, Any]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.unresolved_call_ids: list[str] = []

    def add_observation(self, seq: int, text: str, summary: str, image_data_url: str | None = None) -> None:
        self.turns.append(
            {"kind": "observation", "seq": seq, "text": text, "summary": summary, "image": image_data_url}
        )

    def add_note(self, seq: int, text: str) -> None:
        self.turns.append({"kind": "note", "seq": seq, "text": text})

    def add_decision(
        self, seq: int, call_id: str, name: str, arguments: dict[str, Any], content: str | None = None
    ) -> None:
        self.turns.append(
            {"kind": "decision", "seq": seq, "call_id": call_id, "name": name, "arguments": arguments, "content": content}
        )

    def add_result(self, seq: int, call_id: str, result: dict[str, Any]) -> None:
        self.results[call_id] = result

    def bundle_count(self) -> int:
        return sum(1 for turn in self.turns if turn["kind"] == "decision")

    def estimated_tokens(self) -> int:
        """Size of the pruned transcript before any budget dropping."""
        return _tokens(self._render(self._bundles()))

    def messages(self) -> list[dict[str, Any]]:
        bundles = self._bundles()
        rendered = self._render(bundles)
        if _tokens(rendered) > self.max_tokens:
            rendered = self._render(self._fit(bundles))
        return rendered

    @classmethod
    def rebuild(cls, runs_directory: Path, session_id: str, **kwargs: Any) -> "DaySession":
        session = cls(session_id, **kwargs)
        records: list[tuple[str, dict[str, Any]]] = []
        for events_path in sorted(Path(runs_directory).glob("*/events.jsonl")):
            for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("session_id") != session_id:
                    continue
                if event.get("type") in REBUILD_TYPES:
                    records.append((events_path.parent.name, event))
        records.sort(key=lambda record: (record[1].get("at") or "", record[1].get("seq") or 0))

        seen: set[tuple[Any, ...]] = set()
        for run, event in records:
            key = (run, event["type"], event.get("seq"), event.get("call_id"))
            if key in seen:
                continue
            seen.add(key)
            kind = event["type"]
            if kind == "agent_observation":
                session.add_observation(event.get("seq"), event.get("text", ""), event.get("summary", ""))
            elif kind == "agent_note":
                session.add_note(event.get("seq"), event.get("text", ""))
            elif kind in {"agent_decision", "actor_decision"} and event.get("call_id"):
                session.add_decision(
                    event.get("seq"),
                    event.get("call_id"),
                    event.get("tool", ""),
                    event.get("arguments") or {},
                    event.get("content"),
                )
            elif kind == "tool_result" and event.get("call_id"):
                session.add_result(event.get("seq"), event["call_id"], compact_result(event.get("result") or {}))
        session.unresolved_call_ids = [
            turn["call_id"]
            for turn in session.turns
            if turn["kind"] == "decision" and turn["call_id"] not in session.results
        ]
        return session

    def _bundles(self) -> list[dict[str, Any]]:
        """One bundle per decision, plus a leading bundle for user turns before the first decision."""
        bundles: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for turn in self.turns:
            if turn["kind"] == "decision":
                current = {"decision": turn, "users": []}
                bundles.append(current)
            elif current is None:
                if not bundles:
                    bundles.append({"decision": None, "users": []})
                bundles[0]["users"].append(turn)
            else:
                current["users"].append(turn)
        return bundles

    def _fit(self, bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        protected = self._newest_observation_bundle(bundles)
        order = [index for index, bundle in enumerate(bundles) if bundle["decision"] is not None]
        if bundles and bundles[0]["decision"] is None:
            order.append(0)
        dropped: set[int] = set()
        for index in order:
            if index == protected:
                continue
            kept = [bundle for position, bundle in enumerate(bundles) if position not in dropped]
            if _tokens(self._render(kept)) <= self.max_tokens:
                return kept
            dropped.add(index)
        return [bundle for position, bundle in enumerate(bundles) if position not in dropped]

    @staticmethod
    def _newest_observation_bundle(bundles: list[dict[str, Any]]) -> int | None:
        for index in reversed(range(len(bundles))):
            if any(turn["kind"] == "observation" for turn in bundles[index]["users"]):
                return index
        return None

    def _render(self, bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        newest = self._newest_observation()
        full_result_ids = self._full_result_ids()
        messages: list[dict[str, Any]] = []
        for bundle in bundles:
            decision = bundle["decision"]
            if decision is not None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": decision["content"] or None,
                        "tool_calls": [
                            {
                                "id": decision["call_id"],
                                "type": "function",
                                "function": {
                                    "name": decision["name"],
                                    "arguments": json.dumps(decision["arguments"]),
                                },
                            }
                        ],
                    }
                )
                result = self.results.get(decision["call_id"], UNKNOWN_RESULT)
                if decision["call_id"] not in full_result_ids:
                    result = _reduce(result)
                messages.append(
                    {"role": "tool", "tool_call_id": decision["call_id"], "content": json.dumps(result)}
                )
            for turn in bundle["users"]:
                messages.append({"role": "user", "content": _user_parts(turn, turn is newest)})
        return _merge_user_messages(messages)

    def _newest_observation(self) -> dict[str, Any] | None:
        for turn in reversed(self.turns):
            if turn["kind"] == "observation":
                return turn
        return None

    def _full_result_ids(self) -> set[str]:
        if self.full_result_bundles <= 0:
            return set()
        call_ids = [turn["call_id"] for turn in self.turns if turn["kind"] == "decision"]
        return set(call_ids[-self.full_result_bundles :])


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """What a result looks like inside the transcript: no state snapshot (the next observation has it), bounded size."""
    compact = {key: value for key, value in result.items() if key != "state"}
    if len(json.dumps(compact, ensure_ascii=False, default=str)) > 4000:
        compact = {key: compact[key] for key in ("status", "reason", "error", "warning") if key in compact}
        compact["note"] = "details omitted for length"
    return compact


def _user_parts(turn: dict[str, Any], newest: bool) -> list[dict[str, Any]]:
    if turn["kind"] != "observation":
        return [{"type": "text", "text": turn["text"]}]
    if not newest:
        return [{"type": "text", "text": turn["summary"]}]
    parts = [{"type": "text", "text": turn["text"]}]
    if turn["image"]:
        parts.append({"type": "image_url", "image_url": {"url": turn["image"]}})
    return parts


def _reduce(result: dict[str, Any]) -> dict[str, Any]:
    reduced = {"status": result.get("status")}
    reason = result.get("reason") or result.get("error")
    if reason is not None:
        reduced["reason"] = reason
    return reduced


def _merge_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "user" and merged and merged[-1]["role"] == "user":
            merged[-1]["content"] = merged[-1]["content"] + message["content"]
        else:
            merged.append(message)
    return merged


def _tokens(messages: list[dict[str, Any]]) -> int:
    characters = 0
    images = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            characters += len(content)
        elif isinstance(content, list):
            for part in content:
                if part["type"] == "text":
                    characters += len(part["text"])
                else:
                    images += 1
        for call in message.get("tool_calls", []):
            characters += len(call["function"]["name"]) + len(call["function"]["arguments"])
    return characters // CHARS_PER_TOKEN + images * IMAGE_TOKENS
