# Autoplay

Autoplay is a local autonomous Stardew Valley harness. A C# SMAPI mod exposes bounded game observations and controls through a Windows named pipe. A Python tool loop sends a current game screenshot and structured state to a vision-capable model through OpenRouter.

The harness has completed a continuous full-screen run with MiniMax M3's free OpenRouter route. It loaded `BridgeTest` without model-driven title clicks, completed a leave-and-return objective against structured state, assigned the next objective, exited again, cleared weeds, collected Fiber, and kept playing while segmented video recording ran.

## What is implemented

- Game-only client-area screenshots in windowed or full-screen mode.
- Structured SMAPI state for the player, world, menu, time, inventory, nearby objects, map actions, local collision grid, dialogue responses, cursor, events, and minigames.
- Bounded keyboard, cursor, click, drag, scroll, wait, idle, dialogue-choice, navigation, and stop operations.
- Bridge-side `navigate_to` and `go_to_location` that plan with the game's real collision rules across the whole location and still emit ordinary W/A/S/D controls and the action key.
- A cached world graph, persistent visited-location tracking, compact exploration context, and verified multi-hop `travel_to` routing.
- A per-save daily notebook with agenda carry-over, evening reflection, durable learned facts, and enforced farm-use zones.
- Structured crops, obstacles, NPCs, shop rows, dialogue text, and inventory counts, so objectives can require real work such as `plantedCrops >= 15` or `inventory.Parsnip Seeds >= 20`.
- A compact state-first actor for verified navigation/planting skills, with full visual controls on demand, and a slower director for global priorities.
- A persistent objective ledger that prevents the agent from replacing an unfinished objective.
- Harness-verified objective completion against explicit structured game-state predicates; the director only sets the next objective.
- Movement and repeated-action stagnation guards.
- Append-only telemetry plus bounded deterministic memory compaction.
- A localhost OBS browser-source overlay for the live plan, current objective, actions, and session stats.
- A Stardew Valley Wiki search tool with in-memory result caching.
- Stable OpenRouter system prompts, tool definitions, and a sticky `session_id` for prompt-cache affinity.
- Deterministic first-save loading, full-screen simulation ownership, recoverable continuous mode, and segmented 1080p recording.
- An outer `--forever` supervisor with daily cost accounting, clean stop-file control, restart status, and per-run wall-clock limits.

See [docs/harness.md](docs/harness.md) for the architecture and operating procedure. See [docs/control-surface.md](docs/control-surface.md) for runtime control evidence.

## Verify without spending OpenRouter credits

Close Stardew Valley before building the mod.

```powershell
dotnet build .\src\Autoplay.GameBridge\Autoplay.GameBridge.csproj
.\harness\run-harness.ps1 bridge-test
.\harness\run-harness.ps1 bridge-test --fullscreen
.\harness\run-harness.ps1 wiki-test Inventory
```

`bridge-test` starts SMAPI, captures a game-only frame, exercises every bounded input primitive at harmless title-screen coordinates, and stops the game process that it started. `--fullscreen` waits for the stable title menu, temporarily selects Stardew's true full-screen mode, asserts primary-display dimensions, runs the same controls, and restores the original display mode.

## Configure OpenRouter

Gameplay defaults to `openai/gpt-5.6-luna` with `--reasoning-effort low`. Requests use only OpenAI through OpenRouter, with fallback disabled and provider attribution checked before game control. This is a paid route.

For free local iteration, pass `--model minimax/minimax-m3:free`. That profile remains restricted to `gmicloud/fp8` with fallback disabled. The launcher never broadens either provider allowlist.

Prompt caching uses stable system/tool prefixes and one sticky `session_id` per actor/director session. Z.AI applies caching automatically. A changing screenshot, game state, or recent-event window must still be processed; the reported cache hit rate is the fraction of input tokens cached, not the fraction of calls served from cache.

For a bounded speed comparison, `--model google/gemini-2.5-flash-lite` selects only Google AI Studio, without fallback or a thinking override.

The Gemini trial uses `--model google/gemini-3.7-flash --reasoning-effort low`, restricted to Google AI Studio through OpenRouter, with fallback disabled. Both Gemini 3.7 Flash and the default Luna profile reject responses with a different or missing provider attribution before game control.

`--model qwen/qwen3.8-flash --reasoning-effort low` selects the Alibaba route only, with no fallback. Qwen also supports `medium`; GLM rejects that setting. Use `--isolated-state` for a fresh per-run objective ledger during comparisons. This does not reset the game save. See [gameplay evaluation](docs/evaluation.md) for current checks and missing acceptance coverage.

Routine director reviews run in the background at the configured action interval. Existing objectives do not trigger duplicate startup or location-change reviews; goal selection still waits when there is no objective. `plant_nearest_seeds` accepts a seed slot and count, chooses nearby observed empty soil locally, and verifies up to six plantings using normal controls. `plant_seeds` retains exact tile selection. Per-attempt diagnostics distinguish retries, truncation, invalid tool responses, and request size.

The default `--actor-mode state-first` sends compact structured state and six skill tools for free movement outside menus, events, minigames, and nearby NPCs. `inspect_scene` requests a fresh visual decision with all tools. Use `--actor-mode visual` for the previous always-image path. Luna uses an explicit cache boundary after stable instructions/tools/objective and a stable prefix key; changing game state and frames are excluded from cache writes. Reports separate actor/director cache usage, later-call hit rate, mean model latency, and complete actor cycles. See [latency investigation](artifacts/latency-optimization-2026-09-02/report.md).

```powershell
OPENROUTER_API_KEY=<your key>
```

Put that line in the repository-root `.env`. The launcher loads it without printing it. Git ignores `.env`; `.env.example` documents the required field without containing a secret. An existing shell environment variable takes precedence.

## Run a bounded first session

```powershell
.\harness\run-harness.ps1 run `
  --model openai/gpt-5.6-luna `
  --reasoning-effort low `
  --objective 'Load the existing BridgeTest save and reach the Farm exterior' `
  --success-condition 'worldReady is true, location is Farm, and playerFree is true' `
  --max-actions 5 `
  --max-decisions 8 `
  --save-frames `
  --keep-game-open
```

The defaults are 10 game actions and 15 actor decisions. Increase both limits only after reviewing the first run under `harness/runs/<run-id>/`.

Autonomous runs switch Stardew Valley to true full-screen mode before the first model observation.
Use `--keep-game-open` during iterative testing to leave Stardew running after a bounded run or harness error. The launcher remains attached until the game is closed.

## Run continuously with recording

```powershell
.\harness\run-harness.ps1 run `
  --objective 'Build a productive Spring farm while preserving health, stamina, and a safe route home' `
  --success-condition 'day >= 9, worldReady is true' `
  --director-interval 24 `
  --continuous `
  --record-video `
  --video-segment-minutes 5 `
  --video-retention-segments 6 `
  --keep-game-open
```

Continuous mode ignores the bounded action and decision caps. It recovers from transient bridge, capture, and provider failures and keeps the active objective stable until its structured condition is verified. Video segments are written to `harness/runs/<run-id>/video/` after the save reaches a playable world state. Recording uses a six-segment rolling buffer by default; pass `--video-retention-segments 0` only when a complete raw VOD is intentionally required. Do not enable `--save-frames` for routine continuous play.
