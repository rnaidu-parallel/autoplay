import tempfile
import unittest
from pathlib import Path

from autoplay_harness.world import WorldMap, travel_to


NODES = [
    {"name": name, "isOutdoors": name in {"A", "B", "C", "D", "Farm"},
     "isFarm": name == "Farm"}
    for name in ("A", "B", "C", "D", "Farm", "FarmHouse", "Cellar", "Cellar2", "Shop")
]
EDGES = [
    {"from": "A", "to": "B", "x": 1, "y": 1, "kind": "warp"},
    {"from": "B", "to": "C", "x": 2, "y": 2, "kind": "action_warp",
     "requiresAction": True, "openTime": 900, "closeTime": 1700},
    {"from": "C", "to": "D", "x": 3, "y": 3, "kind": "warp"},
    {"from": "FarmHouse", "to": "Farm", "x": 4, "y": 4, "kind": "warp"},
    {"from": "Farm", "to": "A", "x": 5, "y": 5, "kind": "warp"},
    {"from": "A", "to": "Cellar", "x": 6, "y": 6, "kind": "warp"},
    {"from": "A", "to": "Cellar2", "x": 7, "y": 7, "kind": "warp"},
    {"from": "A", "to": "Shop", "x": 8, "y": 8, "kind": "door"},
]


class _MapBridge:
    def __init__(self) -> None:
        self.calls = []

    def request(self, request_type, **arguments):
        self.calls.append((request_type, arguments))
        return {
            "status": "completed",
            "nodes": NODES,
            "edges": EDGES,
            "state": {"worldMapVersion": 1},
        }


class _TravelBridge:
    def __init__(self, location: str, event: bool = False) -> None:
        self.location = location
        self.event = event
        self.calls = []

    def request(self, request_type, **arguments):
        self.calls.append((request_type, arguments))
        if request_type == "go_to_location":
            self.location = arguments["location"]
        return {
            "status": "completed",
            "state": {
                "location": self.location,
                "canMove": True,
                "menu": "none",
                "eventUp": self.event,
                "dialogueResponses": [],
                "health": 100,
            },
        }


class _RejectingTravelBridge:
    def __init__(self) -> None:
        self.calls = []

    def request(self, request_type, **arguments):
        self.calls.append((request_type, arguments))
        return {
            "status": "blocked",
            "error": "no_exit_to_location",
            "state": {"location": "A", "day": 5, "health": 100, "menu": "none"},
        }


class _BlockedTravelBridge:
    def __init__(self) -> None:
        self.calls = []

    def request(self, request_type, **arguments):
        self.calls.append((request_type, arguments))
        return {
            "status": "blocked",
            "error": "no_walkable_path",
            "state": {"location": "A", "day": 5, "health": 100, "menu": "none"},
        }


class WorldMapTests(unittest.TestCase):
    def make_world(self, directory: str) -> WorldMap:
        world = WorldMap(Path(directory))
        world.load(_MapBridge(), 1)
        return world

    def test_load_fetches_graph_once_until_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = _MapBridge()
            world = WorldMap(Path(directory))
            world.load(bridge, 1)
            world.load(bridge, 1)
            world.load(bridge, 2)
            self.assertEqual(["world_map", "world_map"], [call[0] for call in bridge.calls])
            self.assertEqual("A", world.nodes["A"]["name"])

    def test_route_uses_three_hop_bfs_and_undirected_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            self.assertEqual(["B", "C", "D"], world.route("A", "D"))
            self.assertEqual(["A", "Farm"], world.route("B", "Farm"))

    def test_locked_door_reports_closed_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            blocked = world.route("A", "D", 800)
            self.assertEqual(["B", "C", "D"], blocked["route"])
            self.assertEqual(900, blocked["blocked_by"]["openTime"])
            self.assertEqual(["B", "C", "D"], world.route("A", "D", 900))

    def test_visited_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = WorldMap(Path(directory))
            world.observe({"location": "Town", "day": 4})
            reloaded = WorldMap(Path(directory))
            self.assertEqual(4, reloaded.visited["Town"])
            self.assertEqual(1, reloaded.visited["Farm"])
            self.assertEqual("Town", reloaded.last_location)

    def test_summary_contains_exits_nearest_unvisited_counts_and_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            world.observe({"location": "A", "day": 2})
            summary = world.summary("A", 1200)
            self.assertEqual("A", summary["here"])
            self.assertIn("B", summary["exits"])
            self.assertEqual(["B", "C", "D", "Shop"], summary["unvisited"])
            self.assertNotIn("Cellar", summary["unvisited"])
            self.assertNotIn("Cellar2", summary["unvisited"])
            self.assertEqual([], summary["unreachable"])
            self.assertEqual(3, summary["visitedCount"])
            self.assertEqual(9, summary["totalCount"])
            self.assertEqual(["Farm", "FarmHouse"], summary["routeHome"])

    def test_travel_to_completes_verified_hops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            bridge = _TravelBridge("A")
            result = travel_to(
                bridge,
                {"location": "A", "time": 1200, "health": 100, "menu": "none"},
                world,
                "C",
                6,
            )
            self.assertEqual("completed", result["status"])
            self.assertEqual(["B", "C"], result["hops_completed"])
            self.assertTrue(result["arrived"])
            self.assertEqual(6, result["controls_executed"])

    def test_travel_to_stops_on_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            bridge = _TravelBridge("A", event=True)
            result = travel_to(
                bridge,
                {"location": "A", "time": 1200, "health": 100, "menu": "none"},
                world,
                "B",
                3,
            )
            self.assertEqual("blocked", result["status"])
            self.assertEqual("world_changed_or_damage_taken", result["reason"])
            self.assertEqual(1, result["controls_executed"])

    def test_rejected_hop_preserves_reason_and_persists_unreachable_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            bridge = _RejectingTravelBridge()
            result = travel_to(
                bridge,
                {"location": "A", "day": 5, "time": 1200, "health": 100, "menu": "none"},
                world,
                "B",
                3,
            )
            self.assertEqual("blocked", result["status"])
            self.assertEqual("hop_failed:no_exit_to_location", result["reason"])
            self.assertEqual(1, result["controls_executed"])
            self.assertEqual(
                [{"from": "A", "to": "B", "day": 5, "reason": "no_exit_to_location"}],
                world.unreachable_edges,
            )

            reloaded = WorldMap(Path(directory))
            reloaded.load(_MapBridge(), 1)
            self.assertEqual([], reloaded.route("A", "B"))
            summary = reloaded.summary("A", 1200)
            self.assertNotIn("B", summary["exits"])
            self.assertEqual(["B"], summary["unreachable"])

    def test_no_walkable_hop_is_blocked_for_today_and_expires_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            bridge = _BlockedTravelBridge()
            result = travel_to(
                bridge,
                {"location": "A", "day": 5, "time": 1200, "health": 100, "menu": "none"},
                world,
                "B",
                3,
            )

            self.assertEqual("hop_failed:no_walkable_path", result["reason"])
            self.assertEqual(
                [{"from": "A", "to": "B", "day": 5}],
                world.blocked_paths,
            )
            self.assertEqual(["B"], world.summary("A", 1200)["blockedNow"])
            self.assertEqual([], world.route("A", "B"))

            reloaded = WorldMap(Path(directory))
            reloaded.load(_MapBridge(), 1)
            self.assertEqual(["B"], reloaded.summary("A", 1200)["blockedNow"])
            reloaded.observe({"location": "A", "day": 6})

            self.assertEqual([], reloaded.blocked_paths)
            self.assertEqual([], reloaded.summary("A", 1200)["blockedNow"])
            self.assertEqual(["B"], reloaded.route("A", "B"))

    def test_travel_to_rejects_unknown_destination_without_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world = self.make_world(directory)
            bridge = _TravelBridge("A")
            result = travel_to(bridge, {"location": "A"}, world, "Nowhere", 3)
            self.assertEqual("rejected", result["status"])
            self.assertEqual("unknown_location", result["reason"])
            self.assertEqual([], bridge.calls)


if __name__ == "__main__":
    unittest.main()
