from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .bridge import BridgeError, NamedPipeBridge


class GameSupervisor:
    def __init__(self, game_directory: Path, launch_if_needed: bool = True) -> None:
        self.game_directory = game_directory
        self.launch_if_needed = launch_if_needed
        self.process: subprocess.Popen[bytes] | None = None

    def connect_bridge(self) -> NamedPipeBridge:
        quick_bridge = NamedPipeBridge(connect_timeout_seconds=2)
        try:
            quick_bridge.connect()
            return quick_bridge
        except BridgeError:
            quick_bridge.close()

        if not self.launch_if_needed:
            raise BridgeError("SMAPI is not running and automatic launch is disabled.")
        executable = self.game_directory / "StardewModdingAPI.exe"
        if not executable.exists():
            raise BridgeError(f"SMAPI executable not found at {executable}.")
        self._prepare_windowed_startup()
        self.process = subprocess.Popen(
            [str(executable)],
            cwd=self.game_directory,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        bridge = NamedPipeBridge(connect_timeout_seconds=90)
        bridge.connect()
        time.sleep(2)
        return bridge

    def stop_started_game(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def wait_for_started_game(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.wait()

    @staticmethod
    def _prepare_windowed_startup() -> None:
        preferences = Path(os.environ["APPDATA"]) / "StardewValley" / "startup_preferences"
        if not preferences.exists():
            return
        original = preferences.read_bytes()
        updated = re.sub(rb"<windowMode>\d+</windowMode>", b"<windowMode>1</windowMode>", original)
        updated = updated.replace(b"<fullscreen>true</fullscreen>", b"<fullscreen>false</fullscreen>")
        updated = updated.replace(
            b"<windowedBorderlessFullscreen>true</windowedBorderlessFullscreen>",
            b"<windowedBorderlessFullscreen>false</windowedBorderlessFullscreen>",
        )
        if updated != original:
            preferences.write_bytes(updated)
