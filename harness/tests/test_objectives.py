import tempfile
import unittest
from pathlib import Path

from autoplay_harness.objectives import ObjectiveError, ObjectiveLedger, evaluate_state_condition


class ObjectiveLedgerTests(unittest.TestCase):
    def test_snapshot_bounds_long_lived_ledger_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Start", "day >= 2")
            ledger.data["active"]["milestone"] = "m" * 1400
            ledger.data["active"]["last_review_reason"] = "verbose"
            ledger.data["history"] = [
                {"id": f"objective-{index}", "goal": f"Goal {index}", "status": "completed"}
                for index in range(8)
            ]
            ledger.data["progress"] = [
                {"note": f"Progress {index}", "evidence": "e"} for index in range(9)
            ]

            snapshot = ledger.snapshot()

            self.assertEqual(3, len(snapshot["history"]))
            self.assertEqual("objective-5", snapshot["history"][0]["id"])
            self.assertEqual(4, len(snapshot["progress"]))
            self.assertLessEqual(len(snapshot["active"]["milestone"]), 1201)
            self.assertNotIn("last_review_reason", snapshot["active"])

    def test_active_objective_cannot_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Water crops", "All crops are watered")
            with self.assertRaises(ObjectiveError):
                ledger.set_objective("Go fishing", "Catch one fish", "Reach the lake")

    def test_completed_objective_can_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Water crops", "All crops are watered")
            ledger.complete_objective("All crop tiles report watered")
            ledger.set_objective("Go fishing", "location is Mountain", "Reach the lake")
            self.assertEqual("Go fishing", ledger.snapshot()["active"]["goal"])

    def test_explicit_startup_objective_supersedes_persisted_active_objective(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "objectives.json"
            ObjectiveLedger(path, "Water crops", "All crops are watered")
            ledger = ObjectiveLedger(path, "Go fishing", "Catch one fish")
            self.assertEqual("Go fishing", ledger.snapshot()["active"]["goal"])
            self.assertEqual("superseded", ledger.snapshot()["history"][0]["status"])

    def test_structured_success_condition_reports_mismatch(self) -> None:
        result = evaluate_state_condition(
            "worldReady is true, location is Farm, and playerFree is true",
            {"worldReady": True, "location": "FarmHouse", "playerFree": True},
        )
        self.assertIsNotNone(result)
        self.assertFalse(result[0])
        self.assertEqual(["location expected 'Farm' but observed 'FarmHouse'"], result[1])

    def test_director_cannot_set_an_unverifiable_success_condition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ObjectiveLedger(Path(directory) / "objectives.json", "Start", "location is Farm")
            ledger.complete_objective("Reached farm")
            with self.assertRaises(ObjectiveError):
                ledger.set_objective("Clear debris", "Enough debris has been cleared", "Use the axe")

    def test_numeric_success_condition_supports_time_ranges(self) -> None:
        before = evaluate_state_condition("time >= 900, day is 7", {"time": 850, "day": 7})
        after = evaluate_state_condition("time >= 900, day is 7", {"time": 900, "day": 7})
        self.assertFalse(before[0])
        self.assertTrue(after[0])

    def test_inventory_and_crop_counters_are_verifiable(self) -> None:
        state = {"inventoryCounts": {"Parsnip Seeds": 20, "Fiber": 4}, "plantedCrops": 0, "wateredCrops": 0}
        seeds = evaluate_state_condition("inventory.Parsnip Seeds >= 20, plantedCrops >= 15", state)
        missing = evaluate_state_condition("inventory.Parsnip >= 1", state)
        unknown = evaluate_state_condition("shopItems.Parsnip >= 1", state)

        self.assertFalse(seeds[0])
        self.assertEqual(["plantedCrops expected >= 15 but observed 0"], seeds[1])
        self.assertEqual(["inventoryCounts.Parsnip expected >= 1 but observed 0"], missing[1])
        self.assertEqual(["shopItems.Parsnip is unavailable"], unknown[1])

    def test_none_matches_literal_menu_state_but_null_matches_missing_value(self) -> None:
        menu = evaluate_state_condition("menu is none", {"menu": "none"})
        cursor = evaluate_state_condition("cursorItem is null", {"cursorItem": None})

        self.assertTrue(menu[0])
        self.assertTrue(cursor[0])


if __name__ == "__main__":
    unittest.main()
