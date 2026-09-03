# Going live: step-by-step plan

Status as of 2026-09-03. Prerequisites shipped: verified farm-day skills, world map and travel, daily planner
with farm zones, guards (bedtime, stall, budget), borderless display with focus watchdog, recorder restart,
overlay v1, and the `--forever` supervisor. Everything below runs on the helios Windows machine that owns the
game, SMAPI, and the harness.

Latest rehearsal: four attempts evaluated, $0.327020 total; longest raw recording 14:30.40. The continuous
clock worked, but blocked return-home routing and a director loop failed the viewing gate. Final retry/context
fixes pass 174 tests and still need another recorded run. See [measured review](memory-narration-review.md).

## Phase A: private dress rehearsal (no viewers)

1. Fresh save or the current one. Back up `%APPDATA%\StardewValley\Saves\<save>` first.
2. Start the harness forever mode with a daily budget and the recorder:
   ```powershell
   cd E:\Claude\autoplay\harness
   .\run-harness.ps1 run --forever --budget-usd 3.00 --record-video --video-retention-segments 0
   ```
   The daily budget is the tripwire; the harness sleeps until local midnight when it is hit.
3. Start the overlay feed in a second terminal:
   ```powershell
   cd E:\Claude\autoplay\harness
   .\run-harness.ps1 overlay --run latest --port 8765
   ```
4. Open OBS. Scene "Autoplay": Game Capture (Stardew Valley window, borderless) + Browser Source
   `http://127.0.0.1:8765/`, 1920×1080, transparent, refresh on scene activation. Add Audio Output Capture for
   game audio. Set OBS to record locally (NVENC, 1080p30, 6 Mbps) and let it run for 6 hours.
5. Review the recording against the checklist below. Fix what fails. Repeat until two consecutive sessions pass.

Checklist per session: zero pass-outs; zero stalls over two minutes; zero manual interventions; at least three
distinct locations per in-game day; agenda visible and updating on the overlay; narration readable; restarts, if
any, recovered within a minute with the overlay showing "restarting"; cost within budget.

## Phase B: unlisted test stream

1. Twitch: create the channel, enable VOD storage, set category Stardew Valley, title "AI plays Stardew Valley
   (autonomous agent, test stream)". Keep the channel unpromoted.
2. OBS Stream settings: Twitch, server auto, NVENC H.264, 1080p30, 6000 kbps CBR, keyframe 2 s, audio 160 kbps.
   Enable "Automatically reconnect" with 10 s delay and 20 retries.
3. Start the harness and the overlay as in Phase A, then Start Streaming. Run for 3 to 6 hours.
4. Watch the stream from a phone for the first 15 minutes: overlay legible, audio present, no desktop leaks when
   the harness restarts the game (the hold scene should cover it).
5. After the session, read the VOD at 2× and the harness report:
   ```powershell
   .\run-harness.ps1 report
   ```

## Phase C: public stream

1. Only after two clean Phase B sessions. Announce a fixed daily window (for example 18:00 to 00:00 local) and
   keep to it; the harness runs 24/7 but the stream window is what viewers learn.
2. Chat stays read-only for the agent; chat interaction is a later design.
3. Moderation: enable Twitch AutoMod level 2, follower-only chat for the first week.
4. Weekly: rotate the save backup, archive VODs, review the lessons store and the cost ledger, and prune the
   notebook if the weekly roll-up did not fire.

## Operations

- Finish the session: use **Finish & Save** in `http://127.0.0.1:8765/?operator=1`. Wait for **Saved and stopped**. The active-run STOP file requests the same flow. See [operator controls](operator-controls.md) for steering, handoff and checkpoint resume.
- Emergency stop: close OBS streaming first, then Ctrl+C the harness; the finally block restores the display
  mode and closes the game.
- Status: `harness/state/forever_status.json` (running, restarting, sleeping_budget, stopped) and
  `harness/state/cost_ledger.json` (today's spend).
- Logs: `harness/runs/<run>/events.jsonl`, `harness/state/forever.log`, SMAPI log in
  `%APPDATA%\StardewValley\ErrorLogs\SMAPI-latest.txt`.
- Secrets: the OpenRouter key lives in `.env` only; never show a terminal on stream.

## Known gaps before Phase C

- Narration and the Speech panel are implemented. Live speech covered all 88 actor decisions in the latest run,
  but the panel went blank during a prolonged director-only loop. A clean 30-minute run and an OBS recording
  with audio and overlay are still required.
- Return-home routing must handle different farm entrances. Nightly save still needed manual post-window
  recovery in the latest rehearsal; existing-crop totals also produced a misleading planting completion.
- Progression state (skills, friendship, quests, mail) is not exposed; town social play is shallow until then.
- Festivals and seasonal events are untested.
- Fishing, mining, and combat are untested; the director is steered away from them until proven.
