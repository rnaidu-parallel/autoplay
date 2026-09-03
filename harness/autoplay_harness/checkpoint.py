"""Pair a verified Stardew nightly save with the matching harness state."""

import hashlib
import json
import shutil
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .control import write_json


STATE_FILES = ("objectives.json", "notebook.json", "world.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Checkpoints:
    def __init__(self, state_directory: Path, saves_directory: Path) -> None:
        self.state_directory = state_directory
        self.saves_directory = saves_directory
        self.directory = state_directory / "checkpoints"

    def _save_directory(self, save_id: str) -> Path:
        if not save_id or Path(save_id).name != save_id or save_id in {".", ".."} or any(c in save_id for c in "/\\:"):
            raise ValueError("The bridge did not provide a valid save identity.")
        return self.saves_directory / save_id

    def create(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        if not (state.get("worldReady") and state.get("playerFree") and not state.get("nightActive")
                and state.get("location") == "FarmHouse" and state.get("menu") == "none"):
            raise ValueError("The nightly save has not finished settling.")
        save_id = state.get("saveId") or ""
        source = self._save_directory(save_id)
        try:
            game = ET.parse(source / save_id).getroot()
        except ET.ParseError as error:
            raise ValueError("The game save could not be parsed.") from error
        date = {"year": int(game.findtext("year", "0")), "season": game.findtext("currentSeason"),
                "day": int(game.findtext("dayOfMonth", "0"))}
        if date != {key: state.get(key) for key in date}:
            raise ValueError("The save on disk does not match the observed game date.")
        checkpoint_id = uuid.uuid4().hex
        target = self.directory / checkpoint_id
        target.mkdir(parents=True)
        files = {}
        for group, directory, names in (("game", source, (save_id, "SaveGameInfo")),
                                       ("state", self.state_directory, STATE_FILES)):
            for name in names:
                destination = target / group / name
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(directory / name, destination)
                files[f"{group}/{name}"] = digest(destination)
        manifest = {"id": checkpoint_id, "run_id": run_id, "save_id": save_id, "date": date,
                    "created_at": datetime.now(timezone.utc).isoformat(), "files": files}
        write_json(target / "manifest.json", manifest)
        write_json(self.directory / "latest.json", {"id": checkpoint_id})
        return manifest

    def restore(self) -> dict[str, Any]:
        pointer = json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))
        checkpoint_id = pointer["id"]
        if len(checkpoint_id) != 32 or any(c not in "0123456789abcdef" for c in checkpoint_id):
            raise ValueError("Invalid checkpoint identity.")
        source = self.directory / checkpoint_id
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        save_directory = self._save_directory(manifest["save_id"])
        expected = [f"state/{name}" for name in STATE_FILES] + [f"game/{name}" for name in (manifest["save_id"], "SaveGameInfo")]
        if set(manifest["files"]) != set(expected):
            raise ValueError("Incomplete checkpoint manifest.")
        for relative in expected:
            if digest(source / relative) != manifest["files"][relative]:
                raise ValueError("Checkpoint files changed or are incomplete.")
            if relative.startswith("game/") and digest(save_directory / Path(relative).name) != manifest["files"][relative]:
                raise ValueError("The game save changed after this checkpoint. Resume normally to preserve that progress.")
        backup = self.state_directory / "before-resume" / uuid.uuid4().hex
        backup.mkdir(parents=True)
        for name in STATE_FILES:
            current = self.state_directory / name
            if current.exists():
                shutil.copy2(current, backup / name)
            write_json(current, json.loads((source / "state" / name).read_text(encoding="utf-8")))
        return manifest
