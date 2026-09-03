from __future__ import annotations

import base64
import ctypes
import io
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import dxcam
from PIL import Image, ImageStat


class CaptureError(RuntimeError):
    pass


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


@dataclass(frozen=True)
class Frame:
    frame_id: str
    width: int
    height: int
    data_url: str
    saved_path: Path | None
    visual_variance: float = 0.0
    visual_brightness: float = 0.0
    image_width: int = 0
    image_height: int = 0


class ScreenCapture:
    def __init__(
        self,
        jpeg_quality: int = 75,
        save_directory: Path | None = None,
        require_game_window: bool = True,
        max_image_width: int = 1280,
    ) -> None:
        self.jpeg_quality = jpeg_quality
        self.max_image_width = max_image_width
        self.save_directory = save_directory
        self.require_game_window = require_game_window
        self._raised_handle: int | None = None
        self._camera = None
        if save_directory is not None:
            save_directory.mkdir(parents=True, exist_ok=True)
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Rect)]
        user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Point)]
        user32.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetFocus.argtypes = [ctypes.c_void_p]
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()

    def capture(self) -> Frame:
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        title = self._window_title(handle)
        if self.require_game_window and "Stardew Valley" not in title:
            handle = self._find_game_window()
            if not handle:
                raise CaptureError(f"Could not find a Stardew Valley window; foreground was {title!r}")
            self._activate_window(handle)
            self._raised_handle = handle

        client = _Rect()
        if not ctypes.windll.user32.GetClientRect(handle, ctypes.byref(client)):
            raise CaptureError("Could not read the foreground window client bounds.")
        top_left = _Point(client.left, client.top)
        bottom_right = _Point(client.right, client.bottom)
        ctypes.windll.user32.ClientToScreen(handle, ctypes.byref(top_left))
        ctypes.windll.user32.ClientToScreen(handle, ctypes.byref(bottom_right))
        if bottom_right.x <= top_left.x or bottom_right.y <= top_left.y:
            raise CaptureError("The foreground window has no visible client area.")

        camera = self._get_camera()
        try:
            pixels = camera.grab(region=(top_left.x, top_left.y, bottom_right.x, bottom_right.y))
        except Exception as error:
            self._release_camera()
            raise CaptureError(f"DXGI capture failed: {error}") from error
        if pixels is None:
            raise CaptureError("DXGI did not return a frame for the game window.")
        image = Image.fromarray(pixels)
        center = image.crop(
            (
                round(image.width * 0.15),
                round(image.height * 0.15),
                round(image.width * 0.85),
                round(image.height * 0.75),
            )
        ).resize((64, 36))
        center_stats = ImageStat.Stat(center)
        visual_variance = sum(center_stats.stddev) / 3
        visual_brightness = sum(center_stats.mean) / 3
        frame_id = str(uuid.uuid4())
        encoded = io.BytesIO()
        sent = image.convert("RGB")
        if sent.width > self.max_image_width:
            scale = self.max_image_width / sent.width
            sent = sent.resize((self.max_image_width, round(sent.height * scale)), Image.Resampling.LANCZOS)
        sent.save(encoded, format="JPEG", quality=self.jpeg_quality)
        jpeg = encoded.getvalue()
        saved_path = None
        if self.save_directory is not None:
            saved_path = self.save_directory / f"{frame_id}.jpg"
            saved_path.write_bytes(jpeg)
        return Frame(
            frame_id=frame_id,
            width=image.width,
            height=image.height,
            data_url="data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
            saved_path=saved_path,
            visual_variance=visual_variance,
            visual_brightness=visual_brightness,
            image_width=sent.width,
            image_height=sent.height,
        )

    def release(self) -> None:
        self._release_camera()
        if self._raised_handle is not None:
            self._release_topmost(self._raised_handle)
            self._raised_handle = None

    def _get_camera(self):
        if self._camera is not None:
            return self._camera
        errors = []
        for backend in ("dxgi", "winrt"):
            for _ in range(3):
                try:
                    self._camera = dxcam.create(
                        backend=backend,
                        output_color="RGB",
                        processor_backend="numpy",
                    )
                    return self._camera
                except Exception as error:
                    errors.append(f"{backend}: {error}")
                    time.sleep(0.5)
        raise CaptureError("Could not initialize screen capture: " + "; ".join(errors))

    def _release_camera(self) -> None:
        if self._camera is not None:
            self._camera.release()
            self._camera = None

    @staticmethod
    def _window_title(handle: int) -> str:
        user32 = ctypes.windll.user32
        title_length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(handle, title_buffer, len(title_buffer))
        return title_buffer.value

    @classmethod
    def _find_game_window(cls) -> int:
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def inspect(handle: int, _: int) -> bool:
            if ctypes.windll.user32.IsWindowVisible(handle) and "Stardew Valley" in cls._window_title(handle):
                matches.append(handle)
                return False
            return True

        callback = callback_type(inspect)
        ctypes.windll.user32.EnumWindows(callback, 0)
        return matches[0] if matches else 0

    @staticmethod
    def _activate_window(handle: int) -> None:
        user32 = ctypes.windll.user32
        no_move_or_size = 0x0001 | 0x0002
        show_window = 0x0040
        user32.ShowWindowAsync(handle, 9)
        user32.SetWindowPos(handle, ctypes.c_void_p(-1), 0, 0, 0, 0, no_move_or_size | show_window)
        virtual_key_alt = 0x12
        key_up = 0x0002
        user32.keybd_event(virtual_key_alt, 0, 0, 0)
        user32.keybd_event(virtual_key_alt, 0, key_up, 0)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        user32.SetFocus(handle)
        time.sleep(0.5)
        if user32.GetForegroundWindow() != handle:
            # Raising the game above another app can leave XNA inactive. The Windows
            # application activation API recovered this during the recorded rehearsal.
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            try:
                subprocess.run(
                    [str(shell), "-NoProfile", "-NonInteractive", "-Command",
                     f"if (-not (New-Object -ComObject WScript.Shell).AppActivate({process_id.value})) {{ exit 1 }}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                raise CaptureError("Could not activate the game window.") from error

    @staticmethod
    def _release_topmost(handle: int) -> None:
        no_move_or_size = 0x0001 | 0x0002
        show_window = 0x0040
        ctypes.windll.user32.SetWindowPos(
            handle,
            ctypes.c_void_p(-2),
            0,
            0,
            0,
            0,
            no_move_or_size | show_window,
        )
