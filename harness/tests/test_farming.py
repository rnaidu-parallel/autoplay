import copy
import unittest

from autoplay_harness.farming import clear_debris, nearest_empty_tiles, plant_seeds, till_tiles, water_crops


class FarmBridge:
    def __init__(self, plant_on_click=1, change_location=False, wrong_facing=False):
        self.calls = []
        self.plant_on_click = plant_on_click
        self.clicks = {}
        self.change_location = change_location
        self.wrong_facing = wrong_facing
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "eventUp": False, "toolbarIndex": 0,
                      "tileX": 60, "tileY": 10, "facing": 0,
                      "viewportWidth": 1920, "viewportHeight": 1080,
                      "navigationOriginX": 40, "navigationOriginY": 0,
                      "navigationRows": ["." * 50 for _ in range(50)],
                      "inventory": [{"slot": 5, "isSeed": True, "qualifiedId": "(O)472", "stack": 5}],
                      "cropsNearby": [{"x": x, "y": 18, "crop": None, "screenX": 1, "screenY": 2}
                                      for x in range(60, 65)]}

    def _faced(self):
        delta = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}[self.state["facing"]]
        return self.state["tileX"] + delta[0], self.state["tileY"] + delta[1]

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "navigate":
            self.state.update(tileX=arguments["x"], tileY=arguments["y"])
            for crop in self.state["cropsNearby"]:
                crop["screenX"], crop["screenY"] = crop["x"] * 10, 500
            if self.change_location:
                self.state["location"] = "BusStop"
        elif kind == "press":
            button = arguments["buttons"][0]
            if button in {"W", "A", "S", "D"}:
                if not self.wrong_facing:
                    self.state["facing"] = {"W": 0, "D": 1, "S": 2, "A": 3}[button]
            else:
                self.state["toolbarIndex"] = 5
        elif kind == "click":
            target = self._faced()
            crop = next(crop for crop in self.state["cropsNearby"]
                        if (crop["x"], crop["y"]) == target)
            assert arguments == {"x": crop["screenX"], "y": crop["screenY"], "button": "right"}
            self.clicks[target] = self.clicks.get(target, 0) + 1
            if self.clicks[target] == self.plant_on_click:
                crop["crop"] = "Parsnip"
                self.state["inventory"][0]["stack"] -= 1
        return {"status": "completed", "state": copy.deepcopy(self.state)}


class DebrisBridge:
    def __init__(self, disappear_after=2, stamina=100):
        self.calls = []
        self.clicks = 0
        self.disappear_after = disappear_after
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "stamina": stamina, "eventUp": False,
                      "tileX": 10, "tileY": 10, "facing": 0, "tool": "Hoe",
                      "viewportWidth": 1920, "viewportHeight": 1080,
                      "navigationOriginX": 0, "navigationOriginY": 0,
                      "navigationRows": ["." * 25 for _ in range(25)],
                      "inventory": [{"slot": 1, "name": "Copper Axe"}],
                      "inventoryCounts": {"Wood": 3},
                      "nearbyObjects": [{"x": 10, "y": 9, "name": "Twig", "recommendedTool": "Axe",
                                         "screenX": 100, "screenY": 200}]}

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "press":
            button = arguments["buttons"][0]
            if button == "D2":
                self.state["tool"] = "Copper Axe"
            elif button in {"W", "A", "S", "D"}:
                self.state["facing"] = {"W": 0, "D": 1, "S": 2, "A": 3}[button]
        elif kind == "click":
            self.clicks += 1
            if self.clicks > 1:
                self.state["stamina"] -= 2
            if self.clicks == self.disappear_after:
                self.state["nearbyObjects"] = []
                self.state["inventoryCounts"]["Wood"] += 1
        return {"status": "completed", "state": copy.deepcopy(self.state)}


class RetryingToolBridge:
    def __init__(self, tool):
        self.calls = []
        self.clicks = 0
        self.tool = tool
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "stamina": 100, "eventUp": False,
                      "tileX": 10, "tileY": 10, "facing": 0, "tool": tool,
                      "viewportWidth": 1920, "viewportHeight": 1080,
                      "navigationOriginX": 0, "navigationOriginY": 0,
                      "navigationRows": ["." * 25 for _ in range(25)],
                      "inventory": [{"slot": 0, "name": tool}],
                      "wateringCanWater": 40,
                      "cropsNearby": [{"x": 10, "y": 9, "crop": "Parsnip", "watered": False,
                                        "screenX": 100, "screenY": 200}],
                      "tillableNearby": [{"x": 10, "y": 9, "screenX": 100, "screenY": 200}]}

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "click":
            self.clicks += 1
            if self.clicks == 2 and self.tool == "Watering Can":
                self.state["cropsNearby"][0]["watered"] = True
                self.state["wateringCanWater"] -= 1
            elif self.clicks == 2:
                self.state["tillableNearby"] = []
                self.state["cropsNearby"] = [{"x": 10, "y": 9, "crop": None,
                                               "screenX": 100, "screenY": 200}]
        return {"status": "completed", "state": copy.deepcopy(self.state)}


class FarmingTests(unittest.TestCase):
    targets = [{"x": x, "y": 18} for x in range(60, 65)]

    def test_nearest_selection_uses_only_observed_empty_unique_tiles(self):
        state = {"tileX": 64, "tileY": 15, "cropsNearby": [
            {"x": 71, "y": 27, "crop": None}, {"x": 64, "y": 19, "crop": None},
            {"x": 64, "y": 19, "crop": None}, {"x": 62, "y": 18, "crop": None},
            {"x": 64, "y": 16, "crop": "Parsnip"}]}
        self.assertEqual([{"x": 64, "y": 19}, {"x": 62, "y": 18}], nearest_empty_tiles(state, 2))
        self.assertEqual([], nearest_empty_tiles(state, 4))

    def test_five_crops_use_fresh_targets_and_one_selection(self):
        bridge = FarmBridge()
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 25)
        self.assertEqual("completed", result["status"])
        self.assertEqual(self.targets, result["tiles_planted"])
        self.assertEqual(0, result["state"]["inventory"][0]["stack"])
        navigations = [arguments for kind, arguments in bridge.calls if kind == "navigate"]
        self.assertEqual(5, len(navigations))
        self.assertTrue(all(abs(call["x"] - target["x"]) + abs(call["y"] - target["y"]) == 1
                            for call, target in zip(navigations, self.targets)))
        self.assertEqual(1, sum(kind == "press" and arguments["buttons"] == ["D6"]
                                for kind, arguments in bridge.calls))
        self.assertTrue(all(arguments["button"] == "right"
                            for kind, arguments in bridge.calls if kind == "click"))

    def test_second_click_plants_and_marks_the_tile_retried(self):
        bridge = FarmBridge(plant_on_click=2)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, [self.targets[0]], 5)
        self.assertEqual("completed", result["status"])
        self.assertEqual([{"x": 60, "y": 18, "retried": True}], result["tiles_planted"])
        self.assertEqual(5, result["controls_executed"])

    def test_two_ineffective_clicks_report_existing_failure(self):
        bridge = FarmBridge(plant_on_click=None)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 25)
        self.assertEqual("planting_not_verified_by_crop_and_seed_delta", result["reason"])
        self.assertEqual(5, result["controls_executed"])
        self.assertEqual([], result["tiles_planted"])

    def test_no_inputs_after_location_change(self):
        bridge = FarmBridge(change_location=True)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 25)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(1, len(bridge.calls))

    def test_budget_stops_between_tiles_and_invalid_target_sends_no_input(self):
        bridge = FarmBridge()
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 5)
        self.assertEqual("partial", result["status"])
        self.assertEqual(1, len(result["tiles_planted"]))
        self.assertEqual(4, len(bridge.calls))
        bridge = FarmBridge()
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, [{"x": 10, "y": 10}], 25)
        self.assertEqual("rejected", result["status"])
        self.assertEqual([], bridge.calls)

    def test_wrong_facing_stops_before_clicking(self):
        bridge = FarmBridge(wrong_facing=True)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 25)
        self.assertEqual(("blocked", "facing_change_failed"), (result["status"], result["reason"]))
        self.assertEqual(["navigate", "press", "press"], [kind for kind, _ in bridge.calls])

    def test_no_passable_neighbor_stops_without_input(self):
        bridge = FarmBridge()
        bridge.state["navigationRows"] = ["#" * 50 for _ in range(50)]
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 25)
        self.assertEqual(("blocked", "no_passable_tile_next_to_target"),
                         (result["status"], result["reason"]))
        self.assertEqual([], bridge.calls)

    def test_clear_debris_matches_upgraded_tool_and_refaces_after_a_miss(self):
        bridge = DebrisBridge()
        result = clear_debris(bridge, copy.deepcopy(bridge.state), [{"x": 10, "y": 9}], 16)

        self.assertEqual("completed", result["status"])
        self.assertEqual([{"x": 10, "y": 9, "kind": "Twig", "swings": 2}], result["tiles_cleared"])
        self.assertEqual({"Wood": 1}, result["items_gained"])
        self.assertIn(("press", {"buttons": ["D2"]}), bridge.calls)
        self.assertEqual(1, sum(kind == "press" and arguments["buttons"] == ["W"]
                                for kind, arguments in bridge.calls))

    def test_clear_debris_stops_before_swinging_at_low_stamina(self):
        bridge = DebrisBridge(stamina=19)
        result = clear_debris(bridge, copy.deepcopy(bridge.state), [{"x": 10, "y": 9}], 16)

        self.assertEqual(("blocked", "stamina_low"), (result["status"], result["reason"]))
        self.assertFalse(any(kind == "click" for kind, _arguments in bridge.calls))

    def test_watering_and_tilling_retry_after_forced_reface(self):
        water_bridge = RetryingToolBridge("Watering Can")
        water = water_crops(water_bridge, copy.deepcopy(water_bridge.state), [{"x": 10, "y": 9}], 8)
        self.assertEqual("completed", water["status"])
        self.assertTrue(water["tiles_watered"][0]["retried"])
        self.assertIn(("press", {"buttons": ["W"]}), water_bridge.calls)

        till_bridge = RetryingToolBridge("Hoe")
        till_bridge.state["cropsNearby"] = []
        tilled = till_tiles(till_bridge, copy.deepcopy(till_bridge.state), [{"x": 10, "y": 9}], 8)
        self.assertEqual("completed", tilled["status"])
        self.assertTrue(tilled["tiles_tilled"][0]["retried"])
        self.assertIn(("press", {"buttons": ["W"]}), till_bridge.calls)
