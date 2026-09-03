from __future__ import annotations

import json
import re
import time as clock
from collections import deque
from pathlib import Path
from typing import Any

from .calendar import calendar_day

from .bridge import BridgeError, NamedPipeBridge


class WorldMap:
    def __init__(self, state_directory: Path) -> None:
        self.path = state_directory / "world.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.visited: dict[str, int] = data.get("visited", {})
        self.last_location: str | None = data.get("last_location")
        self.unreachable_edges: list[dict[str, Any]] = data.get("unreachable_edges", [])
        self.blocked_paths: list[dict[str, Any]] = data.get("blocked_paths", [])
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
        changed = location != self.last_location or changed
        self.last_location = location
        if location not in self.visited:
            self.visited[location] = calendar_day(state) or 1
            changed = True
        if changed:
            self._save()

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
        if value in self.blocked_paths:
            return
        self.blocked_paths.append(value)
        self._save()

    def clear_blocked_path(self, from_name: str, to_name: str) -> None:
        remaining = [
            path for path in self.blocked_paths
            if path.get("from") != from_name or path.get("to") != to_name
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
        blocked_pairs = self._blocked_pairs()
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
        unavailable = self._unreachable_pairs() | self._blocked_pairs()
        for edge in self._available_edges():
            adjacent[edge["from"]].append((edge["to"], edge))
            if undirected and (edge["to"], edge["from"]) not in unavailable:
                adjacent[edge["to"]].append((edge["from"], edge))
        queue = deque([from_name])
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        seen = {from_name}
        while queue:
            name = queue.popleft()
            for neighbor, edge in adjacent[name]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                previous[neighbor] = (name, edge)
                if neighbor == to_name:
                    path: list[tuple[str, dict[str, Any]]] = []
                    cursor = to_name
                    while cursor != from_name:
                        prior, path_edge = previous[cursor]
                        path.append((cursor, path_edge))
                        cursor = prior
                    path.reverse()
                    return path
                queue.append(neighbor)
        return None

    def _distances(self, origin: str | None) -> dict[str, int]:
        if origin not in self.nodes:
            return {}
        adjacent = {name: set() for name in self.nodes}
        unavailable = self._unreachable_pairs() | self._blocked_pairs()
        for edge in self._available_edges():
            adjacent[edge["from"]].add(edge["to"])
            if (edge["to"], edge["from"]) not in unavailable:
                adjacent[edge["to"]].add(edge["from"])
        distances = {origin: 0}
        queue = deque([origin])
        while queue:
            name = queue.popleft()
            for neighbor in adjacent[name]:
                if neighbor not in distances:
                    distances[neighbor] = distances[name] + 1
                    queue.append(neighbor)
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

    def _blocked_pairs(self) -> set[tuple[str, str]]:
        return {
            (path["from"], path["to"])
            for path in self.blocked_paths
            if path.get("day") == self.current_day and path.get("from") and path.get("to")
        }

    def _available_edges(self) -> list[dict[str, Any]]:
        unavailable = self._unreachable_pairs() | self._blocked_pairs()
        return [edge for edge in self.edges if (edge["from"], edge["to"]) not in unavailable]

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({
                "visited": self.visited,
                "last_location": self.last_location,
                "unreachable_edges": self.unreachable_edges,
                "blocked_paths": self.blocked_paths,
                "day": self.current_day,
            }, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


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
    route = world_map.route(here or "", destination, state.get("time"))
    if isinstance(route, dict):
        open_time = route["blocked_by"]["openTime"]
        return finish("blocked", f"door_closed_until_{open_time}")
    if not route:
        return finish("rejected", "no_route")

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
        if reason == "no_walkable_path":
            world_map.record_blocked_path(from_name, to_name, day)
        elif reason.startswith("no_exit_") or reason.startswith("door_action_"):
            world_map.record_unreachable(
                from_name, to_name, day, reason
            )
        return reason

    for hop in route:
        if controls_executed + 3 > action_budget:
            return finish("blocked", "action_budget_reached")
        from_name = current.get("location") or ""
        transit = send("go_to_location", location=hop, ticks=600)
        if transit.get("status") != "completed" and current.get("location") != hop:
            return finish("blocked", f"hop_failed:{remember_failed_edge(from_name, hop, transit)}")
        if unsafe():
            return finish("blocked", "world_changed_or_damage_taken")
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
