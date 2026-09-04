"""Local operator inbox. Submission and consumption are separate acknowledgements."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class OperatorControl:
    def __init__(self, run_directory: Path) -> None:
        self.directory = run_directory / "control"
        self.run_id = run_directory.name
        self.status_path = self.directory / "status.json"

    def status(self) -> dict[str, Any]:
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"run_id": self.run_id, "mode": "unavailable", "closed": True}

    def update(self, **fields: Any) -> None:
        write_json(self.status_path, {**self.status(), **fields, "run_id": self.run_id,
                                     "updated_at": datetime.now(timezone.utc).isoformat()})

    def submit(self, run_id: str, kind: str, message: str = "", support: int = 0) -> dict[str, Any]:
        if run_id != self.run_id or self.status().get("closed", True):
            raise ValueError("This run is no longer accepting commands. Refresh the operator view.")
        if kind not in {"finish_save", "steer", "hold", "audience"}:
            raise ValueError("Unknown operator command.")
        if not isinstance(message, str) or len(message) > 600:
            raise ValueError("Guidance must be at most 600 characters.")
        if kind in {"steer", "audience"} and not message.strip():
            raise ValueError("Enter guidance for the farmer.")
        # Chat speaks through "audience" only: it never stops, holds or preempts a run.
        if not isinstance(support, int) or isinstance(support, bool) or not 0 <= support <= 100000:
            raise ValueError("Support must be a viewer count between 0 and 100000.")
        command = {"id": uuid.uuid4().hex, "run_id": run_id, "kind": kind, "message": message.strip(),
                   "support": support, "at": datetime.now(timezone.utc).isoformat()}
        write_json(self.directory / "inbox" / f"{time.time_ns():020d}-{command['id']}.json", command)
        return {**command, "status": "queued"}

    def consume(self) -> list[dict[str, Any]]:
        commands = []
        for path in sorted((self.directory / "inbox").glob("*.json")):
            command = json.loads(path.read_text(encoding="utf-8"))
            if command.get("run_id") == self.run_id:
                commands.append(command)
            processed = self.directory / "processed" / path.name
            processed.parent.mkdir(parents=True, exist_ok=True)
            path.replace(processed)
        return commands
