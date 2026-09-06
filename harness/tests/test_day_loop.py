import copy
import unittest

from autoplay_harness.farming import go_home_and_sleep, till_tiles, water_crops


class ToolBridge:
    """Fake bridge where a tool swing only becomes observable after the using_tool wait."""

    def __init__(self, wrong_tool=False, ineffective=False, change_location=False, water=40):
        self.calls = []
        self.wrong_tool = wrong_tool
        self.ineffective = ineffective
        self.change_location = change_location
        self.pending = None
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "eventUp": False, "usingTool": False,
                      "toolbarIndex": 0, "tool": "Axe", "facing": 2, "viewportWidth": 1920,
                      "viewportHeight": 1080, "tileX": 60, "tileY": 10,
                      "wateringCanWater": water, "wateringCanMax": 40,
                      "navigationOriginX": 40, "navigationOriginY": 0,
                      "navigationRows": ["." * 50 for _ in range(50)],
                      "inventory": [{"slot": 0, "name": "Axe"}, {"slot": 1, "name": "Hoe"},
                                    {"slot": 2, "name": "Watering Can"}],
                      "cropsNearby": [{"x": x, "y": 18, "crop": "Parsnip", "watered": False,
                                       "screenX": 0, "screenY": 0} for x in range(60, 65)],
                      "tillableNearby": [{"x": x, "y": 20, "screenX": 0, "screenY": 0}
                                         for x in range(70, 75)]}
        self._refresh()

    def _refresh(self):
        for entry in self.state["cropsNearby"] + self.state["tillableNearby"]:
            entry["screenX"], entry["screenY"] = entry["x"] * 10, entry["y"] * 10

    def _faced(self):
        delta = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}[self.state["facing"]]
        return (self.state["tileX"] + delta[0], self.state["tileY"] + delta[1])

    def _apply(self, tile):
        if self.ineffective or tile is None:
            return
        crop = next((entry for entry in self.state["cropsNearby"] if (entry["x"], entry["y"]) == tile), None)
        if crop is not None:
            crop["watered"] = True
            self.state["wateringCanWater"] -= 1
            return
        ground = next((entry for entry in self.state["tillableNearby"] if (entry["x"], entry["y"]) == tile), None)
        if ground is not None:
            self.state["tillableNearby"].remove(ground)
            self.state["cropsNearby"].append({"x": tile[0], "y": tile[1], "crop": None, "watered": False,
                                              "screenX": tile[0] * 10, "screenY": tile[1] * 10})

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "navigate":
            self.state.update(tileX=arguments["x"], tileY=arguments["y"])
            self._refresh()
            if self.change_location:
                self.state["location"] = "BusStop"
        elif kind == "press":
            button = arguments["buttons"][0]
            if button in {"W", "A", "S", "D"}:
                self.state["facing"] = {"W": 0, "D": 1, "S": 2, "A": 3}[button]
            else:
                selected = {"D2": ("Hoe", 1), "D3": ("Watering Can", 2)}[button]
                self.state.update(tool="Pickaxe" if self.wrong_tool else selected[0],
                                  toolbarIndex=selected[1])
        elif kind == "click":
            tile = self._faced()
            expected = next(entry for entry in self.state["cropsNearby"] + self.state["tillableNearby"]
                            if (entry["x"], entry["y"]) == tile)
            assert arguments == {"x": expected["screenX"], "y": expected["screenY"], "button": "left"}
            self.pending = tile
            self.state.update(usingTool=True, canMove=False)
        elif kind == "wait":
            self.state.update(usingTool=False, canMove=True)
            self._apply(self.pending)
            self.pending = None
        return {"status": "completed", "state": copy.deepcopy(self.state)}

    def selections(self, button):
        return sum(1 for kind, arguments in self.calls
                   if kind == "press" and arguments["buttons"] == [button])


class SleepBridge:
    def __init__(self, advance_day=True, prompt=True, transit=True, component_click=True, save=True):
        self.calls = []
        self.advance_day = advance_day
        self.prompt = prompt
        self.transit = transit
        self.component_click = component_click
        self.save = save
        self.idles = 0
        self.night_idle_rounds = 0
        self.state = {"worldReady": True, "playerFree": True, "canMove": True, "menu": "none",
                      "location": "Farm", "health": 100, "eventUp": False, "day": 5, "time": 1900,
                      "stamina": 120, "money": 500, "tileX": 64, "tileY": 15,
                      "nightActive": False, "saveCount": 0, "dialogueResponses": []}

    def request(self, kind, **arguments):
        self.calls.append((kind, arguments))
        if kind == "go_to_location":
            self.state.update(location="FarmHouse" if self.transit else "Farm", tileX=6, tileY=10,
                              bedTile={"x": 9, "y": 9, "screenX": 100, "screenY": 100})
            return {"status": "blocked", "reason": "no_progress_toward_(3,12)",
                    "state": copy.deepcopy(self.state)}
        elif kind == "navigate":
            self.state.update(tileX=arguments["x"], tileY=arguments["y"])
            if self.prompt:
                self.state.update(menu="DialogueBox:question=True:selected=0:responses=2",
                                  dialogueResponses=[{"index": 0, "key": "Yes", "text": "Yes"},
                                                     {"index": 1, "key": "No", "text": "No"}])
        elif kind == "choose_dialogue_response":
            if self.component_click:
                self.state.update(menu="ShippingMenu", dialogueResponses=[], playerFree=False)
        elif kind == "press" and arguments["buttons"] == ["Y"]:
            self.state.update(menu="ShippingMenu", dialogueResponses=[], playerFree=False)
        elif kind == "press" and self.state["menu"] == "ShippingMenu":
            self.state["menu"] = "none"
        elif kind == "idle":
            self.idles += 1
            if (self.advance_day and self.state["day"] == 5 and self.idles >= 3
                    and self.state["menu"] == "none"):
                self.state.update(day=6, time=620, stamina=270, playerFree=False, canMove=False,
                                  nightActive=True)
            elif self.state["day"] == 6 and arguments["ticks"] == 300:
                self.night_idle_rounds += 1
                if self.night_idle_rounds >= 3:
                    self.state.update(nightActive=False, playerFree=True, canMove=True)
                    if self.save:
                        self.state["saveCount"] = 1
        return {"status": "completed", "state": copy.deepcopy(self.state)}


class WaterCropsTests(unittest.TestCase):
    targets = [{"x": x, "y": 18} for x in range(60, 63)]

    def test_three_crops_use_one_selection_and_spend_one_water_each(self):
        bridge = ToolBridge()
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 12)
        self.assertEqual("completed", result["status"])
        self.assertEqual([{"x": 60, "y": 18, "watered": True, "water_left": 39},
                          {"x": 61, "y": 18, "watered": True, "water_left": 38},
                          {"x": 62, "y": 18, "watered": True, "water_left": 37}], result["tiles_watered"])
        self.assertEqual(11, result["controls_executed"])
        self.assertEqual(1, bridge.selections("D3"))

    def test_invalid_arguments_send_no_input(self):
        bridge = ToolBridge()
        state = copy.deepcopy(bridge.state)
        repeated = water_crops(bridge, state, [{"x": 60, "y": 18}, {"x": 60, "y": 18}], 10)
        self.assertEqual(("rejected", "supply_one_to_six_distinct_tiles"),
                         (repeated["status"], repeated["reason"]))
        state["cropsNearby"][0]["watered"] = True
        wet = water_crops(bridge, state, [{"x": 60, "y": 18}], 10)
        self.assertEqual("targets_must_be_observed_unwatered_crops", wet["reason"])
        bare = water_crops(bridge, state, [{"x": 99, "y": 99}], 10)
        self.assertEqual("targets_must_be_observed_unwatered_crops", bare["reason"])
        self.assertEqual([], bridge.calls)

    def test_wrong_tool_selection_is_not_repeated(self):
        bridge = ToolBridge(wrong_tool=True)
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 10)
        self.assertEqual(("blocked", "tool_selection_failed"), (result["status"], result["reason"]))
        self.assertEqual(["navigate", "press"], [call[0] for call in bridge.calls])

    def test_no_inputs_after_location_change(self):
        bridge = ToolBridge(change_location=True)
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 10)
        self.assertEqual(("blocked", "world_changed_or_damage_taken"), (result["status"], result["reason"]))
        self.assertEqual(1, len(bridge.calls))

    def test_budget_stops_between_tiles(self):
        bridge = ToolBridge()
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 5)
        self.assertEqual(("partial", "action_budget_reached"), (result["status"], result["reason"]))
        self.assertEqual(1, len(result["tiles_watered"]))
        self.assertEqual(4, len(bridge.calls))

    def test_empty_watering_can_blocks_before_any_input(self):
        bridge = ToolBridge(water=0)
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 10)
        self.assertEqual(("blocked", "watering_can_empty"), (result["status"], result["reason"]))
        self.assertEqual([], bridge.calls)

    def test_ineffective_swing_retries_once_then_stops(self):
        bridge = ToolBridge(ineffective=True)
        result = water_crops(bridge, copy.deepcopy(bridge.state), self.targets, 10)
        self.assertEqual("watering_not_verified_by_watered_state_and_water_delta", result["reason"])
        self.assertEqual([], result["tiles_watered"])
        self.assertEqual(7, len(bridge.calls))


class TillTilesTests(unittest.TestCase):
    targets = [{"x": x, "y": 20} for x in range(70, 72)]

    def test_two_tiles_become_empty_tilled_soil(self):
        bridge = ToolBridge()
        result = till_tiles(bridge, copy.deepcopy(bridge.state), self.targets, 9)
        self.assertEqual("completed", result["status"])
        self.assertEqual([{"x": 70, "y": 20, "tilled": True}, {"x": 71, "y": 20, "tilled": True}],
                         result["tiles_tilled"])
        self.assertEqual(8, result["controls_executed"])
        self.assertEqual(1, bridge.selections("D2"))

    def test_untillable_or_repeated_targets_send_no_input(self):
        bridge = ToolBridge()
        state = copy.deepcopy(bridge.state)
        self.assertEqual("targets_must_be_observed_tillable_tiles",
                         till_tiles(bridge, state, [{"x": 60, "y": 18}], 9)["reason"])
        self.assertEqual("supply_one_to_six_distinct_tiles",
                         till_tiles(bridge, state, [{"x": 70, "y": 20}] * 2, 9)["reason"])
        self.assertEqual([], bridge.calls)

    def test_wrong_tool_selection_is_not_repeated(self):
        bridge = ToolBridge(wrong_tool=True)
        result = till_tiles(bridge, copy.deepcopy(bridge.state), self.targets, 9)
        self.assertEqual(("blocked", "tool_selection_failed"), (result["status"], result["reason"]))
        self.assertEqual(["navigate", "press"], [call[0] for call in bridge.calls])

    def test_ineffective_swing_stops_instead_of_repeating(self):
        bridge = ToolBridge(ineffective=True)
        result = till_tiles(bridge, copy.deepcopy(bridge.state), self.targets, 9)
        self.assertEqual("tilling_not_verified_by_new_empty_soil", result["reason"])
        self.assertEqual([], result["tiles_tilled"])

    def test_budget_stops_between_tiles(self):
        bridge = ToolBridge()
        result = till_tiles(bridge, copy.deepcopy(bridge.state), self.targets, 5)
        self.assertEqual(("partial", "action_budget_reached"), (result["status"], result["reason"]))
        self.assertEqual(1, len(result["tiles_tilled"]))


class GoHomeAndSleepTests(unittest.TestCase):
    def test_night_transition_reports_the_new_day(self):
        bridge = SleepBridge()
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 12)
        self.assertEqual("completed", result["status"])
        self.assertEqual({"day": 5, "time": 1900, "stamina": 120, "money": 500}, result["before"])
        self.assertEqual({"day": 6, "time": 620, "stamina": 270, "money": 500}, result["after"])
        self.assertEqual(["go_to_location", "wait", "wait", "navigate", "idle",
                          "choose_dialogue_response", "idle", "press", "idle",
                          "idle", "idle", "idle"],
                         [call[0] for call in bridge.calls])
        self.assertNotIn(["Y"], [arguments["buttons"] for kind, arguments in bridge.calls
                                 if kind == "press"])
        self.assertIn(("press", {"buttons": ["X"]}), bridge.calls)
        self.assertEqual(["enter_farmhouse", "reach_bed", "answer_sleep_prompt", "new_day",
                          "night_finished"],
                         [step["step"] for step in result["steps"]])
        self.assertEqual({"step": "night_finished", "idle_rounds": 3, "saveCount": 1},
                         result["steps"][-1])

    def test_unanswered_question_falls_back_to_the_yes_hotkey(self):
        bridge = SleepBridge(component_click=False)
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 20)
        self.assertEqual("completed", result["status"])
        self.assertIn(("press", {"buttons": ["Y"]}), bridge.calls)

    def test_night_without_a_new_save_reports_partial(self):
        bridge = SleepBridge(save=False)
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 30)
        self.assertEqual(("partial", "night_did_not_finish"), (result["status"], result["reason"]))
        self.assertEqual(0, result["state"]["saveCount"])

    def test_day_that_does_not_advance_reports_partial(self):
        bridge = SleepBridge(advance_day=False)
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 8)
        self.assertEqual(("partial", "night_transition_did_not_finish"), (result["status"], result["reason"]))
        self.assertEqual(5, result["after"]["day"])

    def test_missing_sleep_prompt_stops_before_the_night(self):
        bridge = SleepBridge(prompt=False)
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 12)
        self.assertEqual(("blocked", "sleep_prompt_did_not_appear"), (result["status"], result["reason"]))
        self.assertNotIn("choose_dialogue_response", [call[0] for call in bridge.calls])

    def test_transit_that_never_arrives_stops_before_the_bed(self):
        bridge = SleepBridge(transit=False)
        result = go_home_and_sleep(bridge, copy.deepcopy(bridge.state), 12)
        self.assertEqual(("blocked", "no_progress_toward_(3,12)"), (result["status"], result["reason"]))
        self.assertEqual(["go_to_location", "wait"], [call[0] for call in bridge.calls])

    def test_other_locations_and_busy_players_send_no_input(self):
        bridge = SleepBridge()
        state = copy.deepcopy(bridge.state)
        away = go_home_and_sleep(bridge, {**state, "location": "Town"}, 12)
        self.assertEqual(("blocked", "not_on_farm"), (away["status"], away["reason"]))
        busy = go_home_and_sleep(bridge, {**state, "menu": "ShippingMenu"}, 12)
        self.assertEqual("player_must_be_free_in_a_loaded_world_outside_menus", busy["reason"])
        self.assertEqual([], bridge.calls)
