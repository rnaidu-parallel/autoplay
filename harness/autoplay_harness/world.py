from __future__ import annotations

import json
import re
import time as clock
from collections import deque
from pathlib import Path
from typing import Any

from .calendar import calendar_day
from .control import replace_with_retry

from .bridge import BridgeError, NamedPipeBridge


class WorldMap:
    def __init__(self, state_directory: Path) -> None:
        self.path = state_directory / "world.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.visited: dict[str, int] = data.get("visited", {})
        self.last_location: str | None = data.get("last_location")
        self.entered_from: str | None = data.get("entered_from")
        self.unreachable_edges: list[dict[str, Any]] = data.get("unreachable_edges", [])
        self.blocked_paths: list[dict[str, Any]] = data.get("blocked_paths", [])
        # Observed reachability of each exit, keyed "<location>|<entered from>": a location can be
        # several pockets (the Farm's south entrance is cut off from the house by debris).
        self.pockets: dict[str, dict[str, Any]] = data.get("pockets", {})
        self.current_day: int | None = data.get("day")
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.version: int | None = None
        self._loaded = False
        changed = False
        for name in ("FarmHouse", "Farm"):
            if name not in self.visited:
                self.visited[name] = 1
                changed = True
        if changed or not self.path.exists():
            self._save()

    def load(self, bridge: NamedPipeBridge, version: int | None = None) -> None:
        if self._loaded and (version is None or version == self.version):
            return
        response = bridge.request("world_map")
        if response.get("status") != "completed":
            raise BridgeError(f"World map request failed: {response}")
        self.nodes = {node["name"]: node for node in response.get("nodes", [])}
        self.edges = [
            edge for edge in response.get("edges", [])
            if edge.get("from") in self.nodes and edge.get("to") in self.nodes
        ]
        returned_state = response.get("state") or {}
        self.version = returned_state.get("worldMapVersion", version)
        self._loaded = True

    def observe(self, state: dict[str, Any]) -> None:
        changed = False
        day = calendar_day(state)
        if isinstance(day, int) and day != self.current_day:
            self.current_day = day
            self.blocked_paths = []
            changed = True
        location = state.get("location")
        if not location:
            if changed:
                self._save()
            return
        if location != self.last_location:
            self.entered_from = self.last_location
            changed = True
        self.last_location = location
        if location not in self.visited:
            self.visited[location] = calendar_day(state) or 1
            changed = True
        exits = state.get("exits")
        if isinstance(exits, list) and exits and not state.get("eventUp"):
            key = f"{location}|{self.entered_from or '*'}"
            observed: dict[str, Any] = {"day": day, "reachable": {}}
            for exit in exits:
                target = exit.get("target")
                if target:
                    observed["reachable"][target] = observed["reachable"].get(target, False) or bool(exit.get("reachable"))
            if self.pockets.get(key) != observed:
                self.pockets[key] = observed
                changed = True
        if changed:
            self._save()

    def _pocket_blocked(self, name: str, entered_from: str | None) -> set[tuple[str, str]]:
        """Exits observed unreachable from this entry point recently. Debris gets cleared, so old observations lapse."""
        pocket = self.pockets.get(f"{name}|{entered_from or '*'}")
        if not pocket or not isinstance(self.current_day, int) or not isinstance(pocket.get("day"), int):
            return set()
        if self.current_day - pocket["day"] > 3:
            return set()
        return {(name, target) for target, reachable in pocket.get("reachable", {}).items() if not reachable}

    def record_unreachable(self, from_name: str, to_name: str, day: int, reason: str) -> None:
        existing = next(
            (edge for edge in self.unreachable_edges
             if edge.get("from") == from_name and edge.get("to") == to_name),
            None,
        )
        value = {"from": from_name, "to": to_name, "day": day, "reason": reason}
        if existing == value:
            return
        if existing is None:
            self.unreachable_edges.append(value)
        else:
            existing.update(value)
        self._save()

    def record_blocked_path(self, from_name: str, to_name: str, day: int) -> None:
        if day != self.current_day:
            self.current_day = day
            self.blocked_paths = []
        value = {"from": from_name, "to": to_name, "day": day}
        if from_name == self.last_location and self.entered_from is not None:
            value["entered_from"] = self.entered_from
        if value in self.blocked_paths:
            return
        self.blocked_paths.append(value)
        self._save()

    def clear_blocked_path(self, from_name: str, to_name: str) -> None:
        remaining = [
            path for path in self.blocked_paths
            if (path.get("from") != from_name or path.get("to") != to_name
                or path.get("entered_from") not in {None, self.entered_from})
        ]
        if len(remaining) == len(self.blocked_paths):
            return
        self.blocked_paths = remaining
        self._save()

    def edge_hours(self, from_name: str, to_name: str) -> tuple[int, int] | None:
        edge = next(
            (candidate for candidate in self.edges
             if candidate.get("from") == from_name and candidate.get("to") == to_name
             and candidate.get("kind") == "action_warp"
             and candidate.get("openTime") is not None and candidate.get("closeTime") is not None),
            None,
        )
        return (edge["openTime"], edge["closeTime"]) if edge is not None else None

    def route(
        self, from_name: str, to_name: str, time: int | None = None
    ) -> list[str] | dict[str, Any]:
        path = self._find_path(from_name, to_name, undirected=False)
        if path is None:
            path = self._find_path(from_name, to_name, undirected=True)
        if path is None:
            return []
        hops = [hop for hop, _edge in path]
        if time is not None:
            for _hop, edge in path:
                open_time = edge.get("openTime")
                close_time = edge.get("closeTime")
                if open_time is not None and close_time is not None and not self._is_open(time, open_time, close_time):
                    return {
                        "route": hops,
                        "blocked_by": {"edge": edge, "openTime": open_time, "closeTime": close_time},
                    }
        return hops

    def summary(self, current: str | None, time: int | None) -> dict[str, Any]:
        edges = self._available_edges()
        exits = sorted({edge["to"] for edge in edges if edge["from"] == current})
        distances = self._distances(current)
        unvisited = sorted(
            (name for name in self.nodes
             if name not in self.visited and name in distances
             and re.fullmatch(r"Cellar\d*", name) is None),
            key=lambda name: (not self.nodes[name].get("isOutdoors", False), distances[name], name),
        )[:6]
        unreachable = list(dict.fromkeys(
            edge["to"] for edge in self.unreachable_edges if edge.get("to") in self.nodes
        ))[-12:]
        blocked_pairs = self._blocked_pairs(self.entered_from, current)
        destinations = {edge["to"] for edge in self.edges if edge["from"] == current}
        blocked_now = sorted(
            destination for destination in destinations
            if all(
                (edge["from"], edge["to"]) in blocked_pairs
                for edge in self.edges
                if edge["from"] == current and edge["to"] == destination
            )
        )
        closed_now = []
        if time is not None:
            closed_now = [
                {"name": name, "opensAt": open_time}
                for name, open_time in sorted({
                    (edge["to"], edge["openTime"])
                    for edge in self.edges
                    if edge.get("from") == current and edge.get("kind") == "action_warp"
                    and edge.get("openTime") is not None and edge.get("closeTime") is not None
                    and not self._is_open(time, edge["openTime"], edge["closeTime"])
                })
            ]
        home = self.route(current or "", "FarmHouse", time)
        route_home = home.get("route", []) if isinstance(home, dict) else home
        return {
            "here": current,
            "exits": exits[:8],
            "unvisited": unvisited,
            "unreachable": unreachable,
            "blockedNow": blocked_now[:4],
            "closedNow": closed_now[:4],
            "visitedCount": len(set(self.nodes) & set(self.visited)),
            "totalCount": len(self.nodes),
            "routeHome": route_home[:6],
        }

    def _find_path(
        self, from_name: str, to_name: str, undirected: bool
    ) -> list[tuple[str, dict[str, Any]]] | None:
        if from_name not in self.nodes or to_name not in self.nodes:
            return None
        if from_name == to_name:
            return []
        adjacent: dict[str, list[tuple[str, dict[str, Any]]]] = {name: [] for name in self.nodes}
        unavailable = self._unreachable_pairs()
        for edge in self.edges:
            if (edge["from"], edge["to"]) in unavailable:
                continue
            adjacent[edge["from"]].append((edge["to"], edge))
            if undirected and (edge["to"], edge["from"]) not in unavailable:
                adjacent[edge["to"]].append((edge["from"], edge))
        origin = (from_name, self.entered_from if from_name == self.last_location else None)
        queue = deque([origin])
        previous = {}
        seen = {origin}
        while queue:
            current = queue.popleft()
            name, entered_from = current
            blocked = self._blocked_pairs(entered_from, name)
            for neighbor, edge in adjacent[name]:
                arrival = (neighbor, name)
                if arrival in seen or (name, neighbor) in blocked:
                    continue
                seen.add(arrival)
                previous[arrival] = (current, edge)
                if neighbor == to_name:
                    path: list[tuple[str, dict[str, Any]]] = []
                    cursor = arrival
                    while cursor != origin:
                        prior, path_edge = previous[cursor]
                        path.append((cursor[0], path_edge))
                        cursor = prior
                    path.reverse()
                    return path
                queue.append(arrival)
        return None

    def _distances(self, origin: str | None) -> dict[str, int]:
        if origin not in self.nodes:
            return {}
        distances = {}
        for name in self.nodes:
            path = self._find_path(origin, name, undirected=True)
            if path is not None:
                distances[name] = len(path)
        return distances

    @staticmethod
    def _is_open(time: int, open_time: int, close_time: int) -> bool:
        if open_time <= close_time:
            return open_time <= time < close_time
        return time >= open_time or time < close_time

    def _unreachable_pairs(self) -> set[tuple[str, str]]:
        return {
            (edge["from"], edge["to"])
            for edge in self.unreachable_edges
            if edge.get("from") and edge.get("to")
        }

    def _blocked_pairs(self, entered_from: str | None = None, name: str | None = None) -> set[tuple[str, str]]:
        pairs = {
            (path["from"], path["to"])
            for path in self.blocked_paths
            if path.get("day") == self.current_day and path.get("from") and path.get("to")
            and path.get("entered_from") in {None, entered_from}
        }
        if name is not None:
            pairs |= self._pocket_blocked(name, entered_from)
        return pairs

    def _available_edges(self) -> list[dict[str, Any]]:
        unavailable = self._unreachable_pairs() | self._blocked_pairs(self.entered_from, self.last_location)
        return [edge for edge in self.edges if (edge["from"], edge["to"]) not in unavailable]

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({
                "visited": self.visited,
                "last_location": self.last_location,
                "entered_from": self.entered_from,
                "unreachable_edges": self.unreachable_edges,
                "blocked_paths": self.blocked_paths,
                "pockets": self.pockets,
                "day": self.current_day,
            }, indent=2),
            encoding="utf-8",
        )
        replace_with_retry(temporary, self.path)


def travel_to(
    bridge: NamedPipeBridge,
    state: dict[str, Any],
    world_map: WorldMap,
    destination: str,
    action_budget: int,
) -> dict[str, Any]:
    current = state
    controls_executed = 0
    hops_completed: list[str] = []
    control_timings: list[dict[str, Any]] = []

    def finish(status: str, reason: str | None, arrived: bool = False) -> dict[str, Any]:
        world_map.observe(current)
        return {
            "status": status,
            "reason": reason,
            "state": current,
            "controls_executed": controls_executed,
            "hops_completed": hops_completed,
            "arrived": arrived,
            "control_timings": control_timings,
        }

    here = state.get("location")
    if destination not in world_map.nodes:
        return finish("rejected", "unknown_location")
    if destination == here:
        return finish("rejected", "already_there", arrived=True)
    start_health = state.get("health") or 0

    def unsafe() -> bool:
        return bool(
            current.get("eventUp")
            or (current.get("menu") not in {None, "none"})
            or current.get("dialogueResponses")
            or (current.get("health") or 0) < start_health
        )

    def send(kind: str, **arguments: Any) -> dict[str, Any]:
        nonlocal controls_executed, current
        started = clock.perf_counter()
        response = bridge.request(kind, **arguments)
        control_timings.append({
            "control": kind,
            "arguments": arguments,
            "from_location": current.get("location"),
            "entered_from": world_map.entered_from,
            "elapsed_ms": round((clock.perf_counter() - started) * 1000),
            "status": response.get("status"),
        })
        controls_executed += 1
        current = response.get("state") or current
        return response

    def response_reason(response: dict[str, Any], fallback: str) -> str:
        return response.get("error") or response.get("reason") or (
            response.get("status") if response.get("status") != "completed" else fallback
        )

    def remember_failed_edge(from_name: str, to_name: str, response: dict[str, Any]) -> str:
        reason = response_reason(response, "location_did_not_change")
        day = calendar_day(current) or calendar_day(state) or 1
        if reason in {"no_walkable_path", "no_walkable_path_to_exit"}:
            world_map.record_blocked_path(from_name, to_name, day)
        elif reason.startswith("no_exit_") or reason.startswith("door_action_"):
            world_map.record_unreachable(
                from_name, to_name, day, reason
            )
        return reason

    while current.get("location") != destination:
        world_map.observe(current)
        route = world_map.route(current.get("location") or "", destination, current.get("time"))
        if isinstance(route, dict):
            return finish("blocked", f"door_closed_until_{route['blocked_by']['openTime']}")
        if not route:
            return finish("blocked" if controls_executed else "rejected", "no_route")
        if controls_executed + 3 > action_budget:
            return finish("blocked", "action_budget_reached")
        hop = route[0]
        from_name = current.get("location") or ""
        transit = send("go_to_location", location=hop, ticks=600)
        if unsafe():
            return finish("blocked", "world_changed_or_damage_taken")
        if transit.get("status") != "completed" and current.get("location") != hop:
            reason = remember_failed_edge(from_name, hop, transit)
            if (reason in {"no_walkable_path", "no_walkable_path_to_exit"} or reason.startswith(("no_exit_", "door_action_"))) and current.get("location") == from_name:
                if controls_executed + 3 <= action_budget and world_map.route(from_name, destination, current.get("time")):
                    continue
            return finish("blocked", f"hop_failed:{reason}")
        arrival = send("wait", field="location", value=hop, ticks=180)
        if unsafe():
            return finish("blocked", "world_changed_or_damage_taken")
        if current.get("location") != hop:
            failed = transit if transit.get("status") != "completed" else arrival
            return finish("blocked", f"hop_failed:{remember_failed_edge(from_name, hop, failed)}")
        world_map.clear_blocked_path(from_name, hop)
        ready = send("wait", field="can_move", value="true", ticks=180)
        if unsafe():
            return finish("blocked", "world_changed_or_damage_taken")
        if current.get("location") != hop:
            return finish("blocked", "world_changed_or_damage_taken")
        if ready.get("status") != "completed" or current.get("canMove") is not True:
            return finish(
                "blocked", f"hop_failed:{response_reason(ready, 'player_not_ready')}"
            )
        hops_completed.append(hop)

    return finish("completed", None, arrived=current.get("location") == destination)
