import json
import tempfile
import unittest
from pathlib import Path

from autoplay_harness.diary import Diary, game_time


class GameTimeTests(unittest.TestCase):
    def test_formats_morning_afternoon_and_after_midnight_rollover(self):
        self.assertEqual(game_time(940), "9:40 AM")
        self.assertEqual(game_time(1330), "1:30 PM")
        self.assertEqual(game_time(2450), "12:50 AM")
        self.assertEqual(game_time(None), "—")


class DiaryWriteTests(unittest.TestCase):
    def test_write_round_trip_and_file_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            diary = Diary(state_directory)
            returned = diary.write(3, 940, "Farm", "Watered the crops.")

            path = state_directory / "diary" / "3.jsonl"
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            stored = json.loads(lines[0])

            self.assertEqual(stored["day"], 3)
            self.assertEqual(stored["time"], 940)
            self.assertEqual(stored["location"], "Farm")
            self.assertEqual(stored["text"], "Watered the crops.")
            self.assertEqual(stored["kind"], "entry")
            self.assertIn("at", stored)
            self.assertEqual(returned, stored)
            self.assertEqual(diary.entries(3), [stored])

    def test_folder_created_lazily(self):
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            diary = Diary(state_directory)
            self.assertFalse((state_directory / "diary").exists())
            diary.write(1, 600, "Farm", "Woke up.")
            self.assertTrue((state_directory / "diary").exists())

    def test_invalid_kind_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            with self.assertRaises(ValueError):
                diary.write(1, 600, "Farm", "Text", kind="bogus")

    def test_empty_text_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            with self.assertRaises(ValueError):
                diary.write(1, 600, "Farm", "   ")

    def test_valid_kinds_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            for kind in ("entry", "note", "bedtime"):
                diary.write(1, 600, "Farm", f"A {kind}.", kind=kind)
            self.assertEqual([entry["kind"] for entry in diary.entries(1)], ["entry", "note", "bedtime"])


class DiaryDaysTests(unittest.TestCase):
    def test_days_are_sorted_ascending_regardless_of_write_order(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(5, 600, "Farm", "Day five.")
            diary.write(2, 600, "Farm", "Day two.")
            diary.write(10, 600, "Farm", "Day ten.")
            self.assertEqual(diary.days(), [2, 5, 10])

    def test_days_empty_when_nothing_written(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            self.assertEqual(diary.days(), [])


class DiaryLastEntryTests(unittest.TestCase):
    def test_last_entry_prefers_bedtime_over_later_written_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 800, "Farm", "Morning chores.")
            diary.write(1, 2200, "Farmhouse", "Going to sleep.", kind="bedtime")
            diary.write(1, 2201, "Farmhouse", "One more note.", kind="note")
            entry = diary.last_entry(before_day=2)
            self.assertEqual(entry["kind"], "bedtime")
            self.assertEqual(entry["text"], "Going to sleep.")

    def test_last_entry_falls_back_to_final_entry_without_bedtime(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 800, "Farm", "First.")
            diary.write(1, 900, "Farm", "Second.")
            entry = diary.last_entry(before_day=2)
            self.assertEqual(entry["text"], "Second.")

    def test_last_entry_uses_most_recent_day_strictly_before(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 800, "Farm", "Day one.")
            diary.write(3, 800, "Farm", "Day three.")
            entry = diary.last_entry(before_day=5)
            self.assertEqual(entry["day"], 3)

    def test_last_entry_none_when_no_earlier_day(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(5, 800, "Farm", "Only day.")
            self.assertIsNone(diary.last_entry(before_day=5))
            self.assertIsNone(diary.last_entry(before_day=1))


class DiarySearchTests(unittest.TestCase):
    def _seed(self, diary):
        diary.write(1, 900, "Farm", "Watered the parsnips near the coop.")  # too old: before today-days
        diary.write(2, 900, "SeedShop", "Bought parsnip seeds from Pierre and chatted about crops.")
        diary.write(2, 1600, "Farm", "Chatted with Pierre about parsnip crops again.")
        diary.write(8, 900, "Farm", "Unrelated errands, no matching words.")  # in-window, zero overlap
        diary.write(9, 900, "Farm", "Today's entry about parsnip crops, must be excluded.")  # today itself

    def test_search_ranks_by_token_overlap_and_recency(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            self._seed(diary)
            results = diary.search("parsnip crops Pierre", today=9, days=7)
            self.assertGreaterEqual(len(results), 2)
            self.assertEqual(results[0]["day"], 2)
            self.assertEqual(results[0]["time"], 1600)
            self.assertEqual(results[1]["day"], 2)
            self.assertEqual(results[1]["time"], 900)

    def test_search_excludes_today_and_out_of_window_days(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            self._seed(diary)
            results = diary.search("parsnip crops Pierre", today=9, days=7)
            days_found = {result["day"] for result in results}
            self.assertNotIn(9, days_found)
            self.assertNotIn(8, days_found)

    def test_search_drops_zero_overlap_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 900, "Farm", "Nothing relevant here at all.")
            results = diary.search("parsnip", today=5, days=7)
            self.assertEqual(results, [])

    def test_search_respects_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            for day in range(1, 6):
                diary.write(day, 900, "Farm", "parsnip parsnip parsnip")
            results = diary.search("parsnip", today=6, days=7, limit=2)
            self.assertEqual(len(results), 2)

    def test_search_stopwords_and_short_tokens_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 900, "Farm", "The and for are common words with no meaning here.")
            results = diary.search("the and for are", today=5, days=7)
            self.assertEqual(results, [])

    def test_excerpt_windows_with_ellipses_on_both_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            padding = "x" * 200
            text = f"{padding} parsnip {padding}"
            diary.write(1, 900, "Farm", text)
            results = diary.search("parsnip", today=5, days=7, excerpt_chars=40)
            excerpt = results[0]["excerpt"]
            self.assertTrue(excerpt.startswith("…"))
            self.assertTrue(excerpt.endswith("…"))
            self.assertIn("parsnip", excerpt)
            self.assertLessEqual(len(excerpt), 42)

    def test_excerpt_no_ellipsis_when_match_near_start(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            text = "parsnip seeds are cheap this week"
            diary.write(1, 900, "Farm", text)
            results = diary.search("parsnip", today=5, days=7, excerpt_chars=240)
            excerpt = results[0]["excerpt"]
            self.assertFalse(excerpt.startswith("…"))
            self.assertFalse(excerpt.endswith("…"))
            self.assertEqual(excerpt, text)


class DiaryReadTests(unittest.TestCase):
    def test_read_formats_lines_with_time_location_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 940, "Farm", "Watered crops.")
            diary.write(1, 1330, "Town", "Sold some eggs.")
            diary.write(1, None, None, "A stray thought.")
            expected = (
                "9:40 AM · Farm · Watered crops.\n"
                "1:30 PM · Town · Sold some eggs.\n"
                "— · — · A stray thought."
            )
            self.assertEqual(diary.read(1), expected)

    def test_read_returns_empty_string_for_missing_day(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            self.assertEqual(diary.read(1), "")

    def test_read_truncates_with_ellipsis(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 600, "Farm", "x" * 100)
            result = diary.read(1, max_chars=20)
            self.assertTrue(result.endswith("…"))
            self.assertEqual(len(result), 21)


class DiaryExcerptsTests(unittest.TestCase):
    def test_excerpts_splits_camel_case_location_and_uses_names(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(2, 900, "SeedShop", "Chatted with Pierre near the Seed Shop counter.")
            results = diary.excerpts(today=9, location="SeedShop", names=["Pierre"], objective_words=[])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["day"], 2)

    def test_excerpts_uses_objective_words_and_respects_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 900, "Farm", "Started clearing debris for the greenhouse project.")
            diary.write(2, 900, "Farm", "More greenhouse debris clearing today.")
            diary.write(3, 900, "Farm", "Finished the greenhouse debris clearing project.")
            results = diary.excerpts(today=9, location=None, names=[], objective_words=["greenhouse", "debris"], limit=2)
            self.assertEqual(len(results), 2)

    def test_excerpts_outside_fourteen_day_window_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            diary = Diary(Path(directory))
            diary.write(1, 900, "Farm", "greenhouse debris clearing")
            results = diary.excerpts(today=20, location=None, names=[], objective_words=["greenhouse", "debris"])
            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
