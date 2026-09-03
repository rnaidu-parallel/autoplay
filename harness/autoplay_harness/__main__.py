from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import imageio_ffmpeg

from . import overlay
from .bridge import NamedPipeBridge
from .capture import CaptureError, Frame, ScreenCapture
from .forever import run_forever
from .openrouter import OpenRouterClient
from .prompts import ACTOR_SYSTEM_PROMPT
from .recording import GameplayRecorder
from .report import render, summarize
from .runner import AutoplayHarness
from .supervisor import GameSupervisor
from .tools import ACTOR_TOOLS
from .wiki import StardewWiki


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Autoplay autonomous Stardew Valley harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the autonomous tool loop")
    run_parser.add_argument("--objective")
    run_parser.add_argument("--success-condition")
    run_parser.add_argument("--max-actions", type=int, default=10)
    run_parser.add_argument("--max-decisions", type=int, default=15)
    run_parser.add_argument("--director-interval", type=int, default=12)
    run_parser.add_argument("--budget-usd", type=float, default=None,
                            help="Stop when cumulative actor and director cost reaches this amount")
    run_parser.add_argument("--max-minutes", type=int, default=None,
                            help="Stop cleanly after this many wall-clock minutes")
    run_parser.add_argument("--forever", action="store_true", help="Restart stopped or failed runs indefinitely")
    run_parser.add_argument(
        "--model",
        choices=list(OpenRouterClient.PROVIDER_PREFERENCES),
        default=OpenRouterClient.DEFAULT_MODEL,
    )
    run_parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"], default="low")
    run_parser.add_argument("--isolated-state", action="store_true", help="Use a fresh per-run objective ledger for comparisons; does not reset or change the game save.")
    run_parser.add_argument("--actor-mode", choices=["state-first", "visual"], default="state-first",
                            help="Use compact structured skills with visual fallback, or always send a screenshot.")
    run_parser.add_argument("--no-launch-game", action="store_true")
    run_parser.add_argument("--save-frames", action="store_true")
    run_parser.add_argument("--keep-game-open", action="store_true")
    run_parser.add_argument("--record-video", action="store_true")
    run_parser.add_argument("--video-segment-minutes", type=int, default=5, choices=range(1, 61))
    run_parser.add_argument(
        "--video-retention-segments",
        type=int,
        default=6,
        choices=range(0, 289),
        metavar="COUNT",
        help="Keep a rolling number of raw video segments; 0 retains the full VOD (default: 6)",
    )
    run_parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run without action or decision caps and recover from transient failures",
    )

    bridge_parser = subparsers.add_parser(
        "bridge-test",
        help="Launch/connect to SMAPI and exercise the bounded control surface",
    )
    bridge_parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Temporarily switch to full-screen, assert primary-display dimensions, then restore",
    )
    capture_parser = subparsers.add_parser("capture-test", help="Save the foreground window client area")
    capture_parser.add_argument("--output", default="artifacts/harness-capture.jpg")
    record_parser = subparsers.add_parser(
        "record-test",
        help="Record the desktop briefly and verify the captured frames are live, not stale",
    )
    record_parser.add_argument("--seconds", type=int, default=5)
    wiki_parser = subparsers.add_parser("wiki-test", help="Query the Stardew Valley Wiki")
    wiki_parser.add_argument("query")
    cache_parser = subparsers.add_parser(
        "cache-test",
        help="Repeat one identical multimodal tool request without executing its decisions",
    )
    cache_parser.add_argument("--calls", type=int, default=5, choices=range(2, 11))
    cache_parser.add_argument(
        "--model",
        choices=list(OpenRouterClient.PROVIDER_PREFERENCES),
        default=OpenRouterClient.DEFAULT_MODEL,
    )
    cache_parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"], default="low")
    report_parser = subparsers.add_parser("report", help="Summarize a run's events.jsonl (default: most recent run)")
    report_parser.add_argument("run_id", nargs="?")
    overlay_parser = subparsers.add_parser("overlay", help="Serve the stream overlay for a run")
    overlay_parser.add_argument("--run", default="latest")
    overlay_parser.add_argument("--port", type=int, default=8765)
    overlay_parser.add_argument("--state-dir", default="harness/state")

    arguments = parser.parse_args()
    root = repository_root()

    if arguments.command == "overlay":
        return overlay.main(arguments)

    if arguments.command == "report":
        runs = root / "harness" / "runs"
        run_directory = (
            runs / arguments.run_id
            if arguments.run_id
            else max((path for path in runs.iterdir() if (path / "events.jsonl").exists()), key=lambda path: path.stat().st_mtime)
        )
        print(f"run {run_directory.name}")
        print(render(summarize(run_directory / "events.jsonl")))
        return 0

    if arguments.command == "run":
        if not arguments.forever and (arguments.objective is None or arguments.success_condition is None):
            parser.error("run requires --objective and --success-condition unless --forever is set")
        if arguments.forever and ((arguments.objective is None) != (arguments.success_condition is None)):
            parser.error("--objective and --success-condition must be provided together")
        if arguments.forever and arguments.isolated_state:
            parser.error("--forever cannot be combined with --isolated-state because state must persist across runs")

        def build_harness() -> AutoplayHarness:
            objective = arguments.objective
            success_condition = arguments.success_condition
            if arguments.forever and objective is None:
                ledger_path = root / "harness" / "state" / "objectives.json"
                active_objective = None
                if not arguments.isolated_state and ledger_path.exists():
                    try:
                        active_objective = json.loads(ledger_path.read_text(encoding="utf-8")).get("active")
                    except (OSError, TypeError, ValueError):
                        pass
                if arguments.isolated_state or active_objective is None:
                    objective = "Step outside to start the day."
                    success_condition = "location is Farm"
            harness = AutoplayHarness(
                repository_root=root,
                objective=objective,
                success_condition=success_condition,
                model=arguments.model,
                api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                max_actions=arguments.max_actions,
                max_decisions=arguments.max_decisions,
                director_interval=arguments.director_interval,
                launch_game=not arguments.no_launch_game,
                save_frames=arguments.save_frames,
                keep_game_open=arguments.keep_game_open and not arguments.forever,
                continuous=arguments.continuous,
                record_video=arguments.record_video,
                video_segment_minutes=arguments.video_segment_minutes,
                video_retention_segments=arguments.video_retention_segments,
                reasoning_effort=arguments.reasoning_effort,
                isolated_state=arguments.isolated_state,
                actor_mode=arguments.actor_mode,
                budget_usd=arguments.budget_usd,
                max_minutes=arguments.max_minutes,
                forever=arguments.forever,
            )
            if arguments.forever and harness.ledger.snapshot().get("active") is None:
                assert objective is not None and success_condition is not None
                harness.ledger.set_objective(
                    objective,
                    success_condition,
                    "Observe the current game state and choose the first concrete step.",
                )
            return harness

        if arguments.forever:
            stop_reason = run_forever(
                build_harness,
                stop_file=root / "harness" / "state" / "STOP",
                daily_budget_usd=arguments.budget_usd,
            )
            print(json.dumps({"stop_reason": stop_reason, "forever": True}))
            return 0

        harness = build_harness()
        print(json.dumps({"stop_reason": harness.run(), "run_id": harness.run_id}))
        return 0

    if arguments.command == "bridge-test":
        game_directory = Path(
            os.environ.get(
                "AUTOPLAY_GAME_DIRECTORY",
                r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley",
            )
        )
        supervisor = GameSupervisor(game_directory)
        bridge: NamedPipeBridge | None = None
        capture: ScreenCapture | None = None
        original_display_mode: str | None = None
        try:
            bridge = supervisor.connect_bridge()
            capture_directory = root / "artifacts"
            capture_directory.mkdir(parents=True, exist_ok=True)
            capture = ScreenCapture()
            observation = bridge.observe()
            initial_state = observation.get("state") or {}
            original_display_mode = (
                "borderless"
                if initial_state.get("windowedBorderless")
                else "fullscreen"
                if initial_state.get("graphicsFullScreen")
                else "windowed"
            )
            for attempt in range(21):
                observation, frame = _observe_and_capture(bridge, capture)
                state = observation.get("state") or {}
                title_splash = state.get("menu") == "TitleMenu" and (
                    frame.visual_variance < 5 or frame.visual_brightness > 215
                )
                if not title_splash:
                    break
                if attempt == 2:
                    bridge.request("press", buttons=["Escape"])
                    time.sleep(1)
                if attempt == 20:
                    diagnostic_path = capture_directory / "fullscreen-rejected-frame.jpg"
                    diagnostic_path.write_bytes(base64.b64decode(frame.data_url.partition(",")[2]))
                    raise RuntimeError(
                        "The title-screen splash did not finish rendering within 20 seconds: "
                        f"variance={frame.visual_variance:.2f}, brightness={frame.visual_brightness:.2f}, "
                        f"state={state}, frame={diagnostic_path}."
                    )
                time.sleep(1)
            if state.get("menu") == "TitleMenu":
                time.sleep(22)
                observation, frame = _observe_and_capture(bridge, capture)
            if arguments.fullscreen and original_display_mode != "borderless":
                display_response = bridge.request("set_display_mode", mode="borderless")
                if display_response.get("status") != "completed":
                    raise RuntimeError(f"Could not enable borderless full-screen mode: {display_response}")
                time.sleep(3)
                observation, frame = _observe_and_capture(bridge, capture)
            primary_width = ctypes.windll.user32.GetSystemMetrics(0)
            primary_height = ctypes.windll.user32.GetSystemMetrics(1)
            final_state = observation.get("state") or {}
            if arguments.fullscreen and (
                (frame.width, frame.height) != (primary_width, primary_height)
                or final_state.get("graphicsFullScreen")
                or not final_state.get("windowedBorderless")
            ):
                raise RuntimeError(
                    "Borderless full-screen test failed: "
                    f"captured {frame.width}x{frame.height}, expected {primary_width}x{primary_height}; "
                    f"state={final_state}."
                )
            capture_path = capture_directory / "harness-capture.jpg"
            capture_path.write_bytes(base64.b64decode(frame.data_url.partition(",")[2]))
            smoke_results = {
                "observe": observation,
                "move_cursor": bridge.request("move_cursor", x=10, y=10),
                "click": bridge.request("click", x=10, y=10, button="left"),
                "drag": bridge.request(
                    "drag",
                    startX=10,
                    startY=10,
                    endX=20,
                    endY=20,
                    button="left",
                    ticks=3,
                ),
                "scroll": bridge.request("scroll", direction="down", steps=1),
                "wait": bridge.request("wait", field="menu", value="TitleMenu", ticks=10),
                "press": bridge.request("press", buttons=["D"]),
                "frame": {
                    "path": str(capture_path),
                    "frame_id": frame.frame_id,
                    "width": frame.width,
                    "height": frame.height,
                    "visual_variance": frame.visual_variance,
                    "visual_brightness": frame.visual_brightness,
                    "fullscreen_test": arguments.fullscreen,
                    "primary_width": primary_width,
                    "primary_height": primary_height,
                },
            }
            print(json.dumps(smoke_results, indent=2))
        finally:
            if arguments.fullscreen and original_display_mode not in {None, "borderless"} and bridge is not None:
                try:
                    bridge.request("set_display_mode", mode=original_display_mode)
                    time.sleep(2)
                except Exception:
                    pass
            if bridge is not None:
                bridge.close()
            if capture is not None:
                capture.release()
            supervisor.stop_started_game()
        return 0

    if arguments.command == "capture-test":
        output = root / arguments.output
        capture = ScreenCapture(save_directory=output.parent, require_game_window=False)
        frame = capture.capture()
        assert frame.saved_path is not None
        frame.saved_path.replace(output)
        print(json.dumps({"path": str(output), "width": frame.width, "height": frame.height}))
        return 0

    if arguments.command == "record-test":
        directory = Path(tempfile.mkdtemp(prefix="autoplay-record-test-"))
        recorder = GameplayRecorder(directory)
        recorder.start()
        time.sleep(arguments.seconds)
        recorder.stop()
        video = directory / "gameplay-000.mp4"
        frames = _decoded_frame_count(video)
        expected_minimum = int(0.8 * 30 * arguments.seconds)
        early = _frame_digest(video, 0.5)
        late = _frame_digest(video, max(0.5, arguments.seconds - 0.5))
        frames_differ = early != late
        print(
            json.dumps(
                {
                    "encoder": recorder.encoder,
                    "frames": frames,
                    "expected_min": expected_minimum,
                    "frames_differ": frames_differ,
                    "path": str(video),
                }
            )
        )
        if frames < expected_minimum:
            print("Recording is stale: too few frames were decoded.", file=sys.stderr)
            return 1
        if not frames_differ:
            print("Warning: the sampled frames are identical; the desktop may have been static.", file=sys.stderr)
        return 0

    if arguments.command == "wiki-test":
        print(json.dumps(StardewWiki().search(arguments.query), indent=2, ensure_ascii=False))
        return 0

    if arguments.command == "cache-test":
        game_directory = Path(
            os.environ.get(
                "AUTOPLAY_GAME_DIRECTORY",
                r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley",
            )
        )
        supervisor = GameSupervisor(game_directory)
        bridge = None
        capture = None
        try:
            bridge = supervisor.connect_bridge()
            capture = ScreenCapture()
            state, frame = _observe_and_capture(bridge, capture)
            context = json.dumps(
                {"test": "Identical cache probe; choose one tool but it will not be executed.", "game_state": state},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            client = OpenRouterClient(
                api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                model=arguments.model,
                run_id=f"cache-{uuid.uuid4()}",
                reasoning_effort=arguments.reasoning_effort,
            )
            results = []
            for index in range(arguments.calls):
                decision = client.choose_tool(
                    ACTOR_SYSTEM_PROMPT,
                    context,
                    frame.data_url,
                    ACTOR_TOOLS,
                    cache_namespace="actor",
                )
                details = decision.usage.get("prompt_tokens_details") or {}
                results.append(
                    {
                        "call": index + 1,
                        "prompt_tokens": int(decision.usage.get("prompt_tokens") or 0),
                        "cached_tokens": int(details.get("cached_tokens") or 0),
                        "cache_write_tokens": int(details.get("cache_write_tokens") or 0),
                        "tool": decision.name,
                        "provider": decision.provider,
                        "cost": decision.usage.get("cost"),
                    }
                )
            prompt_tokens = sum(item["prompt_tokens"] for item in results)
            cached_tokens = sum(item["cached_tokens"] for item in results)
            print(
                json.dumps(
                    {
                        "calls": results,
                        "cache_hit_rate": cached_tokens / prompt_tokens if prompt_tokens else 0,
                    },
                    indent=2,
                )
            )
        finally:
            if bridge is not None:
                bridge.close()
            if capture is not None:
                capture.release()
            supervisor.stop_started_game()
        return 0

    return 2


def _decoded_frame_count(video: Path) -> int:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-v", "error", "-stats", "-i", str(video), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    counts = re.findall(r"frame=\s*(\d+)", result.stderr)
    return int(counts[-1]) if counts else 0


def _frame_digest(video: Path, timestamp: float) -> str:
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _observe_and_capture(
    bridge: NamedPipeBridge,
    capture: ScreenCapture,
) -> tuple[dict[str, object], Frame]:
    last_error: CaptureError | None = None
    for _ in range(6):
        observation = bridge.observe()
        try:
            return observation, capture.capture()
        except CaptureError as error:
            last_error = error
            time.sleep(0.25)
    assert last_error is not None
    raise last_error


if __name__ == "__main__":
    raise SystemExit(main())
