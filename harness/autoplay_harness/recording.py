from __future__ import annotations

import subprocess
import time
from pathlib import Path

import imageio_ffmpeg


class RecordingError(RuntimeError):
    pass


class GameplayRecorder:
    def __init__(
        self,
        directory: Path,
        segment_minutes: int = 5,
        retention_segments: int = 6,
    ) -> None:
        self.directory = directory
        self.segment_minutes = segment_minutes
        self.retention_segments = retention_segments
        self.process: subprocess.Popen[bytes] | None = None

    def _command(self) -> list[str]:
        pattern = self.directory / "gameplay-%03d.mp4"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-draw_mouse",
            "0",
            "-framerate",
            "30",
            "-thread_queue_size",
            "1024",
            "-i",
            "desktop",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{self.segment_minutes * 60})",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_minutes * 60),
            "-reset_timestamps",
            "1",
        ]
        if self.retention_segments:
            command.extend(["-segment_wrap", str(self.retention_segments)])
        command.append(str(pattern))
        return command

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        time.sleep(1)
        if self.process.poll() is not None:
            raise RecordingError("FFmpeg exited before gameplay recording started.")

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(b"q\n")
            self.process.stdin.flush()
            self.process.wait(timeout=15)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
