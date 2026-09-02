import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from autoplay_harness.capture import Frame
from autoplay_harness.openrouter import ToolDecision
from autoplay_harness.runner import AutoplayHarness


class AsyncDirectorTests(unittest.TestCase):
    def test_actor_can_continue_and_late_review_cannot_replace_current_plan(self):
        for change in (None, "location", "crop", "objective", "complete", "block"):
            with self.subTest(change=change), ThreadPoolExecutor(max_workers=1) as executor:
                harness = object.__new__(AutoplayHarness)
                harness.director_executor = executor
                harness.director_future = None
                harness.game_actions = 0
                harness.ledger = Mock()
                active = {"id": "one", "success_condition": "plantedCrops >= 5", "milestone": "Plant seeds"}
                harness.ledger.snapshot.side_effect = lambda: {"active": active}
                harness.telemetry = Mock(step=0)
                harness._apply_director_decision = Mock()
                state = {"worldReady": True, "location": "Farm", "day": 10, "stamina": 100, "plantedCrops": 0}
                frame = Frame("frame", 1920, 1080, "image", None)
                harness._observe = lambda: (dict(state), frame)
                harness._context = lambda *args: "context"
                started, release = threading.Event(), threading.Event()

                def review(*args):
                    started.set()
                    release.wait(5)
                    return ToolDecision("block_objective" if change == "block" else "continue_objective",
                                        {"milestone": "Plant five", "reason": "Goal"}, {}, "model"), 100

                harness._choose = review
                harness._start_director_review()
                self.assertTrue(started.wait(1))
                # Polling an unfinished review returns while the actor remains free to act.
                harness._finish_director_review()
                self.assertFalse(harness.director_future.done())
                harness.game_actions = 4
                if change == "location":
                    state["location"] = "BusStop"
                elif change == "crop":
                    state["plantedCrops"] = 1
                elif change == "objective":
                    active["id"] = "two"
                elif change == "complete":
                    active = None
                release.set()
                harness._finish_director_review(wait=True)
                self.assertEqual(change is None, harness._apply_director_decision.called)
                self.assertIsNone(harness.director_future)
