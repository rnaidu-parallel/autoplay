import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import autoplay_harness.forever as forever_module
from autoplay_harness.forever import run_forever


class _Supervisor:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop_started_game(self) -> None:
        self.stop_calls += 1


class _Harness:
    def __init__(self, run_id: str, stop_reason: str, cost: float = 0, decisions: int = 0) -> None:
        self.run_id = run_id
        self.stop_reason = stop_reason
        self.budget_usd = None
        self.telemetry = type("Telemetry", (), {"summary": {"usage": {"cost": cost, "calls": decisions}}})()
        self.supervisor = _Supervisor()

    def run(self) -> str:
        return self.stop_reason


class ForeverTests(unittest.TestCase):
    def test_restarts_stalled_run_and_removes_overlay_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harnesses = [_Harness("run-1", "stalled"), _Harness("run-2", "stop_requested")]

            result = run_forever(
                lambda: harnesses.pop(0),
                restart_delay_seconds=0,
                stop_file=root / "harness" / "state" / "STOP",
                sleep=lambda _seconds: None,
            )

            self.assertEqual("stop_requested", result)
            self.assertEqual([], harnesses)
            self.assertFalse((root / "harness" / "runs" / "run-1" / "overlay" / "restarting").exists())

    def test_budget_reached_without_daily_budget_does_not_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def build():
                calls.append(True)
                return _Harness("run-1", "budget_reached")

            result = run_forever(build, stop_file=Path(directory) / "harness" / "state" / "STOP")

            self.assertEqual("budget_reached", result)
            self.assertEqual(1, len(calls))

    def test_stop_file_is_honored_before_building(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "harness" / "state" / "STOP"
            stop_file.parent.mkdir(parents=True)
            stop_file.touch()

            result = run_forever(
                lambda: self.fail("the harness must not be built"),
                stop_file=stop_file,
            )

            self.assertEqual("stop_requested", result)
            status = json.loads((stop_file.parent / "forever_status.json").read_text(encoding="utf-8"))
            self.assertEqual("stopped", status["state"])

    def test_cost_ledger_accumulates_runs_and_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "harness" / "state"
            harnesses = [
                _Harness("run-1", "stalled", cost=0.25, decisions=2),
                _Harness("run-2", "stop_requested", cost=0.5, decisions=3),
            ]

            run_forever(
                lambda: harnesses.pop(0),
                restart_delay_seconds=0,
                stop_file=state / "STOP",
                sleep=lambda _seconds: None,
            )

            ledger = json.loads((state / "cost_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(["run-1", "run-2"], ledger["runs"])
            self.assertEqual(5, ledger["decisions"])
            self.assertAlmostEqual(0.75, ledger["cost_usd"])

    def test_cost_ledger_resets_on_local_date_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "harness" / "state"
            state.mkdir(parents=True)
            now = datetime(2026, 9, 3, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))
            (state / "cost_ledger.json").write_text(
                json.dumps({"date": "2026-09-02", "cost_usd": 9, "decisions": 99, "runs": ["old"]}),
                encoding="utf-8",
            )

            run_forever(
                lambda: _Harness("new", "stop_requested", cost=0.1, decisions=1),
                stop_file=state / "STOP",
                clock=lambda: now,
            )

            ledger = json.loads((state / "cost_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("2026-09-03", ledger["date"])
            self.assertEqual(["new"], ledger["runs"])
            self.assertEqual(1, ledger["decisions"])
            self.assertAlmostEqual(0.1, ledger["cost_usd"])

    def test_status_records_restart_then_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "harness" / "state"
            harnesses = [_Harness("run-1", "stalled"), _Harness("run-2", "stop_requested")]
            transitions = []
            write_json_atomic = forever_module._write_json_atomic

            def capture_status(path, value):
                if path.name == "forever_status.json":
                    transitions.append(value["state"])
                write_json_atomic(path, value)

            with patch("autoplay_harness.forever._write_json_atomic", side_effect=capture_status):
                run_forever(
                    lambda: harnesses.pop(0),
                    restart_delay_seconds=0,
                    stop_file=state / "STOP",
                    sleep=lambda _seconds: None,
                )

            status = json.loads((state / "forever_status.json").read_text(encoding="utf-8"))
            self.assertIn("running", transitions)
            self.assertIn("restarting", transitions)
            self.assertEqual("stopped", transitions[-1])
            self.assertEqual("stopped", status["state"])
            self.assertEqual("run-2", status["runId"])
            self.assertEqual(1, status["restarts"])
            self.assertEqual("stop_requested", status["lastStopReason"])

    def test_restart_rate_limit_sleeps_ten_minutes_and_never_gives_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harnesses = [
                _Harness("run-1", "stalled"),
                _Harness("run-2", "recovery_exhausted:BridgeError"),
                _Harness("run-3", "stop_requested"),
            ]
            sleeps = []

            result = run_forever(
                lambda: harnesses.pop(0),
                restart_delay_seconds=3,
                max_restarts_per_hour=1,
                stop_file=Path(directory) / "harness" / "state" / "STOP",
                sleep=sleeps.append,
            )

            self.assertEqual("stop_requested", result)
            self.assertEqual([3, 600, 3], sleeps)

    def test_daily_budget_is_passed_as_remaining_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "harness" / "state"
            state.mkdir(parents=True)
            now = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
            (state / "cost_ledger.json").write_text(
                json.dumps({"date": "2026-09-03", "cost_usd": 0.4, "decisions": 2, "runs": ["old"]}),
                encoding="utf-8",
            )
            harness = _Harness("run-1", "stop_requested")

            run_forever(
                lambda: harness,
                daily_budget_usd=1.0,
                stop_file=state / "STOP",
                clock=lambda: now,
            )

            self.assertAlmostEqual(0.6, harness.budget_usd)

    def test_daily_budget_sleeps_until_midnight_then_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = [datetime(2026, 9, 3, 23, 59, tzinfo=timezone(timedelta(hours=5, minutes=30)))]
            harnesses = [
                _Harness("run-1", "budget_reached", cost=1, decisions=1),
                _Harness("run-2", "stop_requested"),
            ]
            sleeps = []

            def sleep(seconds):
                sleeps.append(seconds)
                current[0] += timedelta(seconds=seconds)

            result = run_forever(
                lambda: harnesses.pop(0),
                daily_budget_usd=1,
                stop_file=Path(directory) / "harness" / "state" / "STOP",
                clock=lambda: current[0],
                sleep=sleep,
            )

            self.assertEqual("stop_requested", result)
            self.assertEqual([60], sleeps)
            self.assertEqual([], harnesses)


if __name__ == "__main__":
    unittest.main()
