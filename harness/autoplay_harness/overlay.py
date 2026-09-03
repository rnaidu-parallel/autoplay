from __future__ import annotations

import json
import re
import sys
import threading
import time
from argparse import Namespace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


from .control import OperatorControl

INDEX_PATH = Path(__file__).with_name("overlay") / "index.html"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _game_time(value: Any) -> str | None:
    if not isinstance(value, int):
        return None
    hour = (value // 100) % 24
    minute = value % 100
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _humanize(value: Any) -> str:
    text = str(value or "").replace("_", " ")

    def replace_time(match: re.Match[str]) -> str:
        return f"until {_game_time(int(match.group(1)))}"

    return re.sub(r"until (\d{3,4})\b", replace_time, text)


def action_summary(tool: str, arguments: dict[str, Any] | None = None) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    if tool == "water_crops":
        count = len(arguments.get("tiles") or []) or int(arguments.get("count") or 0)
        return f"Watering {count} crop{'s' if count != 1 else ''}"
    if tool == "travel_to":
        return f"Walking to {arguments.get('destination') or arguments.get('location') or 'the next stop'}"
    if tool == "go_to_location":
        return f"Going to {arguments.get('location') or 'the next area'}"
    if tool == "go_home_and_sleep":
        return "Heading home to sleep"
    if tool == "plan_day":
        return f"Planning the day: {arguments.get('theme') or 'today\'s priorities'}"
    if tool == "set_objective":
        return f"New goal: {arguments.get('goal') or 'choose the next objective'}"
    if tool == "inspect_scene":
        return "Taking a closer look"
    if tool == "remember_interaction":
        return f"Remembering {arguments.get('subject') or 'an encounter'}"
    return _humanize(tool)


def argument_gist(arguments: dict[str, Any] | None = None) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    tiles = arguments.get("tiles") or arguments.get("targets")
    if isinstance(tiles, list) and tiles:
        points = []
        for tile in tiles[:3]:
            if isinstance(tile, dict) and isinstance(tile.get("x"), int) and isinstance(tile.get("y"), int):
                points.append(f"({tile['x']},{tile['y']})")
            elif isinstance(tile, (list, tuple)) and len(tile) >= 2:
                points.append(f"({tile[0]},{tile[1]})")
        if points:
            extra = len(tiles) - len(points)
            return " ".join(points + ([f"+{extra}"] if extra else []))
    if isinstance(arguments.get("tile_x"), int) and isinstance(arguments.get("tile_y"), int):
        return f"({arguments['tile_x']},{arguments['tile_y']})"
    for key in ("location", "destination", "preference"):
        if arguments.get(key):
            return str(arguments[key])
    if isinstance(arguments.get("buttons"), list):
        return "+".join(str(button) for button in arguments["buttons"])
    for key in ("count", "seed_slot"):
        if isinstance(arguments.get(key), int):
            return f"×{arguments[key]}"
    return ""


def _say(event: dict[str, Any]) -> str | None:
    arguments = event.get("arguments")
    if isinstance(arguments, dict) and isinstance(arguments.get("say"), str) and arguments["say"].strip():
        return arguments["say"].strip()
    content = event.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def _director_summary(tool: str, arguments: dict[str, Any] | None) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    detail = arguments.get("goal") or arguments.get("milestone") or arguments.get("theme")
    return f"Director: {detail or action_summary(tool, arguments)}"


def action_outcome(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if status == "completed":
        return "completed"
    if status in {"blocked", "rejected", "error", "timeout"}:
        reason = result.get("reason") or result.get("error")
        return f"{status}: {_humanize(reason)}" if reason else str(status)
    return _humanize(status) if status else None


class OverlayState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.game: dict[str, Any] = {}
        self.notebook: dict[str, Any] = {}
        self.objectives: dict[str, Any] = {}
        self.world_data: dict[str, Any] = {}
        self.world_summary: dict[str, Any] = {}
        self.actions: list[dict[str, Any]] = []
        self.actor_actions: list[dict[str, Any]] = []
        self.sayings: list[str] = []
        self.pending_requests: dict[str, datetime] = {}
        self.pending_action: tuple[Any, str] | None = None
        self.actor_decisions = 0
        self.director_reviews = 0
        self.model_calls = 0
        self.stall_at_decision: int | None = None
        self.stalled_decisions = 0
        self.prompt_tokens = 0
        self.cached_tokens = 0
        self.cost = 0.0
        self.model: str | None = None
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.stop_reason: str | None = None

    def apply(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        occurred = _stamp(event.get("at"))
        if self.started_at is None and occurred is not None:
            self.started_at = occurred

        state = event.get("state")
        if not isinstance(state, dict):
            result = event.get("result")
            state = result.get("state") if isinstance(result, dict) else None
        if isinstance(state, dict):
            self.game.update(state)

        if event_type == "session_started":
            self.model = event.get("model") or self.model
        elif event_type == "model_request":
            role = event.get("role") if event.get("role") in {"actor", "director"} else "actor"
            if occurred is not None:
                self.pending_requests[role] = occurred
        elif event_type in {"actor_decision", "director_decision"}:
            role = event_type.removesuffix("_decision")
            self.pending_requests.pop(role, None)
            if role == "actor":
                self.actor_decisions += 1
            else:
                self.director_reviews += 1
            self.model = event.get("model") or self.model
            self._add_usage(event)
            if event.get("applied") is not False and isinstance(event.get("tool"), str):
                action = {
                    "at": event.get("at"),
                    "gameTime": _game_time(self.game.get("time")),
                    "role": role,
                    "tool": event["tool"],
                    "toolName": event["tool"],
                    "say": _say(event),
                    "summary": (_director_summary(event["tool"], event.get("arguments"))
                                if role == "director" else action_summary(event["tool"], event.get("arguments"))),
                    "gist": argument_gist(event.get("arguments")),
                    "outcome": None,
                    "_step": event.get("step"),
                }
                self.actions.insert(0, action)
                del self.actions[8:]
                if role == "actor":
                    self.actor_actions.insert(0, action)
                    del self.actor_actions[2:]
                    if action["say"]:
                        self.sayings.insert(0, action["say"])
                        del self.sayings[2:]
                    self.pending_action = (event.get("step"), event["tool"])
        elif event_type == "tool_result":
            result = event.get("result")
            if event.get("source") == "operator_finish":
                self.actions.insert(0, {"at": event.get("at"), "role": "operator", "tool": event["tool"],
                                       "gameTime": _game_time(self.game.get("time")),
                                       "toolName": "Return home and save", "say": None, "summary": "Finish & Save",
                                       "gist": "", "outcome": action_outcome(result), "_step": event.get("step")})
                del self.actions[8:]
                self.actor_actions.insert(0, self.actions[0])
                del self.actor_actions[2:]
            if event.get("tool") == "world_map" and isinstance(result, dict) and isinstance(result.get("summary"), dict):
                self.world_summary = result["summary"]
            for action in self.actions + self.actor_actions:
                if action["_step"] == event.get("step") and action["tool"] == event.get("tool"):
                    action["outcome"] = action_outcome(event.get("result"))
                    break
            if self.pending_action == (event.get("step"), event.get("tool")):
                self.pending_action = None
        elif event_type == "operator_discarded_decision":
            for action in self.actions + self.actor_actions:
                if action["_step"] == event.get("step") and action["tool"] == event.get("tool"):
                    action["outcome"] = "Discarded: operator direction received"
            self.pending_action = None
        elif event_type == "model_error":
            self.pending_requests.pop(str(event.get("role") or "actor"), None)
            self._add_usage(event)
        elif event_type == "stall_detected":
            self.stall_at_decision = self.actor_decisions
            self.stalled_decisions = int(event.get("count") or 0)
        elif event_type in {"session_stopped", "fatal_error"}:
            self.stop_reason = str(event.get("reason") or ("fatal error" if event_type == "fatal_error" else "stopped"))
            self.stopped_at = occurred
            self.pending_requests.clear()
            self.pending_action = None

    def _add_usage(self, event: dict[str, Any]) -> None:
        self.model_calls += 1
        usage = event.get("usage") or {}
        if not isinstance(usage, dict):
            return
        prompt_details = usage.get("prompt_tokens_details") or {}
        if not isinstance(prompt_details, dict):
            prompt_details = {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.cached_tokens += int(prompt_details.get("cached_tokens") or 0)
        self.cost += float(usage.get("cost") or 0)

    def update_files(
        self,
        objectives: dict[str, Any] | None = None,
        notebook: dict[str, Any] | None = None,
        world: dict[str, Any] | None = None,
    ) -> None:
        if objectives is not None:
            self.objectives = objectives
        if notebook is not None:
            self.notebook = notebook
        if world is not None:
            self.world_data = world

    def snapshot(self, restarting: bool = False, now: datetime | None = None) -> dict[str, Any]:
        now = now or _now()
        status = "running"
        thinking_role: str | None = None
        thinking_since: int | None = None
        if self.pending_requests:
            thinking_role, started = max(self.pending_requests.items(), key=lambda item: item[1])
            thinking_since = max(0, int((now - started).total_seconds() * 1000))
            status = "thinking"
        elif self.pending_action is not None:
            status = "acting"
        if self.stall_at_decision is not None and self.actor_decisions - self.stall_at_decision < 8:
            status = "stalled"
        if self.stop_reason is not None:
            status = "stopped"
        if restarting:
            status = "restarting"

        agenda, reflection = self._agenda()
        active = self.objectives.get("active")
        objective = None
        if isinstance(active, dict):
            objective = {
                "goal": active.get("goal"),
                "milestone": active.get("milestone"),
                "successCondition": active.get("success_condition"),
                "agendaId": active.get("agenda_id"),
            }
        visited = self.world_data.get("visited") or {}
        total = self.world_data.get("totalCount", self.world_summary.get("totalCount"))
        if total is None and isinstance(self.world_data.get("nodes"), (dict, list)):
            total = len(self.world_data["nodes"])
        unvisited = (
            self.world_data.get("unvisitedNearby")
            or self.world_data.get("unvisited")
            or self.world_summary.get("unvisited")
            or []
        )
        if not isinstance(unvisited, list):
            unvisited = []
        if isinstance(visited, dict):
            unvisited = [name for name in unvisited if isinstance(name, str) and name not in visited]
        lessons = self.notebook.get("learned") or []
        if not isinstance(lessons, list):
            lessons = []
        route_home = (
            self.world_data.get("routeHome")
            or self.world_summary.get("routeHome")
            or []
        )
        if not isinstance(route_home, list):
            route_home = []
        end = self.stopped_at or now
        uptime = max(0, int((end - self.started_at).total_seconds())) if self.started_at else 0
        prompt = self.prompt_tokens
        actions = [{key: item[key] for key in ("at", "gameTime", "role", "tool", "toolName", "say", "summary", "gist", "outcome")} for item in self.actions]
        actor_actions = [{key: item[key] for key in ("at", "gameTime", "role", "tool", "toolName", "say", "summary", "gist", "outcome")} for item in self.actor_actions]
        sayings = self.sayings
        return {
            "status": status,
            "updatedAt": now.isoformat(),
            "game": {
                "day": self.game.get("day"),
                "season": self.game.get("season"),
                "year": self.game.get("year"),
                "time": _game_time(self.game.get("time")),
                "weather": self.game.get("weather"),
                "location": self.game.get("location"),
                "stamina": self.game.get("stamina"),
                "maxStamina": self.game.get("maxStamina") or 270,
                "health": self.game.get("health"),
                "money": self.game.get("money"),
            },
            "agenda": {"theme": agenda.get("theme"), "items": agenda.get("items", []), "reflectionYesterday": reflection},
            "objective": objective,
            "thinking": {"role": thinking_role, "sinceMs": thinking_since},
            "speech": {"say": sayings[0] if sayings else None, "previous": sayings[1] if len(sayings) > 1 else None},
            "actions": actions,
            "actorActions": actor_actions,
            "world": {
                "here": self.game.get("location") or self.world_data.get("last_location"),
                "visitedCount": len(visited) if isinstance(visited, dict) else 0,
                "totalCount": total,
                "unvisitedNearby": unvisited[:4],
                "routeHome": route_home,
            },
            "lessons": list(reversed(lessons))[:3],
            "stats": {
                "decisions": self.actor_decisions,
                "directorReviews": self.director_reviews,
                "modelCalls": self.model_calls,
                "inputTokens": self.prompt_tokens,
                "cost": round(self.cost, 4),
                "cacheHitRate": round(self.cached_tokens / prompt, 4) if prompt else 0,
                "uptimeSeconds": uptime,
                "stalledDecisions": self.stalled_decisions,
            },
            "session": {"runId": self.run_id, "model": self.model, "stopReason": self.stop_reason},
        }

    def _agenda(self) -> tuple[dict[str, Any], str | None]:
        days = self.notebook.get("days") or {}
        if not isinstance(days, dict) or not days:
            return {"theme": None, "items": []}, None
        day_key = str(self.game.get("day"))
        if day_key not in days:
            day_key = next(reversed(days))
        entry = days.get(day_key) or {}
        if not isinstance(entry, dict):
            return {"theme": None, "items": []}, None
        keys = list(days)
        index = keys.index(day_key)
        previous = days.get(keys[index - 1]) if index else None
        yesterday = previous.get("reflection") if isinstance(previous, dict) else None
        items = [
            {key: item.get(key) for key in ("id", "goal", "slot", "status")}
            for item in entry.get("agenda", [])
            if isinstance(item, dict)
        ]
        return {"theme": entry.get("theme"), "items": items}, yesterday


def build_state(
    events: list[dict[str, Any]],
    run_id: str = "test",
    objectives: dict[str, Any] | None = None,
    notebook: dict[str, Any] | None = None,
    world: dict[str, Any] | None = None,
    restarting: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    overlay = OverlayState(run_id)
    for event in events:
        try:
            overlay.apply(event)
        except (TypeError, ValueError):
            continue
    overlay.update_files(objectives, notebook, world)
    return overlay.snapshot(restarting, now)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows refuses the rename while a reader holds the target open; retry briefly, never crash the feed.
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                print(f"overlay: state write skipped ({path.name} busy)", file=sys.stderr)
                return
            time.sleep(0.05)


def make_handler(
    state_path: Path | Callable[[], Path], index_path: Path = INDEX_PATH
) -> type[BaseHTTPRequestHandler]:
    current_path = state_path if callable(state_path) else lambda: state_path

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] == "/":
                self._serve(index_path, "text/html; charset=utf-8")
            elif self.path.split("?", 1)[0] == "/state.json":
                self._serve(current_path(), "application/json; charset=utf-8")
            elif self.path.split("?", 1)[0] == "/control.json":
                self._json(200, OperatorControl(current_path().parent.parent).status())
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/commands":
                self.send_error(404)
                return
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
                self._json(403, {"error": "Commands require the local operator view."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON command of at most 4096 bytes.")
                command = json.loads(self.rfile.read(length))
                if not isinstance(command, dict):
                    raise ValueError("Expected a command object.")
                control = OperatorControl(current_path().parent.parent)
                result = control.submit(command.get("run_id"), command.get("kind"), command.get("message", ""))
                self._json(202, result)
            except (ValueError, TypeError) as error:
                self._json(400, {"error": str(error)})

        def _json(self, status: int, value: dict[str, Any]) -> None:
            content = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _serve(self, path: Path, content_type: str) -> None:
            try:
                content = path.read_bytes()
            except OSError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def create_server(
    state_path: Path | Callable[[], Path], port: int = 0, index_path: Path = INDEX_PATH
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(state_path, index_path))


class EventTail:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.pending = b""
        self.missing_reported = False

    def read(self) -> tuple[list[dict[str, Any]], bool]:
        try:
            size = self.path.stat().st_size
            self.missing_reported = False
        except OSError as error:
            if not self.missing_reported:
                print(f"overlay: waiting for {self.path}: {error}", file=sys.stderr)
                self.missing_reported = True
            return [], False
        reset = size < self.offset
        if reset:
            self.offset = 0
            self.pending = b""
        try:
            with self.path.open("rb") as source:
                source.seek(self.offset)
                chunk = source.read()
        except OSError as error:
            print(f"overlay: cannot read {self.path.name}: {error}", file=sys.stderr)
            return [], reset
        self.offset += len(chunk)
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        events = []
        for raw in lines:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
                if isinstance(event, dict):
                    events.append(event)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                print(f"overlay: skipped malformed event: {error}", file=sys.stderr)
        return events, reset


def _latest_run(runs: Path) -> Path | None:
    try:
        candidates = [path for path in runs.iterdir() if path.is_dir() and (path / "events.jsonl").exists()]
    except OSError:
        return None
    return max(candidates, key=lambda path: (path / "events.jsonl").stat().st_mtime_ns, default=None)


def _read_changed(path: Path, signatures: dict[Path, tuple[int, int] | None]) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    if signatures.get(path, object()) == signature:
        return None
    signatures[path] = signature
    if signature is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError) as error:
        print(f"overlay: cannot load {path.name}: {error}", file=sys.stderr)
        return None


def main(args: Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    runs = root / "harness" / "runs"
    state_directory = Path(args.state_dir)
    if not state_directory.is_absolute():
        state_directory = root / state_directory
    latest = args.run == "latest"
    run_directory = _latest_run(runs) if latest else runs / args.run
    while run_directory is None:
        print("overlay: waiting for a run", file=sys.stderr)
        time.sleep(0.25)
        run_directory = _latest_run(runs)

    current = {"path": run_directory / "overlay" / "state.json"}
    server = create_server(lambda: current["path"], args.port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"overlay: http://127.0.0.1:{server.server_port}", file=sys.stderr)

    state = OverlayState(run_directory.name)
    tail = EventTail(run_directory / "events.jsonl")
    signatures: dict[Path, tuple[int, int] | None] = {}
    try:
        while True:
            if latest:
                selected = _latest_run(runs)
                if selected is not None and selected != run_directory:
                    run_directory = selected
                    current["path"] = run_directory / "overlay" / "state.json"
                    state = OverlayState(run_directory.name)
                    tail = EventTail(run_directory / "events.jsonl")
                    signatures.clear()
            events, reset = tail.read()
            if reset:
                state = OverlayState(run_directory.name)
                signatures.clear()
            for event in events:
                try:
                    state.apply(event)
                except (TypeError, ValueError) as error:
                    print(f"overlay: skipped malformed event: {error}", file=sys.stderr)
            sources = {}
            for name in ("objectives", "notebook", "world"):
                changed = _read_changed(state_directory / f"{name}.json", signatures)
                if changed is not None:
                    sources[name] = changed
            state.update_files(**sources)
            restarting = (run_directory / "overlay" / "restarting").exists()
            atomic_write_json(current["path"], state.snapshot(restarting))
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
