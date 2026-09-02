# GLM 5.3 Flash evaluation — 2026-09-02

## Result

GLM improved control batching and achieved three planted, watered parsnips within 35 game actions; the five-crop objective remained incomplete when the cap interrupted the final batch. Live cache usage increased to 50.0%, but model latency and retries still prevent watchable real-time pacing.

## Implemented configuration

- Default model: `z-ai/glm-5.3-flash`; explicit `reasoning.effort=low` for actor and director.
- Allowed endpoints only: `z-ai/fp8`, `novita/fp8`, `deepinfra/fp8`; `sort=price`, FP8, `allow_fallbacks=false`, `require_parameters=true`.
- All three advertised the same input/output/cache-read prices when checked. Z.AI served all 23 logical gameplay decisions.
- Free profile retained through `--model minimax/minimax-m3:free`, restricted to `gmicloud/fp8`; no reasoning override for MiniMax.
- CLI model/effort selection, provider attribution in telemetry, provider counts and cost in reports. Stronger explicit instruction to batch safe known controls.
- 38 Python tests pass, including provider allowlists, low reasoning, stable system/tool/session prefix, and rejecting unsupported model/effort combinations. No C# changes were needed.
- GLM metadata currently lists mandatory reasoning and efforts `max`, `high`, `low`; default is `max`. `medium` is not advertised, so the requested low/medium range was implemented using `low`.

## Prompt caching

The harness already sent stable system text, stable tool schemas, stable field ordering, and per-role sticky `session_id` values. No explicit cache breakpoint is required by Z.AI. Cache usage is cached input tokens divided by total input tokens; it is not a percentage of requests answered from a response cache. Game state, frame contents, and the rolling recent-event window change between decisions, limiting reusable prefixes.

Five successful logical diagnostic requests reused one saved game frame and executed no game actions:

| Endpoint | Logical call latency | Warm cached-input ratio | Result |
|---|---|---|---|
| Z.AI | 13.379 s / 13.328 s / 24.611 s | 9,984 / 10,014 = 99.7% | Three valid 120-tick movement decisions |
| Novita | 40.236 s / 17.525 s | 4,992 / 5,010 = 99.6% | Two valid 120-tick movement decisions |
| DeepInfra | 14.140 s including bounded retries | unavailable | HTTP 429, upstream `engine_overloaded` |

The static probe shows that caching works. It does not show that inference latency disappears when most input is cached. Diagnostic request cost reported by successful responses: approximately $0.00155.

## Bounded gameplay comparison

GLM run: `f0517bf7-4a9c-4b4e-a562-83b7108d0bfe`. Previous MiniMax sample: `724c1235-491a-40b2-879c-89853349be0b`.

| Metric | Previous MiniMax sample | GLM low |
|---|---:|---:|
| Game action cap | 35 | 35 |
| Actor decisions | 37 | 20 |
| Director reviews | 2 | 3 |
| Actor model p50 | 7.959 s | 9.807 s |
| Actor model p90 | 21.333 s | 33.977 s |
| Actor model maximum | 33.587 s | 43.734 s |
| Director model maximum | 8.781 s | 87.171 s |
| Cached input fraction | 15.7% | 50.0% |
| Objective completions | 0 | 0 |
| Crops planted | 0 | 3 |
| Final stamina | 113 | 135 |
| Recoverable loop errors | 0 | 0 |
| Reported model cost | $0 | $0.015698 |

GLM: 7.3 min including deterministic startup; 394.4 s from first director observation to stop; 96.0% of that autonomous-loop time in model calls. FarmHouse Day 10 06:00→Farm Day 10 06:10. Final Farm: planted 3, watered 3, tilled 16, Parsnip Seeds 17, stamina 135. Same 35-action limit; not a controlled benchmark: GLM included farmhouse exit, while MiniMax's sample began on the farm after deterministic travel probes, and batching instructions were strengthened for GLM.

Tool outcomes: 19 completed, 1 blocked. The 5% rejected/blocked/timeout metric understates unproductive actions: many completed C presses did not plant anything. The full five-crop success condition was retained and never falsely completed. No gameplay save occurred before normal bounded shutdown.

## Batching evidence

- `hold`: one model tool decision holds a direction for up to 600 game ticks; no extra model call per tick or tile.
- `navigate_to`: one decision invokes bridge-side pathfinding and ordinary movement for the current location.
- `go_to_location`: one decision performs adjacent-location travel. GLM exited FarmHouse→Farm on its first actor decision, 4,242 ms model + 3,056 ms execution/settling.
- `control_sequence`: one decision executes up to six keyboard steps, stopping on an unexpected block, menu/location transition, or run action cap.
- This run used 8 sequences for 23 keyboard steps; total 35 game actions came from 20 actor decisions. Last sequence requested 6 steps, executed 2 before the cap, and planted a crop.
- Sequences currently cover keyboard input. There is no dedicated multi-tile planting/watering/harvest operation that resolves fresh target coordinates and verifies each tile locally; mouse targeting still requires separate decisions where needed.

## Interaction diagnosis

Step 17 right-clicked empty tilled tile (64,19): crops 0→1, seeds 20→19. Step 21 aimed the cursor at (60,18); step 22 used C: crops 1→2, seeds 19→18. Step 23 batched east movement and C: crops 2→3, seeds 18→17. Therefore C can plant seeds; earlier failures were target alignment, not lack of keyboard support. The actor initially repeated different C holds without a state change, then diagnosed the cursor mismatch.

## Remaining latency question

Slow decisions include retried provider responses. Aggregated actor usage at step 6 was 20,300 prompt / 951 completion tokens; step 8 was 30,624 / 1,370. Director step 19 was 25,782 / 1,617. Each request has a 600-token output cap; these logical decisions required more than one response. Current telemetry does not preserve individual failed-response finish reasons, so output-cap truncation versus another response-format failure is not yet conclusively separated. The 45 s request timeout, 150 s decision budget, and 600-token cap were retained.

Next options: first record failed-response finish reasons and correct the demonstrated cause within the requested low-effort policy; separately implement a bounded tile-targeted farming operation using the now-verified cursor/input interaction. A model swap and cache improvement alone have not produced streamable pacing.

## Evidence and sources

- [Metrics](metrics.json), [Z.AI identical-prompt probe](cache-probe.json), [other provider probes](provider-probes.json).
- Gameplay telemetry and diagnostic frames: `harness/runs/f0517bf7-4a9c-4b4e-a562-83b7108d0bfe/`.
- [OpenRouter model metadata](https://openrouter.ai/api/v1/models) — verified slug and supported efforts.
- [Provider endpoint metadata](https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints) — endpoint tags, capability and price check.
- [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) — Z.AI automatic caching and session affinity.
- [OpenRouter reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

All harness-launched game and diagnostic processes finished. No public streaming or external messages were sent.
