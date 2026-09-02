# Async director, verified planting, and Gemini trial — 2026-09-02

Routine director reviews now run in one background worker. The actor continues on its objective. Only the main thread observes and controls the game, writes telemetry, and applies the ledger update. Results are discarded after objective, location, day, crop, resource, menu, or safety changes. Background block/set-objective decisions cannot interrupt the actor; choosing a new goal when no objective exists remains synchronous. The simulation still freezes while awaiting the actor's own model call.

The new `plant_seeds` tool selects seeds, walks to each requested observed empty tilled tile, aims from fresh zoom-corrected coordinates, and right-clicks through ordinary controls. A live crop at that exact tile plus a one-seed inventory decrement is required before continuing. At most six tiles; all internal inputs count against the action cap. It does not till or water. Completion is now checked immediately after a tool result, including the final allowed action.

## Live results

Same day-10 save, objective, 12-action cap, six-actor-decision cap, and director interval four. Runs used diagnostic screenshots, not the known-stale fullscreen GDI video recorder. Ledger history persisted between runs, so these are bounded acceptance trials rather than a statistical model benchmark.

| Profile | Actor calls | Actor p50 | Autonomous work time | Crops planted | Response retries | Reported cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| GLM 5.3 Flash low / Z.AI | 2 | 8.037 s | 22.443 s | 5 | 0 | $0.001935 |
| Gemini 2.5 Flash-Lite / auto tools | 6 | 4.320 s | 39.130 s | 0 | 4 | $0.007580 |
| Gemini 2.5 Flash-Lite / required tools | 6 | 3.287 s | 24.335 s | 0 | 0 | $0.006186 |

Autonomous time starts at the initial background review submission and ends at the last actor result. It excludes launch/load and cleanup. Director calls overlap actor work; summing role latencies would overstate wall time.

- GLM run `9f6a04bf-345d-4906-8866-b6932f0a75b0`: one travel call, one planting call. Five verified tiles `(62,18), (61,18), (60,18), (58,18), (57,18)`; seeds 20→15; stamina 135 unchanged; planted/watered crops 5 (rain). Planting controls took 2.971 s. Model requested six tiles; action cap safely stopped after five, hence `partial` at tool level. This run preceded the immediate completion-recording fix: crop proof succeeded, ledger completion event absent. The final-action regression test covers that fix.
- Gemini auto run `ca77bf60-9448-4686-bb13-7540fc3b3d4b`: three consecutive director responses reached `length / MAX_TOKENS` without a tool; one actor response also truncated. Reasoning tokens were zero. Failure usage retained. Later director attempt succeeded without interrupting actor work.
- Required-tool follow-up `11e5af90-06c0-420e-be05-4cf7461a0ddb`: eight physical responses, all valid tool calls on their first attempt. One director review discarded after leaving FarmHouse; one applied while the actor continued. Five of six actor decisions rejected for invalid or repeated planting targets around x=76–78, absent from observed tilled soil. Seeds remained 20, crops 0.
- Two no-action director probes using a captured FarmHouse state and shorter reconstructed context succeeded with required output at 3.260/2.798 s, 67/46 completion tokens. Warm second probe cached 3,493/3,767 input tokens. This is not an exact replay of the failed historical prompt.

## Routing and diagnostics

GLM remains the default, reasoning low, only Z.AI/Novita/DeepInfra FP8, price ordering, no fallback. Gemini remains an optional CLI trial pinned to Google AI Studio, required tool output, no thinking override (documented default disabled), no fallback. The 600-token cap and strict schema/target validation remain intact.

Per-attempt diagnostics now capture response ID, actual provider, HTTP status, latency, finish/native finish reason, token/cost usage, reasoning tokens, and rejection cause. Failed logical calls retain usage. No prompts, image bytes, or API keys are included in attempt diagnostics. The earlier 87-second GLM review's exact failure cause remains unknown; its old telemetry did not capture finish reasons, and the new GLM run did not reproduce retries.

Short-run cache rates: GLM 0%, Gemini auto 33.2%, Gemini required 13.9%. New prompt/tool prefixes and very small samples make these unsuitable as steady-state cache estimates. Earlier repeated-prefix Z.AI tests demonstrated working caching; cache hit rate alone does not measure useful gameplay.

## Validation and remaining work

Release build/deploy succeeded with zero errors and the existing CS9057 analyzer/compiler warning. All 47 Python tests pass, including blocked background review concurrency, stale/completed-objective rejection, crop/seed verification, failed-input stopping, action caps, final-action completion, retry accounting, and provider restrictions. All launched game processes exited; no FFmpeg process remains. No overnight save was performed; these in-day trials did not persist planted crops.

Keep GLM for gameplay. Next: verified watering/tilling and the FarmHouse return/sleep/day loop; repair fullscreen recording before VOD acceptance. Actor model pauses remain a pacing limit. No public stream was started.

Evidence: [metrics](metrics.json), [director probes](director-required-probe.json), and the three run directories under `harness/runs/` named above.

Sources checked: [Google model capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite), [OpenRouter model pricing/default thinking](https://openrouter.ai/google/gemini-2.5-flash-lite), [Google function calling](https://ai.google.dev/gemini-api/docs/function-calling). Current OpenRouter metadata advertised Google AI Studio tools, tool choice, image input, $0.10/M input and $0.40/M output for the standard route.
