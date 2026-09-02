# Autoplay runtime validation — 2026-09-02

Current working tree built and tested; no source files changed. Repository still has no commits, so no isolated before/after source diff is available.

## Verdict

Build and basic controls pass. In-map navigation and three location crossings work through the harness. The bounded autonomous farming objective failed: zero of five required crops planted. The live game was visible and capture was correct; the sampled recorded MP4 showed stale title-screen imagery.

## Checks

- `dotnet build .\src\Autoplay.GameBridge\Autoplay.GameBridge.csproj -c Release`: success, 0 errors, 1 CS9057 warning (SMAPI analyzer expects compiler 4.9; .NET 6 compiler 4.3). Build deployed the mod.
- Bundled Python `-m unittest discover -s .\harness\tests -v`: 36 passed.
- `bridge-test --fullscreen`: passed at 1920×1080; observe, cursor, click, drag, scroll, wait, press completed.
- Initial autonomous smoke run `d302e6d1-58c0-4d2c-aead-5868cd13e429`: 6 actor decisions, 1 director, 5-action cap, zero objectives, 3 blocked / 1 rejected / 2 completed. Actor repeatedly targeted the farmhouse warp with `navigate_to` instead of `go_to_location`.
- Direct raw bridge probe: walked to the farmhouse exit, returned `blocked/no_progress_toward_(3,12)` during the transition fade (`canMove=false`). Initial probe omitted the normal harness settling step; this was corrected in the next probe without changing source.
- Full harness probe: FarmHouse→Farm, Farm→BusStop, BusStop→Farm all completed after existing `_settle_transition` waited for `can_move=true`. Farm→FarmHouse failed with `no_exit_to_location`.
- All tested game and recorder processes stopped after their dependent runs; verified no remaining StardewModdingAPI/FFmpeg processes.

## Autonomous planting sample

Run: `724c1235-491a-40b2-879c-89853349be0b`. Model: `minimax/minimax-m3:free`, pinned GMICloud FP8 endpoint; fallback disabled. Same game continued after the deterministic navigation probes. Objective: `location is Farm, plantedCrops >= 5, worldReady is true`. Limits: 35 game actions / 45 decisions; director interval 24. Stopped normally at `max_actions_reached`.

| Metric | Earlier continuous baseline | Current short sample |
|---|---:|---:|
| Actor decisions | 1,759 | 37 |
| Director decisions | 278 | 2 |
| Actor model median | 6.946 s | 7.959 s |
| Actor model p90 | 13.836 s | 21.333 s |
| Actor model maximum | 60.391 s | 33.587 s |
| Blocked/rejected/timeout result rate | 33.6% | 5.4% |
| Cache hit rate | 9.7% | 15.7% |
| Recoverable errors | 6 | 0 |
| Objective completions | 4 | 0 |

These are unequal workloads and sample lengths; the table is descriptive, not a controlled performance benchmark. Baseline: `ec44b7b5-e937-42a5-915e-043e1776a818`.

Autonomous segment: 440.9 s (7.35 min); model calls consumed 93.8% of this segment. `report` shows 7.8 min including navigation-probe events. Steady observations: median 187 ms, p90 205 ms; tool execution: median 286 ms, p90 667 ms. Tool results: completed 34, rejected 1, blocked 1, recorded 1. Prompt tokens 444,429; completion tokens 11,132. No extrapolation from 20 in-game minutes to a full game day is justified.

World changes: Day 10 06:00→06:20; stamina 135→113; Wood 1→2; Stone 2→3. Tilled tiles 16→16; planted crops 0→0; Parsnip Seeds 20→20. The actor repeatedly used the hoe and claimed tilling progress without an increase in tilled tiles. Late seed selection and use did not plant anything. Harness did not complete the objective.

## Observed gaps and options

1. **Progress recognition:** prioritize a bounded, reproducible till-and-plant control check. Either diagnose the existing per-key tool targeting/interaction first, or introduce a narrowly specified ordinary-input farming macro with state-delta verification. Do not treat stamina consumption or a completed keypress as evidence of crop work. No fix applied in this session.
2. **Farmhouse entrance discovery:** `FindExit` handles warps, normal buildings, and selected map action forms; the current Farm→FarmHouse entrance is not found. Inspect that entrance representation and extend only the missing supported case, or continue using explicit doorway approach/action controls until it is implemented.
3. **Transition reporting:** successful crossings return raw blocked status during fade, then succeed through the existing harness settle step. Make bridge transit completion wait for its target location within the bounded request, or retain settling while preserving the raw status separately for diagnostics.
4. **Recording:** `GameplayRecorder` uses FFmpeg `gdigrab desktop`; one completed segment sampled at 30 s showed the title menu while contemporaneous telemetry and live DXGI/WGC capture showed Farm gameplay. Likely true-fullscreen desktop-capture incompatibility. Reuse the working game capture backend for recording, or test borderless capture as a diagnostic. Recording is not accepted as VOD evidence from this run.

Recommended next step: verify a single till→plant action against crop/inventory deltas, then correct the smallest demonstrated control or actor-context cause before another longer run. Keep the current objective constraints.

## Evidence

- Metrics: [metrics.json](metrics.json)
- Live game screenshot: [live capture](live/0b43e305-be04-45be-af4b-d01c0d254e43.jpg)
- Stale recorded frame: [MP4 sample](live-check.jpg)
- Telemetry: `harness/runs/724c1235-491a-40b2-879c-89853349be0b/events.jsonl`
- Navigation probe artifacts: `artifacts/runtime-validation-2026-09-02/events.jsonl` and `frames/`
