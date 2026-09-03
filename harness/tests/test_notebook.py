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
            "category": ("farming", "clearing", "exploring", "social", "shopping")[index % 5],
        }
        for index in range(count)
    ]


class NotebookTests(unittest.TestCase):
    def test_deferred_items_survive_refill_and_return_as_carried_next_day(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.start_day(1)
            notebook.set_agenda(1, agenda(1), "First plan")
            notebook.mark("d1-1", "deferred", "Retry limit")
            self.assertEqual([], notebook.remaining(1))
            notebook.set_agenda(1, agenda(1), "Refill")
            notebook = Notebook(Path(directory))
            self.assertEqual("deferred", notebook.data["days"]["1"]["agenda"][0]["status"])
            self.assertEqual(["d1-2"], [item["id"] for item in notebook.remaining(1)])
            notebook.start_day(2)
            self.assertEqual("carried", notebook.remaining(2)[0]["status"])
            self.assertEqual("Retry limit", notebook.remaining(2)[0]["note"])

    def test_category_mix_recent_themes_variety_and_actor_context(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            for day, theme in ((1, "Farm"), (2, "Town"), (3, "Mine")):
                notebook.start_day(day)
                notebook.set_agenda(day, agenda(), theme)
            self.assertEqual(1, notebook.category_mix(3)["farming"])
            self.assertEqual(["Farm", "Town", "Mine"], notebook.recent_themes(3))
            errors = notebook.variety_errors(3, agenda(), " mine ", "morning")
            self.assertNotIn("choose a theme different from each of the last 3 days", errors)
            actor = notebook.context(3, "actor")
            self.assertEqual(4, len(actor["today"]["agenda"][0].split("|")))
            self.assertNotIn("yesterday", actor)

    def test_variety_uses_three_prior_days_and_refill_limit_only(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            for day, theme in ((1, "Farm"), (2, "Town"), (3, "Mine"), (4, "")):
                notebook.start_day(day)
                if theme:
                    items = agenda()
                    if day == 3:
                        for item in items[:3]:
                            item["category"] = "farming"
                    notebook.set_agenda(day, items, theme)
            morning = [
                {"goal": "Farm one", "slot": "morning", "category": "farming"},
                {"goal": "Farm two", "slot": "midday", "category": "farming"},
                {"goal": "Farm three", "slot": "afternoon", "category": "farming"},
                {"goal": "Fish", "slot": "evening", "category": "fishing"},
                {"goal": "Mine", "slot": "evening", "category": "mining"},
            ]
            self.assertIn("choose a theme different from each of the last 3 days", notebook.variety_errors(4, morning, "farm", "morning"))
            self.assertNotIn("keep yesterday's dominant farming category to 1 refill item", notebook.variety_errors(4, morning, "new", "morning"))
            self.assertIn("keep yesterday's dominant farming category to 1 refill item", notebook.variety_errors(4, morning, "new", "refill"))

    def test_lessons_merge_cap_and_rollup_week(self):
        with tempfile.TemporaryDirectory() as directory:
            notebook = Notebook(Path(directory))
            notebook.add_lesson("blocked", "Path was blocked.", "auto", 1)
            notebook.add_lesson("blocked", "Path was blocked.", "auto", 2)
            self.assertEqual(["Path was blocked."], notebook.top_lessons(1))
            self.assertEqual(2, notebook.data["lessons"][0]["count"])
            for index in range(40):
                notebook.add_lesson(f"lesson-{index}", f"Lesson {index}", "auto", index + 3)
            self.assertEqual(40, len(notebook.data["lessons"]))
            for day in range(1, 9):
                notebook.start_day(day)
                notebook.set_agenda(day, agenda(), f"Theme {day}")
                notebook.reflect(day, f"Reflection {day}", [])
            notebook.rollup_week()
            self.assertEqual([1, 7], notebook.data["weeks"][-1]["days"])
            self.assertIn("8", notebook.data["days"])
            self.assertNotIn("1", notebook.data["days"])
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
                notebook.set_agenda(2, [{"goal": "Bad", "slot": "night", "category": "home"}], "Bad")
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
