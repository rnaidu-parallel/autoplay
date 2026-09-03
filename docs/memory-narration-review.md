# Memory and narration review — 2026-09-03

Memory, variety validation, narration, the overlay trace, and runner integration are implemented. The recorded
rehearsal is in progress. No model API calls were made during initial implementation.

## Acceptance decision

The parent spec describes 4,500 actor / 8,000 director prompt-token limits, but its trimming procedure applies
to context JSON. Before adding narration, the visual actor's fixed instructions and tools alone estimated
5,993 tokens using that spec's 3.5-character estimator. A whole-request actor limit cannot be satisfied while
preserving those protected inputs. The implementation caps context JSON and reports provider prompt tokens
separately. Rahul accepted that interpretation on 2026-09-03 and authorized the recorded rehearsal and evaluation.

## Verification

- Full Python suite passed: 164 tests, including the live animation-budget and visual-context regressions. No game or provider connection is used by this suite.
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

## Remaining recorded session

Back up the game save and persistent harness state. Resume the current ledger goal; pass both required
objective arguments and `--continuous` in addition to the flags from the original handoff:

```powershell
cd E:\Claude\autoplay
$active = (Get-Content -LiteralPath .\harness\state\objectives.json -Raw | ConvertFrom-Json).active
.\harness\run-harness.ps1 run --objective $active.goal --success-condition $active.success_condition `
  --continuous --max-minutes 30 --record-video --video-retention-segments 0 --budget-usd 0.60 --keep-game-open
```

Start the overlay with `overlay --run <new-run-id> --port 8765`. Keep the game open after the measurement
window. Verify a nightly save before closing it; ordinary timeout cleanup otherwise terminates the game
with the current day unsaved. Evaluate the recorded window against `docs/streaming.md`, and report any
post-window save controls separately from autonomous-session interventions. Stop the overlay feed afterward.
