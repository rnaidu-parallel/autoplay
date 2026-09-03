# Memory and narration review — 2026-09-03

Memory, variety validation, narration, the overlay trace, and runner integration are implemented. Four recorded
attempts were evaluated; none passed the 30-minute gate. Total model spend: $0.327020 of the $0.60 session cap.
No public stream was started. The game was closed only after the Spring 19 nightly save was verified.

## Acceptance decision

The parent spec describes 4,500 actor / 8,000 director prompt-token limits, but its trimming procedure applies
to context JSON. Before adding narration, the visual actor's fixed instructions and tools alone estimated
5,993 tokens using that spec's 3.5-character estimator. A whole-request actor limit cannot be satisfied while
preserving those protected inputs. The implementation caps context JSON and reports provider prompt tokens
separately. Rahul accepted that interpretation on 2026-09-03 and authorized the recorded rehearsal and evaluation.

## Verification

- Full Python suite passed: 174 tests, including animation/context regressions, retry limits, preserved director feedback, bedtime recovery, and advancing story dialogue. No game or provider connection is used by this suite.
- Synthetic large contexts: actor 3,536.9 and director 7,835.7 estimated tokens after the prescribed trims.
  Tests preserve the objective and last result, check trim order, and reject oversized protected content.
- Replay of 266 historical observations with the current notebook: actor median/max 3,822.9/4,299.7;
  director median/max 6,972.0/7,524.9. This offline estimate uses a reduced world summary and is not a measured
  provider prompt or a substitute for the live run.
- Narration schemas cover all actor tools. A regression test verifies that changing narration cannot bypass
  the unchanged-action guard. Stable prefix tests cover the state actor, visual actor, and director.
- Overlay replay on port 8766 returned action data; its process was stopped. A headless 1920×1080 preview
  checked long speech and objective text. Speech remains above the ticker; long objective text is clamped,
  and the visible agenda starts with the active item.
- Release mod build and deployment succeeded. The existing CS9057 analyzer/compiler warning remains.
- The reconstructed Day 18 director failure packet now fits at 7,960.9 estimated tokens. The final trim removes
  local navigation rows and diagnostics; it preserves the farm layout, objective ledger, and last action result.

## Historical cache and latency baseline

Source: `harness/runs/baa41570-f885-4914-8a65-45e01f71a120/events.jsonl`. These are historical numbers from before
the changes, not evidence of a new performance improvement. Latencies are milliseconds.

| Metric | Actor | Director |
| --- | ---: | ---: |
| Calls | 120 | 22 |
| Prompt median / maximum | 6,019.5 / 13,091 | 11,160 / 12,211 |
| Cached input share | 33.6% | 24.1% |
| Completion median | 87.5 | 144.5 |
| Physical model attempt p50 / p95 | 3,764.5 / 6,373 | 4,323 / 6,926 |
| Bridge p50 | 209.5 | n/a |
| Cost per call | $0.001315 | $0.001930 |
| Cost per elapsed game hour | $0.026290 | $0.007078 |

`inspect_scene` accounts for 27.5% of actor decisions. Requests keep the existing model, reasoning effort,
provider restrictions, image size, completion limits, and timeouts. The new report uses calendar day/time
deltas for game hours, including season/year boundaries when available.

## Recorded rehearsal results

Run artifacts are under `harness/runs/`; the combined report, overlay samples, save backups, and cleanup logs
are in `harness/runs/rehearsal-20260903-115556/`. Durations below include startup where present.

| Run ID | Wall minutes | Cost | Outcome |
| --- | ---: | ---: | --- |
| `887b9375-6a69-429f-a607-0ae88ec041d6` | 1.4 | $0.006486 | Actor context overflow after a tool animation; click/wait budget reserve fixed. |
| `4554c2c8-5418-4ea2-bbed-f1b218af9338` | 0.5 | $0.004495 | Visual actor context overflow; local geometry trim fixed. |
| `838f2914-4552-47ec-89fb-05fc3244a5a4` | 5.5 | $0.087280 | Unavailable Desert retry loop; short retry deferral added. |
| `7a81817b-f9eb-46a3-ad39-47a0e48982c9` | 15.3 | $0.228759 | Live clock and retry deferral worked; repeated bedtime plans ended in director context overflow. |

The fourth run produced three 1920×1080 H.264, 30 FPS segments: 5:00, 5:00, and 4:30.40. Frames from each
segment were decoded and inspected. This is raw game video without audio or an embedded overlay. The overlay
feed ran separately; live snapshots and a 1080p rendered preview are retained. This does not verify an OBS broadcast.

| Fourth-run check | Result |
| --- | --- |
| Continuous clock | 368 loaded-world observations, all `simulationPaused=false`; 06:00 to 23:00 on Spring 18. |
| Failed objective recovery | Six objectives deferred after six decisions without progress. The first moved on from Desert. One later deferral incorrectly treated advancing story dialogue as a stall. |
| Two-minute stall ceiling | Failed: 152.2 seconds without observable progress during repeated director proposals, excluding clock ticks. |
| Locations | Six: FarmHouse, Farm, BusStop, Town, Blacksmith, Forest. |
| Pass-outs / autonomous saves | Zero observed pass-outs; zero autonomous nightly saves during the window. |
| Narration | 88/88 actor decisions had speech; maximum 137 characters. The speech panel lost its current line during the prolonged director-only loop. |
| Task progress | Three verified predicates completed; zero verified seed plantings. The planting predicate was satisfied by existing crops on arrival, so it does not prove new planting. |
| Cost | $0.228759 for 88 actor and 48 director calls; four extra physical attempts rejected malformed model arguments. |

Latest measured cache and latency, using the report's provider usage accounting:

| Metric | Actor | Director |
| --- | ---: | ---: |
| Prompt median / maximum per logical call | 10,604 / 11,371 | 12,153 / 25,599 |
| Cached input share | 42.3% | 30.2% |
| Physical attempt p50 / p95, ms | 4,038.5 / 6,558 | 4,907 / 9,255 |
| Completion median | 106 | 222 |
| Cost per logical call | $0.001331 | $0.002326 |

Logical-call usage includes physical retries, which explains the director maximum. Actor visual-inspection
share was 25%. Model requests stayed within the JSON caps (actor max 4,493.7; director max 7,988.6); the fatal
8,312-token director packet was rejected before sending. Workload differences prevent a controlled performance
comparison with the historical baseline.

After the recording, ordinary controls took the southern-farm detour through Forest → Town → BusStop → Farm →
FarmHouse. The character reached bed at 01:10, slept, and reached Spring 19 at 06:00 with `saveCount=1` and
`nightActive=false`. This manual cleanup is logged separately in `post-fourth-cleanup.jsonl`; it is not an
autonomous recovery. The game, recorder, overlay, and owned launchers are stopped.

Post-run fixes pass the full test suite: advancing dialogue counts as progress; agenda reminders preserve
rejection feedback; bedtime can reopen a deferred home target; three repeated invalid proposals stop the run;
blocked bedtime travel stops for handoff; director contexts can shed local navigation geometry. These final
Python changes have not yet passed another autonomous recording.

## Remaining gate

Before another full rehearsal, improve return-home routing across different farm entrances and reject goals
that claim new work through location-dependent existing totals. A clean 30-minute session, autonomous nightly
save, continuous readable overlay trace, and game audio still need verification. The current build is not
ready for unattended streaming. No more paid calls were made after the fourth failure; $0.272980 remains
under this session's original cap.

For a later rehearsal:

Back up the game save and persistent harness state. Resume the current ledger goal; pass both required
objective arguments and `--continuous` in addition to the flags from the original handoff:

```powershell
cd E:\Claude\autoplay
$active = (Get-Content -LiteralPath .\harness\state\objectives.json -Raw | ConvertFrom-Json).active
.\harness\run-harness.ps1 run --objective $active.goal --success-condition $active.success_condition `
  --continuous --max-minutes 30 --record-video --video-retention-segments 0 --budget-usd 0.27 --keep-game-open
```

If the ledger has no active goal, choose a fresh verifiable objective instead of passing empty arguments.
The example budget leaves a small margin for the final billed call. Start the overlay with
`overlay --run <new-run-id> --port 8765`. Keep the game open after the measurement
window. Verify a nightly save before closing it; ordinary timeout cleanup otherwise terminates the game
with the current day unsaved. Evaluate the recorded window against `docs/streaming.md`, and report any
post-window save controls separately from autonomous-session interventions. Stop the overlay feed afterward.
