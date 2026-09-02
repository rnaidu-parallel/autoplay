# Harness latency investigation — 2026-09-02

The optimized Luna controller reached approximately three seconds per model response in live gameplay. The complete decision-and-action cycle still takes approximately six seconds because movement is real game time. This is a bounded navigation/planting result, not a general three-second gameplay guarantee.

| Measurement | Previous Luna | Final Luna |
| --- | ---: | ---: |
| Model response mean, two live actor calls | 4.394 s | 3.036 s |
| Time from first ready observation to five-crop completion | 16.512 s | 12.477 s |
| Actor calls / director calls | 2 / 2 | 2 / 0 |
| Actor input tokens | 13,591 | 4,212 |
| Actor cached input tokens | 3,129 | 2,086 |
| Actor uncached input tokens | 10,462 | 2,126 |
| Actor cache fraction | 23.0% | 49.5% |
| Total reported model cost | $0.005525 | $0.000587 |
| Verified plantings | 5 | 5 |

The final run's static prefix was already warm from the replay probe. The old and new samples were sequential, not randomized; improvements combine smaller inputs, shorter outputs, changed review scheduling, and cache behavior. Do not attribute all savings to caching or assume this exact latency will persist. The prior GLM low result was 6.602 s per actor response and 20.216 s for the task; GLM was not rerun because the current provider constraint permits only official Google/OpenAI inference. All new calls used Luna low, 600 output tokens, only OpenAI through OpenRouter, with fallback disabled and response attribution checked.

## What was inefficient

1. Every actor call sent the full visual prompt and about twenty tools, including controls irrelevant to navigation and planting. The Farm snapshot included 3,448 characters of obstacles and 701 characters of collision rows even though bridge navigation already owns collision checks. Old Luna requests were 5,761 and 7,830 tokens.
2. An explicit objective still triggered a startup director review; reaching Farm triggered another. Both reviews were discarded after state changed. The actor did not need them to execute this task.
3. The model selected and emitted five coordinate pairs even when the request simply asked for the nearest five tiles. The compact pilot spent 131 reasoning tokens on its planting choice; the count-based pilot needed 20.
4. The reported cache percentage mixed first/cold actor calls with a different director prefix. Old Luna's aggregate was 20.6%, but its second actor call already cached 40% of input. A changing screenshot/state cannot reuse the same complete prefix.
5. Generic pointer actions added a one-second sleep after the bridge had already released the mouse and returned state. The planting macro bypassed this path, so removing that sleep is not credited for the planting benchmark.

## Changes

- Default `--actor-mode state-first`: compact typed crop/inventory tables and six tools for verified navigation, travel, nearest planting, wiki lookup, scene inspection, and stopping. No image is sent to the model on this path. Full state remains with the executor and in the audit trail.
- Menus, events, minigames, nearby NPCs, or unavailable movement use the full visual path. `inspect_scene` requests a fresh visual decision when another skill, exact tiles, or visual evidence is needed. It counts toward the decision cap and sends no game controls. `--actor-mode visual` remains available.
- `plant_nearest_seeds(seed_slot, count)` selects observed empty soil locally in greedy Manhattan-distance order. It uses the existing ordinary-input executor and checks every live crop and one-seed decrement. Selection is not a globally optimal path and may fail on inaccessible soil; the collision-aware executor stops rather than forcing a move.
- Existing objectives no longer trigger startup or location-change director calls. Routine reviews remain asynchronous at the configured action interval; new-goal selection remains synchronous when no objective is active.
- Luna sends an explicit cache boundary after stable instructions, tools, and objective. The prefix key remains stable across runs with the same prefix. Objective audit timestamps are omitted from prompts, retained on disk. Changing observation and image blocks are outside the cache-write boundary.
- Reports include arithmetic means, complete actor-cycle timings, per-role cache reads/writes, and hit fraction after the first logical call. Planting results include timing for each individual control. No C# or game-speed changes.

## Cache diagnosis and verification

The initial compact prefix did not cache: the explicit boundary was below the model's minimum eligible prefix size. Moving the stable objective before the boundary and retaining useful control-contract instructions brought it to **1,043 eligible tokens**. The final eight-request replay wrote that prefix once, then read exactly 1,043 tokens on all seven subsequent calls. No dynamic-state cache writes occurred.

The replay alternated recorded FarmHouse/Farm observations and changed the decision counter. All eight returned the expected travel or five-seed intent. It averaged **2.563 s** including its first request, **2.419 s** after the first; range 1.771–3.571 s. Overall cached-input fraction was **50.1%**, or **56.5%** after the first call. Cost: **$0.00209037**. This checks model intent against recorded states; it executes no controls and does not prove live gameplay by itself.

The final live run reused the same stable prefix on both requests: 1,043 cached tokens each, total cache fraction49.5%. The smaller absolute uncached input is more useful than maximizing a percentage. Padding prompts with unrelated text or reusing stale game observations would be counterproductive.

Provider requirements differ. OpenAI documents a 1,024-visible-token minimum for GPT-5.6 and later and explicit cache boundaries; Google documents a 4,096-token minimum for Gemini3.7 Flash. Cache keys influence routing, not guaranteed hits. Cache reuse reduces input processing and cost; it does not remove output generation, network/provider queues, or gameplay time. Sources: [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching), [OpenRouter forwarding and caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching), [Google caching](https://ai.google.dev/gemini-api/docs/caching), [OpenAI latency guidance](https://developers.openai.com/api/docs/guides/latency-optimization).

## Execution time and remaining target

Final live actor-cycle mean: **6.455 s** = roughly3.036s request +3.111s controls +0.308s observation/overhead. The two actions were travel to Farm and the entire five-seed planting batch.

The planting batch took **3.191 s** across11 normal controls. Its five navigation operations totaled2.529s; toolbar selection83ms; five clicks totaled577ms (100–177ms each). Most of the execution time is walking, not Python overhead or a three-second click. It chose the same nearby five tiles as the earlier successful GLM benchmark. Health100, stamina135, money5, and all non-seed inventory remained unchanged; seeds20→15; planted crops0→5; objective false→true. Soil was already watered.

A three-second response target is now supported by these samples. A three-second full cycle is **not** achieved. The next architectural step for smooth visible play is longer verified local skills and planning during safe ongoing execution, with fresh-state validation before applying a plan and freezing as a backstop. That concurrency is not implemented here; current actor requests still freeze simulation. Watering/tilling, return/sleep/day persistence, visual fallback gameplay, and reliable recording remain acceptance gaps.

## Evidence

- Final live run: `2f7e36d4-0a7c-496e-bad5-5801154d42a1`.
- State-only pilot: `f15aea7c-2a9f-496c-9f14-f01d91f307ef`:3.592s mean, five crops, initial short prefix/no cache.
- Count-skill pilot: `eadebe1f-a25b-43db-b466-2167cf7d449b`:2.724s mean, five crops, prefix still below minimum.
- [Metrics and control timings](metrics.json), [replay requests](replay.json), [replay script](replay_probe.py), [summary script](summarize_trials.py), [intermediate prefix diagnostic](prefix-size.json).
-61 Python tests passed, including visual fallback without inputs, strict provider attribution, cache-boundary placement, nearest-target validity, planting postconditions, and existing objective/call limits. Three bounded live trials verified five plantings each. Owned game/FFmpeg processes were absent after cleanup. No overnight saves or provider-policy changes.

Replay is a paid, bounded model probe. Run from the repository with the harness on `PYTHONPATH`; it uses the existing ignored OpenRouter key and never prints it or executes game actions. Re-running it overwrites `replay.json`; historical live logs remain unchanged.
