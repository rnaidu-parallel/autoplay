from __future__ import annotations

import subprocess
import time
from pathlib import Path

import imageio_ffmpeg


class RecordingError(RuntimeError):
    pass


class GameplayRecorder:
    # Desktop Duplication output to record. ScreenCapture._get_camera calls dxcam.create()
    # without an output index, which selects the primary output, so recording and
    # screenshots must both use output 0.
    OUTPUT_INDEX = 0
    PRIMARY_ENCODER = "h264_nvenc"
    FALLBACK_ENCODER = "libx264"

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
        self.encoder: str | None = None

    def _command(self, encoder: str = PRIMARY_ENCODER) -> list[str]:
        pattern = self.directory / "gameplay-%03d.mp4"
        segment_start_number = len(list(self.directory.glob("gameplay-*.mp4")))
        # Frames stay on the GPU only if a CUDA device can be derived from the desktop's
        # D3D11 device; on this machine that derivation fails, so the frames are downloaded
        # and handed to the encoder as software BGRA.
        filters = f"ddagrab=output_idx={self.OUTPUT_INDEX}:framerate=30:draw_mouse=0,hwdownload,format=bgra"
        if encoder == self.PRIMARY_ENCODER:
            encoding = [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-rc",
                "vbr",
                "-cq",
                "23",
                "-b:v",
                "0",
                # NVENC only honours -force_key_frames as an IDR when forced-idr is set.
                "-forced-idr",
                "1",
                "-pix_fmt",
                "yuv420p",
            ]
        else:
            encoding = [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
            ]
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-init_hw_device",
            "d3d11va",
            "-filter_complex",
            filters,
            *encoding,
            "-force_key_frames",
            f"expr:gte(t,n_forced*{self.segment_minutes * 60})",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_minutes * 60),
            "-segment_start_number",
            str(segment_start_number),
            "-reset_timestamps",
            "1",
        ]
        if self.retention_segments:
            command.extend(["-segment_wrap", str(self.retention_segments)])
        command.append(str(pattern))
        return command

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for encoder in (self.PRIMARY_ENCODER, self.FALLBACK_ENCODER):
            self.process = subprocess.Popen(
                self._command(encoder),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            time.sleep(1)
            if self.process.poll() is None:
                self.encoder = encoder
                return
        raise RecordingError("FFmpeg exited before gameplay recording started.")

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

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
