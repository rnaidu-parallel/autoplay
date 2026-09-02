# Autonomous harness

## Runtime shape

The harness uses two model roles.

The actor receives one fresh request per decision. In the default `state-first` mode, free movement without menus, events, minigames, or nearby NPCs uses a compact structured request and six tools: navigation, adjacent-location travel, nearest-seed planting, wiki lookup, visual inspection, and stop. Crop/inventory tables retain exact values; collision maps, obstacle geometry, cursor fields, and images stay with the local executor. All other scenes use the full visual prompt and tool set. `inspect_scene` spends one decision to request that full path on the next fresh observation; it sends no game input. `--actor-mode visual` always sends a screenshot. Diagnostic capture remains available in both modes.

The director runs after a configurable number of game actions and whenever no objective is active. Existing goals no longer cause duplicate startup or location-transition reviews. Routine reviews run in one background worker while the actor continues. The main thread applies only milestone updates whose objective, location, day, resources, crops, menus, and safety state still match the snapshot. Stale results and background blocking decisions are discarded. New objective selection waits for the director when no active objective exists. The worker never controls the game or writes the ledger. Blocked movements and rejected actions are reported to the actor in `harnessLastResult` and `harnessBlockedDirectionsHere`; they do not trigger extra director reviews.

The harness completes the active objective itself, without a model call, the moment its success condition is verified against structured state. The next director review then sets a new objective. A rejected `set_objective` is explained back to the director in `director_feedback` on its next call.

There is no growing transcript of prior user and assistant turns. Each call is stateless. Durable state lives in explicit files and bounded context fields. This keeps request size stable and removes the need to compact a conversational transcript.

## Prompt caching

The system prompt and tool list stay byte-stable between actor calls. Dynamic state stays in the final user message, ordered from slow-changing (ledger, memory, wiki) to fast-changing (game state, frame, counters) so a prefix-caching provider can reuse as much as possible. Each run also sends one stable OpenRouter `session_id`, which gives compatible providers a sticky routing key. Cache availability and discounts still depend on the selected model and provider; the pinned free MiniMax endpoint returned a live hit rate under 10%.

Luna additionally sends a `prompt_cache_key` derived from the model, instructions, tool schema, and stable objective prefix, plus explicit caching with a 30-minute TTL. State-first requests put the objective before the cache boundary; changing state and images follow it. Objective audit timestamps stay on disk and are omitted from the prompt. The final replay prefix wrote 1,043 tokens and reused them on later calls. Prefixes below the provider minimum cannot cache; do not add irrelevant text merely to increase hit rate. Compare absolute uncached tokens and cost, not hit percentage alone. `report` exposes per-role cache reads/writes and hit rate after the first call.

Each request sends the frame downscaled to 1280 pixels wide at JPEG quality 75, caps completion at 600 tokens, and gives up after a 150-second decision budget so a slow provider cannot hold the frozen game indefinitely. Pointer coordinates stay normalized, so the downscale does not affect click accuracy.

OpenRouter uses its OpenAI-compatible Chat Completions endpoint. The harness sends the screenshot as a base64 `image_url` and enables tools. Gameplay defaults to `openai/gpt-5.6-luna`, reasoning `low`, with only the OpenAI provider through OpenRouter, fallback disabled, and provider attribution enforced. The optional GLM profile uses only `z-ai/fp8`, `novita/fp8`, and `deepinfra/fp8`, sorted by price. GLM currently supports `low`, `high`, and `max`, not `medium`. `--model minimax/minimax-m3:free` selects the free local profile through only `gmicloud/fp8`, without a reasoning override. These profiles disable provider fallback and require support for their request parameters. GLM and MiniMax requests use `tool_choice: auto` because not every eligible endpoint supports required tool choice. The Gemini trial uses `tool_choice: required`, verified against Google AI Studio, to prevent free-text responses from consuming the output cap before any tool call. The response parser rejects a response with no tool call and executes only the first call if the model returns more than one. Provider attribution is retained in decision telemetry. See the OpenRouter [reasoning guide](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens), [prompt caching guide](https://openrouter.ai/docs/guides/best-practices/prompt-caching), and [provider routing guide](https://openrouter.ai/docs/guides/routing/provider-selection).

## Objective stability

For official-provider comparisons, use `--model google/gemini-3.7-flash --reasoning-effort low` or `--model openai/gpt-5.6-luna --reasoning-effort low`. OpenRouter routes Gemini only to `google-ai-studio` and Luna only to `openai`, with fallback disabled and parameter support required. Both use required tool choice and retain the 600-token cap. Luna omits temperature because its endpoint does not support it. The client rejects missing or mismatched response-provider attribution before any tool can run. These are routed official upstreams, not direct Google/OpenAI API connections.

For an optional latency trial, select `--model google/gemini-2.5-flash-lite`. This profile uses only Google AI Studio through OpenRouter, with no fallback and no reasoning override (Flash-Lite defaults to thinking disabled). Luna remains the default.

`--model qwen/qwen3.8-flash --reasoning-effort low` uses only Alibaba through OpenRouter. Low is explicit because Qwen's default thinking effort is `xhigh`. Qwen also accepts medium; GLM does not. Qwen retains auto tool choice because its thinking mode does not support required tool choice. All profiles retain the 600-token output cap and strict tool validation.

For model comparisons, `--isolated-state` places a fresh objective ledger under `harness/runs/<run-id>/state/`. It does not read or replace the shared ledger and does not reset the saved game. The initial objective and isolation setting are recorded in the session-start event. [Gameplay evaluation](evaluation.md) describes the outcome checks and remaining gaps.

The persistent ledger is `harness/state/objectives.json`.

- An explicit CLI objective becomes active on a new ledger and supersedes a different persisted objective from an earlier run.
- The actor can record progress and unrelated opportunities.
- The director can revise the current milestone without changing the objective.
- The harness must complete the active objective (or the synchronous director must block it) before the director can set another. Continuous mode rejects blocking caused only by failed controls or large telemetry counters.
- A later run resumes the active ledger when the CLI objective matches or is omitted.
- A parseable success condition is checked against structured SMAPI state on every actor step; the harness completes the objective when it holds.
- Conditions may reference `inventory.<Item Name>` (0 when absent), `plantedCrops`, `wateredCrops`, `harvestableCrops`, `tilledTiles`, `money`, and any other top-level state field, so the director can require real work instead of the passage of time.

This policy prevents a nearby event or short-term distraction from causing continuous objective churn.

## Memory and compaction

Each logical model call includes per-HTTP-attempt latency, status, response ID, provider, finish reason, usage, reasoning-token count, and validation outcome. Failed logical calls retain billable usage in `model_error` events. Background director decisions record `applied`; discarded advice is excluded from actor memory. `director_review_started` and `director_review_finished` mark overlap with actor work. Do not add actor and director latency to estimate wall time because these calls can overlap.

Every observation, decision, tool result, and session boundary is appended to `harness/runs/<run-id>/events.jsonl`. That file is the audit trail and is never placed in the model prompt wholesale. Each event carries a `step` id shared by the observation, decision, and result of one turn. Observations record `observe_ms` and `capture_ms`, decisions record `model_ms` plus the model's `content` and `reasoning` text, and tool results record `bridge_ms`. The runner prints one line per step to stdout so a run can be tailed. `run-harness.ps1 report [run-id]` summarizes a run: latency percentiles per role, wasted-decision rate, director cadence, token usage, cache hit rate, and wall hours per game day.

The prompt receives only:

- the 12 most recent relevant events;
- up to 12 compact entries in each of accomplishments, failures, learned wiki topics, and unresolved opportunities;
- a bounded ledger view containing the active objective, the last three historical objectives, four recent progress entries, and four opportunities.

Compaction is deterministic. It extracts durable entries from structured tool calls and results. It does not ask another model to summarize gameplay, so it cannot silently rewrite objectives.

## Screenshots and coordinates

Before each screenshot, the SMAPI bridge brings Stardew Valley to the foreground. If Windows denies foreground transfer, the capture layer locates the Stardew window and temporarily keeps it above competing windows for the autonomous session. The Python capture tries DXGI first and falls back to Windows Graphics Capture. It reads only the Stardew client rectangle, including in full-screen mode. Window borders, the desktop, and other applications are excluded. Cleanup removes the temporary topmost state.

Pointer tools use normalized coordinates from `0` to `1` and must echo the current `frame_id`. The harness converts those values into the exact client-pixel coordinates expected by SMAPI. A stale frame ID aborts the pointer action. This works in both windowed and full-screen modes.

On a new game launch, the harness waits through the publisher splash and title animation, selects the Load menu, and opens the first existing save before recording or making a gameplay model request. Wrong title submenus are escaped deterministically.

Before launching Stardew, the supervisor sets its startup display preference to windowed because switching to full screen during the startup logo can stall that animation. The autonomous runner waits through the startup logos, then requests borderless full-screen mode through the SMAPI bridge before its first model observation. A rejected display-mode change stops the run before any model request. Normal cleanup restores windowed mode. `--keep-game-open` leaves the running game in full screen.

While a playable world is loaded, the bridge owns `Game1.paused`. Each observation freezes simulation; each bounded control resumes it and freezes it again after completion. This prevents provider latency from advancing multiple unattended days. The `idle` tool deliberately advances a bounded number of ticks without pressing a key.

## World map and travel

The bridge builds a read-only directed world graph once per loaded game session and exposes its cache version in structured state. The harness stores first-visit days and the last observed location in `harness/state/world.json` or the isolated run state directory. Each actor and director context includes only the current location, direct exits, up to 12 nearby unvisited locations, visited counts, and the route home. The `world_map` actor tool inspects a destination route without game input, while `travel_to` executes the shortest known multi-hop route with three bounded controls per hop. Travel stops before a closed locked door and stops during execution if an event, menu, dialogue, failed transition, or health loss changes the world. Failed map exits are persisted with their bridge reason, excluded from later routes, and surfaced as unreachable destinations in the compact summary.

## Daily planning and the notebook

The harness stores a per-save notebook at `harness/state/notebook.json`, or in the isolated run state directory. On each new day, the director creates a five-to-eight item agenda with morning, midday, afternoon, and evening work. Pending work from the prior day must be carried into the new plan or dropped with a recorded reason. While reachable unvisited locations remain, the morning agenda includes a verifiable exploration item. If the agenda finishes before bedtime, the director appends two to four new items without changing completed or dropped entries. Before sleep, the director records a reflection and learned facts, and the harness carries unfinished work forward. The notebook also stores farm-use zones derived from the bridge's cached `farmLayout`; planting and tilling on the Farm are rejected outside a crops zone.

## Control boundary

`plant_seeds(seed_slot, tiles)` handles one to six distinct empty tilled tiles from `cropsNearby`. It walks to each tile, selects a seed inventory slot from the first toolbar row, aims using freshly observed zoom-corrected screen centers, and right-clicks. It verifies a live crop on that exact tile and a one-seed inventory decrement before proceeding. It stops on a failed control, world change, damage, ineffective planting, or the action cap. Each internal input counts toward `--max-actions`. It does not till or water soil.

`plant_nearest_seeds(seed_slot, count)` uses the same verified executor. It chooses distinct observed empty tiles in greedy Manhattan-distance order from the current tile. This reduces coordinate-generation and walking overhead; it is not a guaranteed shortest collision-aware tour. Exact tile objectives use `plant_seeds` through the visual controller. Planting telemetry includes each navigation, selection, and click duration. Generic clicks no longer add a one-second sleep after the bridge has already completed input and returned state.

The actor can use:

- allowlisted keyboard buttons for movement, action, tool use, menus, toolbar selection, and cancellation;
- bounded holds from 1 to 600 game ticks;
- bounded multi-step keyboard sequences and collision-aware local navigation;
- left or right click, cursor movement, drag, and bounded wheel steps;
- bounded waits against observable game-state predicates;
- bounded no-input idle periods and exact indexed question-dialogue choices;
- objective progress, opportunity, wiki search, and session-stop tools.

If a movement hold of at least 8 ticks does not change the location or exact player position, the harness reports it as blocked. It rejects the same movement at that position until the actor chooses another direction, and lists those directions in `harnessBlockedDirectionsHere`. One-tick presses are treated as facing changes and are never scored as blocked.

`navigate_to` and `go_to_location` run inside the SMAPI bridge. The bridge searches the whole current location with the player's real bounding box and the game's own collision test, then walks the path with ordinary W/A/S/D presses each tick. It stops on arrival, on a location change, when a menu or cutscene takes control, after 20 ticks without movement, or when the tick budget runs out, and reports the reason. `go_to_location` finds the nearest warp tile or door that leads to the named location; for a door it walks to the tile in front, faces it, and presses the action key.

`nearbyObjects` reports the nearest 40 obstacles, including placed objects, trees, stumps, grass, boulders, and bushes, with their tile, zoom-corrected screen center, and recommended normal tool. `cropsNearby` reports tilled soil with crop, watered, and harvest state; `npcsNearby` lists villagers and monsters; `shopItems` lists shop rows with price, stock, and screen center while a shop is open; `dialogueText` carries the current dialogue page. `navigationRows` is a 25-by-25 local passability grid computed with the same collision test. `nearbyActions`, `warps`, and `dialogueResponses` remove invisible door and menu-coordinate guessing without mutating game state.

The actor cannot use raw SMAPI console commands, debug commands, arbitrary keyboard input, shell access, or game-memory writes.

## Wiki access

`wiki_search` queries the Stardew Valley Wiki MediaWiki API. It searches likely page titles, fetches the selected pages, strips HTML, limits each extract, and caches results for the process lifetime. Queries should be concise topics such as `Inventory`, `Community Center`, or `Abigail`.

## Cost and loop limits

Bounded mode has two independent caps.

- `--max-actions` limits operations sent to the game bridge.
- `--max-decisions` limits logical actor model calls, including failed calls, wiki searches, and memory updates. Each logical call can include bounded physical retries; director calls are separate.
- `--keep-game-open` leaves a harness-launched game running after the bounded run for iterative testing. Its launcher process remains attached until the game is closed.

The defaults are 10 actions and 15 actor decisions. Director reviews add model calls at `--director-interval`, which defaults to 12 game actions, or when a new goal is needed. These are request-count controls, not a guaranteed dollar limit, because cost depends on the selected OpenRouter model and provider.

`--budget-usd` sets an optional cumulative session-cost limit using recorded actor and director decision usage. When cost reaches or exceeds the limit, bounded and continuous runs stop with `budget_reached`. The harness rejects bedtime before 20:00 unless stamina is under 30 or health is low. A stall watchdog requests a director review after 8 unchanged actor decisions, retries focus and borderless mode at 16, and stops with `stalled` at 24.

`--continuous` ignores both caps, applies bounded retry/backoff to transient failures, and keeps the loop running. `--record-video` starts 30 FPS, 1920-by-1080 H.264 capture only after a world is loaded. It captures through the Desktop Duplication API (FFmpeg `ddagrab`), which stays live in full-screen game modes where `gdigrab` froze, and encodes with `h264_nvenc`; if NVENC does not initialize, the recorder retries once with `libx264 -preset ultrafast`. Run `python -m autoplay_harness record-test --seconds 5` to record a short desktop clip and confirm the frame count and that the frames are not stale. `--video-segment-minutes` controls segment length. `--video-retention-segments` defaults to a six-file rolling buffer; `0` explicitly retains the complete raw VOD. Static pauses can be removed from a copy with FFmpeg `mpdecimate`. `--save-frames` remains an opt-in diagnostic mode and should not be used for routine continuous play.

## Local setup

1. Close Stardew Valley.
2. Run `dotnet build .\src\Autoplay.GameBridge\Autoplay.GameBridge.csproj`.
3. Run `.\harness\run-harness.ps1 bridge-test --fullscreen`.
4. Set `OPENROUTER_API_KEY` in the ignored repository-root `.env`.
5. Start with no more than five actions and eight decisions.
6. Inspect `events.jsonl`, `session-summary.json`, sparse diagnostic frames when explicitly enabled, and the retained video segments after the run.

The launcher uses `AUTOPLAY_PYTHON` when set. Otherwise, it checks the bundled Codex Python runtime, the `py` launcher, and `python`. A separate Python installation needs Python 3.11 or newer and the dependencies declared in `harness/pyproject.toml` (`dxcam`, `imageio-ffmpeg`, and Pillow).

## Remaining choices before the first paid run

The recommended smoke-run choices are:

1. Model: `minimax/minimax-m3:free`, restricted to the GMICloud FP8 endpoint.
2. Objective: `Load the existing BridgeTest save and reach the Farm exterior`.
3. Success condition: `worldReady is true, location is Farm, and playerFree is true`.
4. Limits: five game actions and eight actor decisions.

For GLM evaluation, explicitly pass `--model z-ai/glm-5.3-flash --reasoning-effort low`. For the free smoke run above, explicitly pass `--model minimax/minimax-m3:free`.
