import json
import tempfile
import unittest
from pathlib import Path

from autoplay_harness.session import UNKNOWN_RESULT, DaySession, _tokens


def _write_events(directory: Path, name: str, lines: list[str]) -> None:
    run_directory = directory / name
    run_directory.mkdir(parents=True)
    (run_directory / "events.jsonl").write_text("\n".join(lines), encoding="utf-8")


class DaySessionMessagesTests(unittest.TestCase):
    def test_renders_openai_shapes_for_a_normal_step(self) -> None:
        session = DaySession("save:1")
        session.add_observation(1, "It is 6:10 AM on the farm.", "Spring 1 6:10 AM Farm", "data:image/png;base64,AAA")
        session.add_decision(2, "call-1", "press", {"buttons": ["W"]}, "Heading for the door.")
        session.add_result(3, "call-1", {"status": "completed", "state": {"location": "Farm"}})

        messages = session.messages()

        self.assertEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "It is 6:10 AM on the farm."},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                    ],
                },
                {
                    "role": "assistant",
                    "content": "Heading for the door.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "press", "arguments": json.dumps({"buttons": ["W"]})},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": json.dumps({"status": "completed", "state": {"location": "Farm"}}),
                },
            ],
            messages,
        )
        self.assertEqual(1, session.bundle_count())

    def test_older_observations_become_summary_lines_without_images(self) -> None:
        session = DaySession("save:1")
        session.add_observation(1, "Full first observation.", "Spring 1 6:10 AM Farm", "data:image/png;base64,AAA")
        session.add_decision(2, "call-1", "press", {"buttons": ["W"]})
        session.add_result(3, "call-1", {"status": "completed"})
        session.add_observation(4, "Full second observation.", "Spring 1 6:20 AM Farm", "data:image/png;base64,BBB")

        messages = session.messages()

        self.assertEqual([{"type": "text", "text": "Spring 1 6:10 AM Farm"}], messages[0]["content"])
        self.assertEqual(
            [
                {"type": "text", "text": "Full second observation."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
            ],
            messages[-1]["content"],
        )
        self.assertNotIn("AAA", json.dumps(messages))

    def test_results_beyond_the_full_result_window_are_reduced(self) -> None:
        session = DaySession("save:1", full_result_bundles=1)
        session.add_decision(1, "call-1", "press", {"buttons": ["W"]})
        session.add_result(2, "call-1", {"status": "completed", "state": {"location": "Farm"}})
        session.add_decision(3, "call-2", "travel_to", {"destination": "Town"})
        session.add_result(4, "call-2", {"status": "error", "error": "blocked", "state": {"location": "Farm"}})
        session.add_decision(5, "call-3", "wait", {"seconds": 1})
        session.add_result(6, "call-3", {"status": "completed", "state": {"location": "Farm"}})

        contents = [json.loads(message["content"]) for message in session.messages() if message["role"] == "tool"]

        self.assertEqual({"status": "completed"}, contents[0])
        self.assertEqual({"status": "error", "reason": "blocked"}, contents[1])
        self.assertEqual({"status": "completed", "state": {"location": "Farm"}}, contents[2])

    def test_consecutive_user_turns_merge_into_one_message(self) -> None:
        session = DaySession("save:1")
        session.add_observation(1, "Standing by the door.", "Spring 1 6:10 AM Farm")
        session.add_note(2, '(While you stood there: Robin said "Morning!")')
        session.add_note(3, "(The harness restarted.)")
        session.add_decision(4, "call-1", "wait", {"seconds": 1})
        session.add_result(5, "call-1", {"status": "completed"})

        messages = session.messages()

        self.assertEqual("user", messages[0]["role"])
        self.assertEqual(3, len(messages))
        self.assertEqual(
            [
                {"type": "text", "text": "Standing by the door."},
                {"type": "text", "text": '(While you stood there: Robin said "Morning!")'},
                {"type": "text", "text": "(The harness restarted.)"},
            ],
            messages[0]["content"],
        )

    def test_decision_without_a_result_gets_the_synthetic_unknown_result(self) -> None:
        session = DaySession("save:1")
        session.add_observation(1, "Standing by the door.", "Spring 1 6:10 AM Farm")
        session.add_decision(2, "call-1", "press", {"buttons": ["W"]})

        messages = session.messages()

        self.assertEqual("tool", messages[-1]["role"])
        self.assertEqual("call-1", messages[-1]["tool_call_id"])
        self.assertEqual(UNKNOWN_RESULT, json.loads(messages[-1]["content"]))

    def test_notes_are_never_pruned_to_a_summary(self) -> None:
        session = DaySession("save:1")
        session.add_note(1, "(The harness restarted mid-day.)")
        session.add_decision(2, "call-1", "wait", {"seconds": 1})
        session.add_result(3, "call-1", {"status": "completed"})
        session.add_observation(4, "Standing by the door.", "Spring 1 6:20 AM Farm")

        messages = session.messages()

        self.assertEqual([{"type": "text", "text": "(The harness restarted mid-day.)"}], messages[0]["content"])

    def test_estimated_tokens_counts_text_and_images(self) -> None:
        session = DaySession("save:1")
        session.add_observation(1, "x" * 400, "s" * 400, "data:image/png;base64,AAA")

        self.assertEqual(100 + 1000, session.estimated_tokens())

    def test_budget_drops_oldest_bundles_but_keeps_the_newest_observation(self) -> None:
        session = DaySession("save:1", max_tokens=100)
        session.add_observation(0, "F" * 40, "f" * 40)
        for index in range(1, 5):
            call_id = "call-%d" % index
            session.add_decision(index, call_id, "press", {"buttons": ["W"]})
            session.add_result(index, call_id, {"status": "completed"})
            session.add_observation(index, "T%d" % index + "T" * 198, "S%d" % index + "S" * 198)

        messages = session.messages()
        rendered = json.dumps(messages)

        self.assertLessEqual(_tokens(messages), session.max_tokens)
        self.assertNotIn("call-1", rendered)
        self.assertNotIn("call-3", rendered)
        self.assertIn("call-4", rendered)
        self.assertIn("T4", rendered)
        self.assertEqual([{"type": "text", "text": "f" * 40}], messages[0]["content"])


class DaySessionRebuildTests(unittest.TestCase):
    def test_rebuilds_from_two_runs_with_duplicates_and_a_partial_line(self) -> None:
        observation = {
            "at": "2026-09-07T00:00:01+00:00",
            "seq": 1,
            "type": "agent_observation",
            "session_id": "save:1",
            "text": "It is 6:10 AM on the farm.",
            "summary": "Spring 1 6:10 AM Farm",
            "frame_id": "frame-1",
        }
        run_a = [
            json.dumps(observation),
            json.dumps(observation),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:03+00:00",
                    "seq": 3,
                    "type": "agent_decision",
                    "session_id": "save:1",
                    "call_id": "call-1",
                    "tool": "press",
                    "arguments": {"buttons": ["W"]},
                    "content": "Heading out.",
                }
            ),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:09+00:00",
                    "seq": 9,
                    "type": "agent_observation",
                    "session_id": "save:2",
                    "text": "other session",
                    "summary": "other session",
                }
            ),
        ]
        run_b = [
            json.dumps(
                {
                    "at": "2026-09-07T00:00:02+00:00",
                    "seq": 2,
                    "type": "agent_note",
                    "session_id": "save:1",
                    "text": '(While you stood there: Robin said "Morning!")',
                }
            ),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:04+00:00",
                    "seq": 4,
                    "type": "action_started",
                    "session_id": "save:1",
                    "call_id": "call-1",
                }
            ),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:05+00:00",
                    "seq": 5,
                    "type": "tool_result",
                    "session_id": "save:1",
                    "result": {"status": "completed"},
                }
            ),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:06+00:00",
                    "seq": 6,
                    "type": "agent_decision",
                    "session_id": "save:1",
                    "call_id": "call-2",
                    "tool": "wait",
                    "arguments": {"seconds": 1},
                }
            ),
            json.dumps(
                {
                    "at": "2026-09-07T00:00:07+00:00",
                    "seq": 7,
                    "type": "tool_result",
                    "session_id": "save:1",
                    "call_id": "call-2",
                    "result": {"status": "completed", "state": {"location": "Farm"}},
                }
            ),
            '{"at": "2026-09-07T00:00:08+00:00", "seq": 8, "type": "agent_no',
        ]
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            _write_events(runs, "run-a", run_a)
            _write_events(runs, "run-b", run_b)

            session = DaySession.rebuild(runs, "save:1")

        messages = session.messages()
        rendered = json.dumps(messages)

        self.assertEqual(2, session.bundle_count())
        self.assertEqual(["call-1"], session.unresolved_call_ids)
        self.assertNotIn("other session", rendered)
        self.assertEqual(
            [
                {"type": "text", "text": "It is 6:10 AM on the farm."},
                {"type": "text", "text": '(While you stood there: Robin said "Morning!")'},
            ],
            messages[0]["content"],
        )
        self.assertEqual("Heading out.", messages[1]["content"])
        self.assertEqual(UNKNOWN_RESULT, json.loads(messages[2]["content"]))
        self.assertEqual(
            {"status": "completed"}, json.loads(messages[4]["content"])  # the state snapshot never enters the transcript
        )


if __name__ == "__main__":
    unittest.main()
