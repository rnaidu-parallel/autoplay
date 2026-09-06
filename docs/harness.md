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

Each request sends the frame downscaled to 1280 pixels wide at JPEG quality 75, caps completion at 600 tokens, and gives up after a 150-second decision budget to bound a slow provider request. Pointer coordinates stay normalized, so the downscale does not affect click accuracy.

OpenRouter uses its OpenAI-compatible Chat Completions endpoint. Gameplay defaults to `z-ai/glm-5.3-flash`, low reasoning for actor and director, with ordered FP8 routes DeepInfra → NextBit → Baseten and fallback restricted to that allowlist. GLM supports low, high and max; medium is rejected. DeepInfra prefix caching is automatic; the client keeps fixed instructions/tools stable and sends changing quest/game information separately. GLM and MiniMax use auto tool choice with strict response validation. The free profile remains `minimax/minimax-m3:free` through GMICloud FP8 only. [Current configuration, accounting and validation limits](life-followthrough-2026-09-03.md).

## Objective stability

For official-provider comparisons, use `--model google/gemini-3.7-flash --reasoning-effort low` or `--model openai/gpt-5.6-luna --reasoning-effort low`. OpenRouter routes Gemini only to `google-ai-studio` and Luna only to `openai`, with fallback disabled and parameter support required. Both use required tool choice and retain the 600-token cap. Luna omits temperature because its endpoint does not support it. The client rejects missing or mismatched response-provider attribution before any tool can run. These are routed official upstreams, not direct Google/OpenAI API connections.

For an optional latency trial, select `--model google/gemini-2.5-flash-lite`. This profile uses only Google AI Studio through OpenRouter, with no fallback and no reasoning override (Flash-Lite defaults to thinking disabled). GLM remains the default.

`--model qwen/qwen3.8-flash --reasoning-effort low` uses only Alibaba through OpenRouter. Low is explicit because Qwen's default thinking effort is `xhigh`. Qwen also accepts medium; GLM does not. Qwen retains auto tool choice because its thinking mode does not support required tool choice. Ordinary actor actions retain the 600-token output cap; required quest reviews receive 1,200 and director reviews 2,400. Strict tool validation remains enabled.

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

The notebook keeps at most 40 lessons. Repeated auto lessons merge by stable key and increment their count; reflection lessons record deliberate changes for tomorrow. At the start of a day, the runner applies a weekly roll-up when seven days are older than yesterday. The roll-up preserves category totals and a 600-character reflection summary, then removes those daily entries. Three archived themes remain available for variety validation. Actor notebook context contains only today's compact agenda, crop-zone rectangles, and three lessons. Director context contains the agenda, yesterday's reflection, farm plan, six lessons, eight learned facts, category history, recent themes, and the latest weekly summary.

The runner estimates serialized context JSON at 3.5 characters per token, with limits of 4,500 for the actor and 8,000 for the director. These estimates exclude fixed instructions, tool schemas, and images; the report separately shows provider-reported prompt usage. When over budget, the runner removes wiki results, reduces recent events to six, reduces lessons to three, shortens agenda goals to 40 characters, limits actor nearby objects to 12, then limits nearby crops to 24. If an actor context still exceeds the cap, it removes full farm layout, collision rows, and diagnostics; the bridge controllers retain those details. It records actual cuts in `context_trimmed`. It stops the request if those cuts cannot fit the context; it never removes the objective ledger or `harnessLastResult`. Every request starts with sorted JSON blocks for the ledger, notebook, and world; changing state, recent events, counters, and the last result follow outside that cache prefix.

The prompt receives only:

- the 12 most recent relevant events;
- up to 12 compact entries in each of accomplishments, failures, learned wiki topics, and unresolved opportunities;
- a bounded ledger view containing the active objective, the last three historical objectives, four recent progress entries, and four opportunities.

Compaction is deterministic. It extracts durable entries from structured tool calls and results. It does not ask another model to summarize gameplay, so it cannot silently rewrite objectives.

## Screenshots and coordinates

Before each screenshot, the SMAPI bridge brings Stardew Valley to the foreground. If Windows denies foreground transfer, the capture layer locates the Stardew window and temporarily keeps it above competing windows for the autonomous session. Python captures the Stardew client rectangle with DXGI. The WinRT fallback was removed after it returned an old title splash during live gameplay. Use Windowed mode for the recorded stream workflow; see [repair and reattach](operator-controls.md#repair-python-during-a-recording-or-stream). Window borders, the desktop, and other applications are excluded. Cleanup removes the temporary topmost state.

Pointer tools use normalized coordinates from `0` to `1` and must echo the current `frame_id`. The harness converts those values into the exact client-pixel coordinates expected by SMAPI. A stale frame ID aborts the pointer action. This works in both windowed and full-screen modes.

On a new game launch, the harness waits through the publisher splash and title animation, selects the Load menu, and opens the first existing save before recording or making a gameplay model request. Wrong title submenus are escaped deterministically.

Before launching Stardew, the supervisor sets its startup display preference to windowed because switching to full screen during the startup logo can stall that animation. When game launch is enabled, the autonomous runner requests borderless full-screen mode before its first model decision. A rejected display-mode change stops the run before any model request. Normal cleanup restores windowed mode. For recording, start SMAPI separately in Windowed mode and use `--no-launch-game` or `--attach`; these preserve the chosen display mode at startup, reconnect and stall recovery. Both leave the game open on exit and attempt a normal menu pause. Use `--attach` for a mid-day Python replacement; it reads current shared state without restoring a checkpoint.

The game clock and animations keep running between model decisions. The bridge does not write `Game1.paused`; native menus retain their normal pause behavior. Before game input, the actor rechecks the scene and rejects a stale decision after a day, location, menu, control-availability, or health change. Pointer actions also recheck player position, viewport, and nearby NPCs. The `idle` tool waits for bounded ticks without pressing a key. Windows application activation recovers a visible but inactive game window when ordinary foreground activation fails.

During bridge control, the game uses its own pointer and hides the operating-system cursor. The bridge restores
the prior hardware-cursor and visibility settings on `stop` or a bridge error. `systemCursorVisible` reports
the game framework's setting. Windows may still mark the native cursor as showing when its image is fully
transparent; the live check renders that image to verify it has no visible pixels. The title-screen check also
opened Load with normal pointer input and retained the game's brown pointer. No save was opened or model called.

## World map and travel

The bridge builds a read-only directed world graph once per loaded game session and exposes its cache version in structured state. The harness stores first-visit days and the last observed location in `harness/state/world.json` or the isolated run state directory. Each actor and director context includes only the current location, direct exits, up to 12 nearby unvisited locations, visited counts, and the route home. The `world_map` actor tool inspects a destination route without game input, while `travel_to` executes the shortest known multi-hop route with three bounded controls per hop. Travel stops before a closed locked door and stops during execution if an event, menu, dialogue, failed transition, or health loss changes the world. Failed map exits are persisted with their bridge reason, excluded from later routes, and surfaced as unreachable destinations in the compact summary.

Locked-door hours are enforced for both direct `go_to_location` hops and `travel_to` routes. The compact world summary lists currently closed direct exits with their opening times.

## Daily planning and the notebook

Both actor modes and the daily planner share a farmer identity: a resident of Pelican Town who cares for the
farm, notices unfamiliar people and objects, and develops tastes through experience. Brief safe nearby
interactions can fit the current task; larger detours become future plans. Planning balances responsibilities,
remembered preferences, and curiosity. Existing control, clock, retry, and objective-verification rules still apply.

The actor can use `remember_interaction` after a real attempted action. Each journal entry records the subject,
interaction, location, calendar/time, outcome (`possible`, `unavailable`, or `unknown`), preference (`liked`,
`disliked`, `neutral`, or `undecided`), and a short observation/reaction note. The harness attaches the source
tool result, observed changes, dialogue excerpt, and run/step reference. Availability is the farmer's
interpretation of that evidence; preferences are the character's subjective reactions. A completed control
alone does not establish that the intended interaction happened. Unavailable or uncertain interactions need
an undecided preference; travel alone cannot prove an object is unusable. Each attempted action can be
remembered once. Failed memory validation sends no game input and leaves the attempt available for correction.

Entries merge by normalized location, subject, and interaction, keeping the latest experience, encounter count,
and previous report. They persist in `notebook.json` across restarts and weekly summaries. Nothing is invented
to initialize tastes. Actor prompts retrieve up to four entries and director prompts up to six, prioritizing
visible subjects and the current location while retaining other recent experiences for future planning. Context
limits can omit lower-priority retrieved entries without deleting saved memories. Later encounters can revise a
preference; a shop being closed at one hour does not make it permanently unavailable or disliked.

The harness stores a per-save notebook at `harness/state/notebook.json`, or in the isolated run state directory. On each new day, the director creates a five-to-eight item agenda with morning, midday, afternoon, and evening work. Every item has one category: farming, clearing, exploring, social, shopping, fishing, mining, foraging, crafting, event, or home. Morning themes cannot repeat any of the last three days. The plan adds at least two categories outside yesterday's top two, has no more than three items in one category, and has at most one refill item in yesterday's dominant category. Pending work from the prior day must be carried into the new plan or dropped with a recorded reason. While reachable unvisited locations remain, the morning agenda includes a verifiable exploration item. If the agenda finishes before bedtime, the director appends two to four new items without changing completed or dropped entries. Before sleep, the director records a reflection and learned facts, and the harness carries unfinished work forward. The notebook also stores farm-use zones derived from the bridge's cached `farmLayout`; planting and tilling on the Farm are rejected outside a crops zone.

## Reflexes

Some things a person does without deliberating. The harness does them locally, with no model call, at
the start of each actor step:

- **Watching a scene.** A non-question dialogue box or a cutscene that does not allow movement is played
  through by the bridge's `watch_dialogue` command at reading pace (each page is advanced ~1.5 s after it
  has fully typed). A question, a different menu, or the end of the scene returns control. The transcript
  reaches the model afterwards in `life.recentText`, so the character reacts once to the whole scene
  instead of once per page.
- **Greeting in passing.** A villager within six tiles who has not been spoken to today is approached and
  greeted (the existing bounded approach controller), then the conversation is watched through. Each
  person is tried at most once per 90 seconds and never after 22:00 or below half health.
- **Bedtime.** From 23:30 the actor is offered only `go_home_and_sleep`, `close_menu` and `stop_session`
  (it still writes the narration). From 1:00 AM the harness walks home and sleeps without a decision.
  `go_home_and_sleep` works from any location by travelling home first.
- **Changing the scene.** See the stall rules under cost and loop limits.

Journal and mail are information, not gates: `life.quests` carries the real journal, `life.unreadMail`
and `notebook.attention` mention unread letters, and no tool list is ever narrowed to force them. A
decision is rejected as stale only when the change would make that specific action wrong: pointer
actions care about geometry and menus, menu clicks about the entry list, key presses about the kind of
scene (dialogue versus world) and pending questions, and skills about place, day and cutscenes. A
dialogue page that advanced while the model was thinking is not staleness.

## Capture failures

The harness holds the display awake for the whole run (`SetThreadExecutionState`), because Windows
switching the display off kills desktop duplication; on 7 September a run froze inside a screen grab
about ten minutes after the last human input. Each grab now has a five-second deadline. If capture
keeps failing, the run goes blind rather than stopping: decisions use structured state and the
state-first tool set (no pointer tools) until a frame comes back, with `capture_unavailable` and
`capture_restored` in the event log.

## Navigation and pockets

The bridge tests tile walkability with a canonical player box centred on the tile, chooses the nearest
*reachable* exit to a destination (a flood fill from the player, cached per position), reports
`no_walkable_path_to_exit` when exits exist but none is reachable, and lists every exit of the current
location in `exits` with `reachable` and `distance`. The harness records those observations per
(location, entered-from) pair as *pockets* in `world.json`; routes treat an exit observed unreachable from
the current entry point as unavailable for three days, so a farm entrance cut off by debris is left by
the way it was entered and the house is reached from another entrance instead of looping. Travel runs in
long segments (900 ticks) and no longer yields on every villager sighting.

## Control boundary

`plant_seeds(seed_slot, tiles)` handles one to six distinct empty tilled tiles from `cropsNearby`. It walks to each tile, selects a seed inventory slot from the first toolbar row, aims using freshly observed zoom-corrected screen centers, and right-clicks. It verifies a live crop on that exact tile and a one-seed inventory decrement before proceeding. It stops on a failed control, world change, damage, ineffective planting, or the action cap. Each internal input counts toward `--max-actions`. It does not till or water soil.

`plant_nearest_seeds(seed_slot, count)` uses the same verified executor. It chooses distinct observed empty tiles in greedy Manhattan-distance order from the current tile. This reduces coordinate-generation and walking overhead; it is not a guaranteed shortest collision-aware tour. Exact tile objectives use `plant_seeds` through the visual controller. Planting telemetry includes each navigation, selection, and click duration. Generic clicks no longer add a one-second sleep after the bridge has already completed input and returned state.

`clear_debris(targets)` handles one to six distinct wood, stone, or fiber obstacles from `nearbyObjects`. It selects the recommended Axe, Pickaxe, or Scythe, including upgraded tools, then stands beside, faces, and swings at each target until the object disappears.

The skill reports cleared tiles and inventory gains, and stops on low stamina, damage, world changes, action limits, or per-target swing limits. It performs one forced re-face after an ineffective swing before retrying.

The actor can use:

- allowlisted keyboard buttons for movement, action, tool use, menus, toolbar selection, and cancellation;
- bounded holds from 1 to 600 game ticks;
- bounded multi-step keyboard sequences and collision-aware local navigation;
- left or right click, cursor movement, and bounded wheel steps (no drag: menu items are moved by the inventory commands below);
- `close_menu`, which returns a cursor-held item to the bag before closing, and `inventory_move`, `inventory_trash`, and `ship_item`, which act on the bag directly through the game's own item code (the bridge also has `inventory_drop`, not offered to the actor because a dropped item is picked straight back up);
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
- `--keep-game-open` leaves a harness-launched game running after the run for iterative testing. When the player is free, cleanup opens the normal game menu for handoff. Its launcher process remains attached until the game is closed. Verify a nightly save before closing it.

The defaults are 10 actions and 15 actor decisions. Director reviews add model calls at `--director-interval`, which defaults to 12 game actions, or when a new goal is needed. These are request-count controls, not a guaranteed dollar limit, because cost depends on the selected OpenRouter model and provider.

`--budget-usd` sets an optional cumulative session-cost limit using recorded actor and director decision usage. When cost reaches or exceeds the limit, bounded and continuous runs stop with `budget_reached`. The harness rejects bedtime before 20:00 unless stamina is under 30 or health is low. A stall watchdog requests a director review after 8 unchanged actor decisions and retries focus and borderless mode at 16. At 24, a bounded run stops with `stalled`; continuous (live) play never stops on a harness-judged stall. Instead the harness changes the scene: after 20:00 it forces bedtime, otherwise it abandons the current intention and tells the director to choose a clearly different activity. An activity drought (45 s or 8 decisions without new game evidence) asks for a replan; a long one (150 s or 30 decisions) triggers the same scene change.

Continuous play also defers an objective after three failed attempts at the same target or six decisions without progress. Clock ticks and changed narration do not reset this count; advancing story dialogue does. The director receives the failure evidence and must choose another task. Deferred destinations stay excluded for the day, except for a necessary bedtime return home. A blocked bedtime return records `home_return_blocked` and resets the retry counters so a rerouted attempt is not rejected as a repeat; it does not stop the run. Three rejected proposals for the same objective condition stop with `director_repeated_invalid_objective`; agenda reminders preserve the rejection feedback.

`--continuous` ignores both caps, applies bounded retry/backoff to transient failures, and keeps the loop running. `--record-video` starts 30 FPS, 1920-by-1080 H.264 capture only after a world is loaded. It captures through the Desktop Duplication API (FFmpeg `ddagrab`), which stays live in full-screen game modes where `gdigrab` froze, and encodes with `h264_nvenc`; if NVENC does not initialize, the recorder retries once with `libx264 -preset ultrafast`. Run `python -m autoplay_harness record-test --seconds 5` to record a short desktop clip and confirm the frame count and that the frames are not stale. `--video-segment-minutes` controls segment length. `--video-retention-segments` defaults to a six-file rolling buffer; `0` explicitly retains the complete raw VOD. Static pauses can be removed from a copy with FFmpeg `mpdecimate`. `--save-frames` remains an opt-in diagnostic mode and should not be used for routine continuous play.

`--max-minutes` sets a wall-clock limit for bounded and continuous runs. The run stops cleanly with `time_limit_reached` when the limit is reached.

## Running forever

Use `--forever` to run each session under an outer restart supervisor. The objective arguments are optional. If the persistent ledger has no active objective, the first run starts with `Step outside to start the day.` and the success condition `location is Farm`.

```powershell
.\harness\run-harness.ps1 run `
  --forever `
  --continuous `
  --max-minutes 180 `
  --budget-usd 5 `
  --record-video
```

The supervisor starts a fresh harness and run ID after `stalled`, `recovery_exhausted:*`, `time_limit_reached`, other session-stop reasons, or an uncaught exception. `--max-minutes` applies to each inner run. The persistent objective ledger, notebook, world state, and daily cost ledger remain in `harness/state/` across restarts. A model `stop_session` request is rejected in forever mode unless its reason contains `unsafe` or `unrecoverable`.

In forever mode, `--budget-usd` is a daily local-time budget. Each inner run receives only the unspent balance. When the daily budget is reached, the supervisor records `sleeping_budget`, waits until local midnight, resets the ledger for the new date, and continues. A non-supervised run still stops at `budget_reached`.

Create `harness/state/STOP` to stop the supervisor cleanly. Remove the file before the next forever start. The current state is in `harness/state/forever_status.json`, cumulative daily usage is in `harness/state/cost_ledger.json`, and exception tracebacks are appended to `harness/state/forever.log`. During a restart, the previous run temporarily contains `harness/runs/<run-id>/overlay/restarting`. The supervisor does not restart after `stop_requested`; a daily `budget_reached` sleeps until midnight instead of immediately restarting.

## Local setup

1. Close Stardew Valley.
2. Run `dotnet build .\src\Autoplay.GameBridge\Autoplay.GameBridge.csproj`.
3. Run `.\harness\run-harness.ps1 bridge-test --fullscreen`.
4. Set `OPENROUTER_API_KEY` in the ignored repository-root `.env`.
5. Start with no more than five actions and eight decisions.
6. Inspect `events.jsonl`, `session-summary.json`, sparse diagnostic frames when explicitly enabled, and the retained video segments after the run.

The launcher uses `AUTOPLAY_PYTHON` when set. Otherwise, it checks the bundled Codex Python runtime, the `py` launcher, and `python`. A separate Python installation needs Python 3.11 or newer and the dependencies declared in `harness/pyproject.toml` (`dxcam`, `imageio-ffmpeg`, Pillow, and `websockets`).

## Remaining choices before the first paid run

The recommended smoke-run choices are:

1. Model: `minimax/minimax-m3:free`, restricted to the GMICloud FP8 endpoint.
2. Objective: `Load the existing BridgeTest save and reach the Farm exterior`.
3. Success condition: `worldReady is true, location is Farm, and playerFree is true`.
4. Limits: five game actions and eight actor decisions.

For GLM evaluation, explicitly pass `--model z-ai/glm-5.3-flash --reasoning-effort low`. For the free smoke run above, explicitly pass `--model minimax/minimax-m3:free`.

Repeated failures have a separate short limit: three failed attempts at the same target (including alternating travel tools), or six decisions without progress, defer the current objective. Clock ticks alone do not count as progress; completed intentional waits and useful tool work are allowed. The ledger records the objective as blocked, never completed. Its agenda item stays deferred for the rest of the day and carries forward for the next day's planning. The director must choose another remaining task; rewording the same destination cannot reopen it that day.
