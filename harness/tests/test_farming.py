import copy
import unittest

from autoplay_harness.farming import nearest_empty_tiles, plant_seeds


class FarmBridge:
    def __init__(self, failed_click=False, change_location=False):
        self.calls = []
        self.failed_click = failed_click
        self.change_location = change_location
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "toolbarIndex": 0,
                      "viewportWidth": 1920, "viewportHeight": 1080,
                      "inventory": [{"slot": 5, "isSeed": True, "qualifiedId": "(O)472", "stack": 5}],
                      "cropsNearby": [{"x": x, "y": 18, "crop": None, "screenX": 1, "screenY": 2}
                                      for x in range(60, 65)]}

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "navigate":
            self.state.update(tileX=arguments["x"], tileY=arguments["y"])
            for crop in self.state["cropsNearby"]:
                crop["screenX"], crop["screenY"] = crop["x"] * 10, 500
            if self.change_location:
                self.state["location"] = "BusStop"
        elif kind == "press":
            self.state["toolbarIndex"] = 5
        elif kind == "click":
            assert arguments == {"x": self.state["tileX"] * 10, "y": 500, "button": "right"}
            if not self.failed_click:
                next(c for c in self.state["cropsNearby"] if c["x"] == self.state["tileX"])["crop"] = "Parsnip"
                self.state["inventory"][0]["stack"] -= 1
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
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 11)
        self.assertEqual("completed", result["status"])
        self.assertEqual(self.targets, result["tiles_planted"])
        self.assertEqual(11, result["controls_executed"])
        self.assertEqual(0, result["state"]["inventory"][0]["stack"])

    def test_stops_after_ineffective_plant_instead_of_repeating(self):
        bridge = FarmBridge(failed_click=True)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 11)
        self.assertEqual("planting_not_verified_by_crop_and_seed_delta", result["reason"])
        self.assertEqual(3, result["controls_executed"])
        self.assertEqual([], result["tiles_planted"])

    def test_no_inputs_after_location_change(self):
        bridge = FarmBridge(change_location=True)
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 11)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(1, len(bridge.calls))

    def test_budget_stops_between_tiles_and_invalid_target_sends_no_input(self):
        bridge = FarmBridge()
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, self.targets, 4)
        self.assertEqual("partial", result["status"])
        self.assertEqual(1, len(result["tiles_planted"]))
        self.assertEqual(3, len(bridge.calls))
        bridge = FarmBridge()
        result = plant_seeds(bridge, copy.deepcopy(bridge.state), 5, [{"x": 10, "y": 10}], 11)
        self.assertEqual("rejected", result["status"])
        self.assertEqual([], bridge.calls)
