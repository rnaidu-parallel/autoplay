from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from ctypes import wintypes
from typing import Any


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
        wintypes.LPVOID,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
        wintypes.LPVOID,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


class BridgeError(RuntimeError):
    pass


class NamedPipeBridge:
    PIPE_PATH = r"\\.\pipe\autoplay-game-bridge"

    def __init__(self, connect_timeout_seconds: float = 60) -> None:
        if os.name != "nt":
            raise BridgeError("The Autoplay bridge currently supports Windows only.")
        self.connect_timeout_seconds = connect_timeout_seconds
        self._handle: int | None = None

    def connect(self) -> None:
        if self._handle is not None:
            return

        deadline = time.monotonic() + self.connect_timeout_seconds
        while time.monotonic() < deadline:
            if _kernel32.WaitNamedPipeW(self.PIPE_PATH, 1000):
                handle = _kernel32.CreateFileW(
                    self.PIPE_PATH,
                    0xC0000000,
                    0,
                    None,
                    3,
                    0,
                    None,
                )
                if handle != wintypes.HANDLE(-1).value:
                    self._handle = handle
                    return
            time.sleep(0.25)
        raise BridgeError(f"Timed out waiting for {self.PIPE_PATH}.")

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def request(self, request_type: str, **arguments: Any) -> dict[str, Any]:
        self.connect()
        request = {"id": str(uuid.uuid4()), "type": request_type, **arguments}
        payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        self._write(payload)
        response = json.loads(self._read_line().decode("utf-8"))
        if response.get("id") != request["id"]:
            raise BridgeError("The bridge returned a response for a different request.")
        return response

    def observe(self) -> dict[str, Any]:
        return self.request("observe")

    def _write(self, payload: bytes) -> None:
        assert self._handle is not None
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(payload)
        success = _kernel32.WriteFile(
            self._handle,
            buffer,
            len(payload),
            ctypes.byref(written),
            None,
        )
        if not success or written.value != len(payload):
            self.close()
            raise BridgeError("Failed to write to the game bridge.")

    def _read_line(self) -> bytes:
        assert self._handle is not None
        payload = bytearray()
        while True:
            buffer = ctypes.create_string_buffer(4096)
            read = wintypes.DWORD()
            success = _kernel32.ReadFile(
                self._handle,
                buffer,
                len(buffer),
                ctypes.byref(read),
                None,
            )
            if not success:
                self.close()
                raise BridgeError("Failed to read from the game bridge.")
            payload.extend(buffer.raw[: read.value])
            newline = payload.find(b"\n")
            if newline >= 0:
                return bytes(payload[:newline])

    def __enter__(self) -> "NamedPipeBridge":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
