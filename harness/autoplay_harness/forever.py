from __future__ import annotations

import json
import time as time_module
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable


def run_forever(
    build_harness: Callable[[], Any],
    *,
    restart_delay_seconds: int = 15,
    max_restarts_per_hour: int = 12,
    stop_file: Path = Path("harness/state/STOP"),
    daily_budget_usd: float | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    sleep: Callable[[float], None] = time_module.sleep,
) -> str:
    state_directory = stop_file.parent
    runs_directory = state_directory.parent / "runs"
    ledger_path = state_directory / "cost_ledger.json"
    status_path = state_directory / "forever_status.json"
    log_path = state_directory / "forever.log"
    restarts = 0
    restart_times: list[datetime] = []
    last_stop_reason: str | None = None
    run_id: str | None = None

    state_directory.mkdir(parents=True, exist_ok=True)

    def write_status(state: str) -> None:
        _write_json_atomic(
            status_path,
            {
                "state": state,
                "runId": run_id,
                "restarts": restarts,
                "lastStopReason": last_stop_reason,
                "updatedAt": clock().astimezone().isoformat(),
            },
        )

    def log_traceback() -> None:
        try:
            with log_path.open("a", encoding="utf-8") as output:
                output.write(f"{clock().astimezone().isoformat()} run_id={run_id or 'unavailable'}\n")
                output.write(traceback.format_exc())
                output.write("\n")
        except OSError:
            pass

    while True:
        now = clock().astimezone()
        ledger = _load_ledger(ledger_path, now)
        _write_json_atomic(ledger_path, ledger)

        if stop_file.exists():
            last_stop_reason = "stop_requested"
            write_status("stopped")
            return last_stop_reason

        if daily_budget_usd is not None and float(ledger["cost_usd"]) >= daily_budget_usd:
            last_stop_reason = "budget_reached"
            write_status("sleeping_budget")
            sleep(_seconds_until_midnight(now))
            continue

        harness = None
        run_id = None
        write_status("running")
        try:
            harness = build_harness()
            run_id = harness.run_id
            if daily_budget_usd is not None:
                harness.budget_usd = max(0.0, daily_budget_usd - float(ledger["cost_usd"]))
            write_status("running")
            last_stop_reason = harness.run()
        except Exception as error:
            last_stop_reason = f"exception:{type(error).__name__}"
            log_traceback()

        if harness is not None:
            ledger = _load_ledger(ledger_path, clock().astimezone())
            usage = harness.telemetry.summary.get("usage", {})
            ledger["cost_usd"] += float(usage.get("cost") or 0)
            ledger["decisions"] += int(usage.get("calls") or 0)
            if run_id is not None and run_id not in ledger["runs"]:
                ledger["runs"].append(run_id)
            _write_json_atomic(ledger_path, ledger)

        if harness is not None and getattr(harness, "operator_mode", "playing") in {"saved", "held", "needs_attention"}:
            write_status("stopped")
            return last_stop_reason

        if stop_file.exists():
            last_stop_reason = "stop_requested"
            write_status("stopped")
            return last_stop_reason

        if last_stop_reason == "budget_reached":
            if daily_budget_usd is None:
                print("Daily supervision is disabled; stopping because the run budget was reached.", flush=True)
                write_status("stopped")
                return last_stop_reason
            write_status("sleeping_budget")
            sleep(_seconds_until_midnight(clock().astimezone()))
            continue

        if last_stop_reason == "stop_requested":
            write_status("stopped")
            return last_stop_reason

        restarts += 1
        restart_now = clock().astimezone()
        restart_times = [item for item in restart_times if restart_now - item < timedelta(hours=1)]
        restart_times.append(restart_now)
        write_status("restarting")
        restarting_marker = runs_directory / run_id / "overlay" / "restarting" if run_id else None
        if restarting_marker is not None:
            restarting_marker.parent.mkdir(parents=True, exist_ok=True)
            restarting_marker.touch()
        try:
            if harness is not None:
                try:
                    harness.supervisor.stop_started_game()
                except Exception:
                    log_traceback()
            if len(restart_times) > max_restarts_per_hour:
                sleep(600)
            sleep(restart_delay_seconds)
        finally:
            if restarting_marker is not None:
                restarting_marker.unlink(missing_ok=True)


def _load_ledger(path: Path, now: datetime) -> dict[str, Any]:
    today = now.date().isoformat()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return {
                    "date": today,
                    "cost_usd": float(data.get("cost_usd") or 0),
                    "decisions": int(data.get("decisions") or 0),
                    "runs": list(data.get("runs") or []),
                }
        except (OSError, TypeError, ValueError):
            pass
    return {"date": today, "cost_usd": 0.0, "decisions": 0, "runs": []}


def _seconds_until_midnight(now: datetime) -> float:
    midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    return max(0.0, (midnight - now).total_seconds())


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
