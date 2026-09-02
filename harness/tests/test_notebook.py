import tempfile
import unittest
from pathlib import Path

from autoplay_harness.notebook import Notebook


def agenda(count=5):
    slots = ["morning", "midday", "afternoon", "evening", "evening"]
    return [
        {
            "goal": f"Goal {index + 1}",
            "success_condition": f"money >= {index + 1}",
            "slot": slots[index],
        }
        for index in range(count)
    ]


class NotebookTests(unittest.TestCase):
    def test_start_day_pulls_pending_and_active_items_as_carried_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.start_day(16)
            notebook.set_agenda(16, agenda(), "Settle in")
            first, second, third = notebook.data["days"]["16"]["agenda"][:3]
            notebook.mark(first["id"], "done")
            notebook.mark(second["id"], "active")
            notebook.mark(third["id"], "dropped", "Not useful")

            day = notebook.start_day(17)

            self.assertEqual(3, len(day["agenda"]))
            self.assertTrue(all(item["status"] == "carried" for item in day["agenda"]))
            self.assertTrue(all(item["carried_from"] == "16" for item in day["agenda"]))
            self.assertEqual("done", notebook.data["days"]["16"]["agenda"][0]["status"])
            self.assertEqual("carried", notebook.data["days"]["16"]["agenda"][1]["status"])

    def test_set_agenda_validates_shape_and_records_kept_and_dropped_carryover(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.start_day(1)
            notebook.set_agenda(1, agenda(), "First day")
            notebook.start_day(2)
            carried = notebook.remaining(2)
            items = agenda()
            items[0]["carried_id"] = carried[0]["id"]
            dropped = [{"id": item["id"], "reason": "Deferred"} for item in carried[1:]]

            notebook.set_agenda(2, items, "Second day", dropped)

            current = notebook.data["days"]["2"]["agenda"]
            self.assertEqual("1", current[0]["carried_from"])
            self.assertEqual(4, len([item for item in current if item["status"] == "dropped"]))
            with self.assertRaisesRegex(ValueError, "invalid agenda slot"):
                notebook.set_agenda(2, [{"goal": "Bad", "slot": "night"}], "Bad")
            with self.assertRaisesRegex(ValueError, "reason"):
                notebook.set_agenda(2, agenda(), "Bad", [{"id": "d1-1", "reason": ""}])

    def test_mark_active_done_and_remaining(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.start_day(3)
            notebook.set_agenda(3, agenda(), "Work")
            item_id = notebook.remaining(3)[0]["id"]

            notebook.mark(item_id, "active", "Started")
            self.assertEqual(item_id, notebook.active_item(3)["id"])
            notebook.mark(item_id, "done", "Verified")

            self.assertIsNone(notebook.active_item(3))
            self.assertEqual(4, len(notebook.remaining(3)))

    def test_reflect_persists_day_and_bounds_global_learned_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.start_day(4)
            notebook.reflect(4, "A useful day", [f"Fact {index}" for index in range(45)])

            restored = Notebook(Path(directory))

            self.assertEqual("A useful day", restored.data["days"]["4"]["reflection"])
            self.assertEqual(45, len(restored.data["days"]["4"]["learned"]))
            self.assertEqual(40, len(restored.data["learned"]))
            self.assertEqual("Fact 44", restored.context(4)["learned"][-1])

    def test_farm_plan_persistence_round_trip_and_in_zone(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            zones = [
                {"name": "Kitchen plot", "purpose": "crops", "x1": 5, "y1": 6, "x2": 9, "y2": 10},
                {"name": "Orchard", "purpose": "trees", "x1": 20, "y1": 4, "x2": 25, "y2": 12},
            ]
            notebook.set_farm_plan(zones, "Keep the path open.", 5)

            restored = Notebook(Path(directory))

            self.assertEqual(zones, restored.farm_plan["zones"])
            self.assertTrue(restored.in_zone(7, 8, "crops"))
            self.assertFalse(restored.in_zone(20, 8, "crops"))
            self.assertEqual("Keep the path open.", restored.context(5)["farmPlan"]["notes"])


if __name__ == "__main__":
    unittest.main()
