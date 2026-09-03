"""Incremental, bounded retrieval over the local audit archive."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class History:
    def __init__(self, runs_directory: Path, state_directory: Path) -> None:
        self.runs_directory = runs_directory
        self.state_directory = state_directory
        self.path = state_directory / "history.sqlite3"

    def search(self, query: str, kind: str = "all", limit: int = 5) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120 or kind not in {"all", "actions", "objectives"} or not 1 <= limit <= 5:
            raise ValueError("Use a short query, actions/objectives/all, and a limit from 1 to 5.")
        self.state_directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS offsets (run TEXT PRIMARY KEY, offset INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS entries (source TEXT PRIMARY KEY, at TEXT, kind TEXT, text TEXT)")
            for path in sorted(self.runs_directory.glob("*/events.jsonl")):
                run = path.parent.name
                row = db.execute("SELECT offset FROM offsets WHERE run=?", (run,)).fetchone()
                offset = row[0] if row else 0
                if path.stat().st_size < offset:
                    db.execute("DELETE FROM entries WHERE source LIKE ?", (run + ":%",))
                    offset = 0
                with path.open("rb") as stream:
                    stream.seek(offset)
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line.endswith(b"\n"):
                            break
                        offset = stream.tell()
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(event, dict) or event.get("tool") == "search_history":
                            continue
                        event_type = event.get("type", "")
                        if event_type not in {"actor_decision", "director_decision", "tool_result", "operator_command"} and not event_type.startswith("objective_"):
                            continue
                        entry_kind = "objectives" if event_type.startswith("objective_") or event.get("tool") in {"set_objective", "change_objective", "continue_objective", "block_objective"} else "actions"
                        summary = {key: event[key] for key in ("type", "step", "tool", "arguments", "reason", "evidence", "objective_id", "objective", "command") if key in event}
                        if "result" in event:
                            result = event["result"]
                            summary["result"] = {key: result[key] for key in ("status", "reason", "error", "tiles_planted", "tiles_watered", "items_gained") if key in result}
                            state = result.get("state") or {}
                            summary["state"] = {key: state[key] for key in ("location", "day", "season", "year", "time", "dialogueText") if key in state}
                        db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?)", (f"{run}:{start}", event.get("at", ""), entry_kind,
                                   json.dumps(summary, ensure_ascii=False)[:2400]))
                db.execute("INSERT OR REPLACE INTO offsets VALUES (?,?)", (run, offset))
            ledger_path = self.state_directory / "objectives.json"
            if ledger_path.exists():
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                db.execute("DELETE FROM entries WHERE source LIKE 'ledger:%'")
                for objective in ledger.get("history", []) + ([ledger["active"]] if ledger.get("active") else []):
                    db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?)", ("ledger:" + objective["id"],
                               objective.get("ended_at") or objective.get("created_at", ""), "objectives",
                               json.dumps(objective, ensure_ascii=False)[:2400]))
            terms = query.casefold().split()[:8]
            clauses = ["instr(lower(text), ?) > 0" for _ in terms]
            parameters: list[Any] = list(terms)
            if kind != "all":
                clauses.append("kind=?")
                parameters.append(kind)
            rows = db.execute("SELECT source,at,kind,text FROM entries WHERE " + " AND ".join(clauses) + " ORDER BY at DESC,source DESC LIMIT ?", [*parameters, limit])
            return [{"source": source, "at": at, "kind": category, "excerpt": text[:600]} for source, at, category, text in rows]
