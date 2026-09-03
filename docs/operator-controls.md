# Operator controls and session checkpoints

Implemented in runtime `45526a3`. The control panel shares the overlay's game state, farmer narration, and action trace.

## Open the controls

1. Start a harness run with the session settings you approved.
2. Start the overlay from the repository root:

   ```powershell
   .\harness\run-harness.ps1 overlay --run latest --port 8765
   ```

3. Open [the operator view](http://127.0.0.1:8765/?operator=1).
4. Use [the audience view](http://127.0.0.1:8765/) as the OBS browser source. It hides the operator panel.

The server accepts commands on this computer only. An old tab cannot target a different run with its old run ID. A closed run rejects commands.

## Finish and save

1. Select **Finish & Save**.
2. Wait for **Received**. **Queued** means the harness has not consumed the command yet.
3. Let the farmer return home and use the bed. The command takes priority over chores and curiosity. Early sleep is allowed.
4. Wait for **Saved and stopped** and the checkpoint ID.
5. End the broadcast in OBS when you are ready.

The harness requires an increased save count, an advanced calendar date, the same save identity, and a settled morning in FarmHouse. It then checks the saved game's date on disk. It copies the game save, SaveGameInfo, objective ledger, notebook, and world memory into a checkpoint with file hashes. The interrupted objective remains available for the next session.

The harness closes a game that it launched after verifying the checkpoint. If the game was already running, it leaves the game open in a normal menu. Finish & Save does not operate OBS or Twitch.

If navigation reaches its retry limit, a configured limit stops the run, or checkpoint verification fails, the panel reports that assistance is needed. The game stays open. The supervisor does not restart an operator-ended run. Check the reported error before closing the game. No additional model requests are allowed once the run's model budget has been spent; local bedtime controls can still complete when already on the farm.

## Steer or hold

1. Enter a direction in **Steer the farmer**. For example: “That entrance is blocked. Go through town and approach the farm from the bus stop.”
2. Select **Send guidance**.
3. Wait for **Received**.
4. Review the following actions in the trace.

Guidance remains in the farmer's context until replaced or the run ends. Guidance can help during Finish & Save, but does not cancel that save request. A decision produced before newly received guidance is discarded before execution.

**Hold unsaved** stops autonomous input at the next safe action boundary and leaves the game open. It does not save. Resume requires a new harness run. Use this for a manual handoff when saving cannot wait for autonomous recovery.

Commands are cooperative. They can wait for a model request or a local multi-step controller. They do not instantly cancel an in-flight input. Each pipe response has a 90-second deadline; a logical model request has a 150-second budget. These are separate limits, not a total command-latency guarantee. Normal gameplay keeps the clock running. The handoff menu can pause it normally after input stops.

## Resume a saved session

1. Keep the checkpoint's game save in place. Do not replace it with an older copy.
2. If you used the STOP file, remove `harness/state/STOP` before restarting.
3. Set `$sessionBudget` to the model budget you approve for this session.
4. Start the next session:

   ```powershell
   .\harness\run-harness.ps1 run --continuous --resume-checkpoint --budget-usd $sessionBudget
   ```

5. Start the overlay if it is not running.

Do not combine `--resume-checkpoint` with an explicit objective or isolated state. Add recording options separately if needed.

Resume validates the checkpoint copies and the current game files, backs up current harness state, and restores the matching objective ledger, notebook, and world memory. It never overwrites the game save. After loading, the harness verifies the save identity and full calendar date before playing. The existing title loader selects the first listed save; load the matching save yourself if the identity check fails.

If the game save changed after the checkpoint, checkpoint resume refuses it. Resume normally to preserve newer progress; do not force a rollback. Checkpoints currently cover these three harness JSON files, not an in-flight model conversation. Archived events remain available separately.

## Files and agent recall

| Data | Location |
| --- | --- |
| Command receipts and status | `harness/runs/<run>/control/status.json` |
| Queued and consumed commands | `harness/runs/<run>/control/inbox/` and `processed/` |
| Decisions, results, observations, usage and command audit | `harness/runs/<run>/events.jsonl` |
| Latest checkpoint pointer | `harness/state/checkpoints/latest.json` |
| Checkpoint manifest and copies | `harness/state/checkpoints/<id>/` |
| Harness state before a checkpoint restore | `harness/state/before-resume/<id>/` |
| Rebuildable history index | `harness/state/history.sqlite3` |

The actor and director can call `search_history(query, kind, limit)`. It searches earlier recorded actions and objectives, returns at most five excerpts of at most 600 characters, and includes source IDs. Event references use `run-id:byte-offset`; objective references use `ledger:objective-id`. Indexing reads only new complete event lines after the initial scan. Search currently scans the indexed text; it is not semantic retrieval. Results share the existing context budget and can be trimmed. The full archive is never appended to a model conversation.

The STOP file now requests Finish & Save during an active run. Before a supervised run starts, the same file prevents startup. A supervisor sleeping after daily budget exhaustion still checks STOP when that sleep ends.

## Verification and remaining gate

- 197 offline tests pass. They cover command delivery, stale decisions, save proof, changed-save rejection, retry exhaustion, budget handling, season/year identity, history retrieval, and supervisor termination.
- Browser verification: guidance and save submissions work; operator controls are hidden in the audience view; no page errors.
- Live save run `23d519a3-699b-4317-a1eb-66643c285de5`: FarmHouse, Spring 19 → Spring 20, checkpoint `aec76ef4c7174c46ac82d627a5d52f56`, stop reason `finish_saved`. Zero model calls and $0 cost.
- Live reload run `3f1286e8-9646-4409-8865-6e3b09c0a573`: restored and loaded the same Spring 20 checkpoint, verified identity/date, then closed without farm actions. Zero model calls and $0 cost.

The later south-entrance check returned through Forest → Town → BusStop → Farm → FarmHouse and saved Spring 21.
The current checkpoint is `6394d027d2db4de480309f852d5668c6`; reload and one-call dialogue selection passed.
Runtime `ad203b1` also retains the original interrupted intention when an unfinished save request is retried after restart.
204 offline tests pass. Short OBS audio/overlay and three local outputs are verified; the clean 30-minute rehearsal remains.
See [current evidence](readiness-2026-09-03.md) and [broadcast setup](streaming.md).
