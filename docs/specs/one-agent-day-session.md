# One agent, one day per session

Status 2026-09-07, revision 3: build spec. Revisions 1 and 2 were reviewed by Codex (gpt-5.6-sol);
this revision folds in both reviews. It replaces the actor/director split, the success-condition
grammar, the notebook agenda machinery and the watchdog stack with one agent, one conversation per
game day, a diary the agent owns, and a small set of physics-level circuit breakers.

## Goals

- One mind. The same persona plans, plays and reflects. Nothing decides *what to do* for it.
- Continuity inside a day. The agent continues the scene it is in instead of re-reading a briefing
  on every call, so it acts fast mid-task and thinks harder only when something went wrong.
- Curiosity and fluidity. Objectives change on convenience. Mission, event and free play are all
  legitimate objectives.
- Awareness. Mail, journal, quests, notices, calendar, weather, surroundings and the audience
  request are always visible.
- Memory the agent owns. A diary it writes, reads and searches. The harness never writes it,
  but does surface relevant excerpts automatically.
- A live stream that does not stall. Failed physical actions are stopped locally, fast, without
  the harness taking over the objective.

## Non-goals

- Cost minimization by trimming context. The chosen model makes cached context nearly free.
- Harness-verified completion. The agent decides when an objective is done; the harness may
  report a contradicting fact, never overrule.
- Compulsory daily agendas, variety quotas, or quest/exploring thread requirements.

## Model

`meta/muse-spark-1.3-contributor` via OpenRouter, reasoning effort `low` by default. Listing on
2026-09-07: 1 048 576 context; $0.10/M uncached input, $0.002/M cached input, $0.20/M output;
tools, tool_choice, reasoning_effort and image input supported. Provider pin: whatever the probe
reports as the serving provider, with fallbacks disabled.

Probe before the first day (`harness/autoplay_harness/probe.py`, results under
`broadcast/local/muse-spark-13-probe.json`): serving provider name; multi-turn tool-call fidelity
with an image in the newest turn; whether reasoning tokens count against `max_tokens`; cache-read
tokens on a growing prefix across three calls; latency at low and medium effort; truncation
behaviour. Read the contributor tier terms for data use and rate limits.

## Session = game day

- A session starts when the game day starts (or when the harness attaches or restarts mid-day)
  and ends when the day advances after sleep. The forever supervisor keeps running across days.
- **Session id** = `<saveId>:<calendarDay>`. Every event the loop writes carries it, so a
  session can be found across runs.
- The request is a conversation: one stable system message, then the day's transcript, then the
  newest observation as the final user turn. Tool calls and results are ordinary turns.
- **Transcript unit = bundle**: `[assistant tool call] [tool result] [user observation]`.
  Pruning removes or shortens whole bundles, never a single message.
- **Pruning is deterministic and never touches the agent's words.** In every bundle older than
  the newest one, the observation is replaced by one line (`Spring 3 · 9:40 AM · Farm · result:
  completed`), and tool results older than 4 bundles are cut to status and reason; results never
  carry the state snapshot (the next observation has it) and are capped at 4 000 characters, live
  and on rebuild alike, so a rebuilt day renders the same size as a live one. `say`,
  `diary`, tool names and arguments stay verbatim. Above 120 000 estimated tokens (4 chars per
  token) the oldest bundles are dropped; a dropped bundle's diary text is still on disk.
- **Reflexes as turns.** A watched scene or a greeting with no model call becomes a user turn:
  `(While you stood there: Robin said "…")`. The agent reacts once.
- **Rebuild on restart.** The transcript is a projection of the session's events: for each
  session id, read `events.jsonl` of every run under `harness/runs/`, keep events with that id,
  order by `(at, seq)`, drop duplicates by `(run_id, seq)`. Events used: `observation`,
  `agent_decision`, `action_started`, `tool_result`, `reflex`, `diary_entry`, `objective_set`,
  `session_note`. Partial trailing JSONL lines are ignored.
- **At most once, never replayed.** Every decision carries the provider's `call_id`. Before any
  game input or state-file write, the loop writes `action_started`; after, `tool_result`. On
  rebuild, a decision with no result becomes a synthetic result `{"status": "unknown", "reason":
  "the harness restarted before the result was recorded; the state below is current"}`. Nothing
  is replayed: the agent observes the current state and chooses again. State files
  (`objectives.json`, the diary, `notebook.json`) are the truth for their own content and are
  written atomically; the transcript is reconstructed from events and may lag them by one action,
  which the newest observation corrects.
- **Canonical speech.** Only `say` and `diary` are the agent's words in the transcript. Any other
  assistant text the model returns is discarded (recorded truncated in telemetry only).
- **Event schema** (every event the loop writes): `run_id`, `session_id`, `seq` (session-local,
  monotonic within a run), `call_id` where a decision exists. Ordering on rebuild is `(at, run_id,
  seq)`; duplicates are dropped by `(run_id, type, seq, call_id)`.
- **Prompt version.** `agent_session_started` records a hash of the system prompt and tool schema.
  A rebuilt session under a different hash gets one user note: `(Your instructions changed while
  you were away.)`
- **Save scoping.** The objective record carries `saveId`; a mismatch at session start replaces it
  with `free: get my bearings`. The diary lives under `harness/state/diary/<saveId>/`.
- **Counters after restart** (seen facts, decisions since evidence, failed targets) start fresh.
  This is accepted: restarts are rare and the enforced stall change re-arms within 12 decisions.
- Removed: the stateless packet, the 3.5-char token estimate, the trim order, `context_trimmed`.

## Perception

- Every observation carries the compact structured snapshot and the screenshot. One tool set,
  always. Blind mode stays: when capture fails, the observation says so and pointer tools are
  refused with a clear reason.
- Reflexes stay unchanged: dialogue and cutscenes watched at reading pace; greeting in passing.
- Verified skills stay unchanged: plant, water, till, clear, check mail, look at, travel, go
  home and sleep. They are hands, not mind.

## Depth by situation

Arrivals, menus and dialogue are fast situations, not deep ones. The loop sets effort and output
per call:

| Situation | Effort | Output cap | Call timeout |
| --- | --- | --- | --- |
| Default | low | 2 000 | 60 s |
| Last result failed (see the status table); first call of a day; stall advice active | medium | 6 000 | 90 s |
| Agent set `think_harder: true` on the previous call (applies to the next call, once) | high | 12 000 | 120 s |

Every call's timeout is also capped at the remaining stall deadline, so the harness can always
intervene on time. Caps are ceilings. `finish_reason: length` is printed on the step line and
counted in the report. A model timeout is recorded as `model_error` and the next call runs at the
same depth.

Status table. `completed`, `recorded`, `yielded` and `interrupted` are not failures.
`blocked`, `rejected`, `error`, `timeout` and `unknown` are failures: they select medium depth
and count toward the same-target breaker (except `unknown`). The identical-action breaker and
the stall counter apply to every decision regardless of status.

## Objective

Exactly one objective exists at all times, stored in `harness/state/objectives.json` under
`active` (the overlay reads `active.goal`). Schema:

```
id, kind: mission | event | free, goal (title, his words, ≤160), why (≤400),
done_when (plain language, ≤300), source (optional: quest:<id> | event:<name> | letter:<id> |
chat:<id> | operator), check (optional structured comparison), started {day, time},
```

- One tool, `set_objective`, replaces the current objective atomically. It carries
  `previous_outcome: completed | abandoned | interrupted` and `previous_note` (≤200). There is
  never a gap. The first objective of a fresh save is set by the loop: `free: get my bearings`.
- The last three objectives with their outcomes and notes are always in the newest observation.
  Changing your mind is fine; changing it back and forth is visible. No gate.
- Optional `check`, same parser as today. The observation reports `checkHolds: true|false`.
  If the agent marks an objective `completed` while its check is false, the observation carries
  one line: `You marked "<goal>" completed, but its check was false at the time.` and the event
  log records `objective_claim_disputed`. Nothing is rejected.
- Setting an objective does **not** reset the stall clock, the failed-target counters or the
  identical-action memory. Only game evidence does.
- The bedtime entry: a `diary` field on `go_home_and_sleep`, or `diary_write(kind="bedtime")`,
  is stored as kind `bedtime`. A forced or entry-less sleep leaves the day's last entry as the
  carry-over.
- Removed: success-condition requirements, auto-completion, deferral counters, the
  repeated-invalid-objective stop, `block_objective`, `continue_objective`, director feedback.

## Diary

- Starts each morning with the last entry of the previous day (his bedtime entry when it exists,
  otherwise his last entry of that day).
- Writing: `diary_write(text ≤1500)` for a proper entry, plus an optional `diary` field (≤300)
  on every tool call so he can jot while acting. Both are turns in the transcript.
- Reading: today's entries are already in the transcript. `diary_search(query, days=7)`
  returns up to 6 dated excerpts (≤240 chars each) from earlier days by token overlap, most
  recent first. `diary_read(day)` returns one day in full (≤6 000 chars).
- **Automatic retrieval.** Each observation carries up to 3 excerpts from earlier days that
  match the current location, a nearby villager's name, or a word from the objective title,
  so he does not need to know he has forgotten something.
- Storage: `harness/state/diary/<saveId>/<calendarDay>.jsonl`, one entry per line:
  `{day, time, location, text, kind: entry | note | bedtime}`. Text is his; metadata is not.
- Replaces: notebook lessons, reflection, learned facts, agenda, `remember_interaction`.
  The notebook keeps `life` (notices, quests, texts) and `farm_plan` (zones from `farmLayout`).
- `diary_write`, `diary_search` and `diary_read` are decisions like any other; the clock runs.
  The per-action field is free.

## Awareness

The newest observation always includes, compactly: quests with objectives and days left, unread
mail and last letters, pending notices, recent dialogue text, calendar (season, day, weekday,
upcoming festivals from the wiki cache), weather, opening hours for closed direct exits, exits and
route home, curiosities nearby, the audience request if any, operator guidance if any, the last
three objectives, the progress line, and the diary excerpts.

## Circuit breakers (physics, not judgment)

These reject an action and say why. They never change the objective.

1. **Schema, stale frame id, scene changed while thinking.** As today.
2. **Identical action.** Same tool and arguments (ignoring `say`, `diary`, `think_harder`,
   `frame_id`, and the clock except for explicit waits) as the previous call with unchanged
   observable state → rejected. Observable state = location, pixel position, menu, dialogue text,
   held tool, stamina, health, day and the inventory (slot, item, stack, quality), as in the
   existing fingerprint.
3. **Blocked direction at this tile.** A movement that did not move is rejected at the same
   pixel until position or location changes.
4. **Same target.** `travel_to` and `go_to_location` toward the same destination, or the same
   skill on the same arguments, count as one target per location. After 3 failures (blocked,
   rejected, error, timeout) the target is rejected until target-relevant state changes: the
   location, the day, the inventory counts, or the world map's blocked paths and unreachable
   edges. Unrelated evidence does not clear it. A bedtime return is exempt.

## Stall handling

"New game evidence" is a fact never seen today from this set: location, inventory counts, money,
seeds sown, planted, watered, tilled, harvestable crops, quest revision, mail count, save count,
event id and phase, shipping bin count, a villager newly talked to. Clock, position, stamina, menu
and dialogue text are not evidence. Setting an objective is not evidence.

- The observation always carries a progress line: decisions and seconds since the last new
  evidence, and the last five actions with results.
- At 8 decisions without evidence the line gains a sentence asking what he will do differently,
  and the next call runs at medium depth.
- At 12 completed decisions or 150 elapsed seconds without evidence, whichever comes first, the
  harness changes the scene: after 20:00 or under 30 stamina it sets the objective to going home
  to sleep; otherwise it replaces the objective with `free: do something clearly different,
  somewhere else` with outcome `interrupted` and note `stalled`, clears local retry state, and
  records `scene_changed_by_harness`. Model calls are capped at the remaining deadline so this
  cannot be delayed by a slow call. This is the only harness-driven objective change and the
  only one an acceptance day may not show.

## Audience and operator

- Chat stays as today up to the operator inbox: filter, intent extraction (same model), vote
  table, one demand per game day, shadow or bind. The loop consumes `audience` commands into the
  notebook slot exactly as now.
- The observation shows `audience: {id, goal, support, status}` while a demand is pending or
  bound. He may take it with `set_objective(source="chat:<id>")` → status `bound`; finishing
  that objective `completed` → `done`; `abandoned` or `interrupted` → `failed`. At day end a
  pending demand → `missed` and a bound one → `failed` (existing expiry). Overlay and chat
  replies work unchanged.
- Operator `steer` guidance appears in the observation as `operator.guidance` for the next 6
  decisions or until the objective changes, whichever comes first. `finish_save` and `hold`
  behave as today.

## Tools

Kept: press, hold, control_sequence, move_cursor, click, scroll, open_menu_tab,
click_menu_entry, close_menu, inventory_move, inventory_trash, ship_item, wait, idle,
choose_dialogue_response, navigate_to, go_to_location, travel_to, world_map, plant_seeds,
plant_nearest_seeds, water_crops, till_tiles, clear_debris, check_mail, look_at,
go_home_and_sleep, read_life_text, wiki_search, search_history, update_farm_plan, stop_session.
Added: set_objective, diary_write, diary_search, diary_read.
Removed: objective_progress, change_objective, pursue_interest, consider_interest,
record_opportunity, remember_interaction, inspect_scene, plan_day, reflect, and all director tools.
Common fields on every tool: `say` (≤320, required), `diary` (≤300, optional), `think_harder`
(boolean, optional).

## Prompt

One system message: `FARMER_IDENTITY`, a `GAME_BRIEF` rewritten for the transcript model, the
diary and the objective tool, and one short tail. Situational sentences (morning, bedtime, stall
advice, disputed claim, blind mode) go in the newest user turn. System text never changes within
a run.

## Overlay

`objective.goal` is the focus. Thoughts remain `say`. No overlay changes are required.

## Events

New: `agent_observation`, `agent_note`, `action_started`, `objective_set`,
`objective_claim_disputed`, `diary_entry`, `depth_selected`, `transcript_rebuilt`,
`scene_changed_by_harness`, `circuit_breaker`. The decision event keeps the name
`actor_decision` (with `session_id`, `seq`, `call_id`) so the overlay, report and cost
accounting work unchanged. Kept: `observation`, `tool_result`,
`model_request`, `model_error`, `operator_command`, `audience_*`, `session_started`,
`session_stopped`, capture and recording events. Not written by this loop: director events,
`context_trimmed`, `stall_*`, `activity_*`, `objective_deferred`.

## Status (2026-09-07, evening)

Built behind `--agent` (`harness/autoplay_harness/agent.py`, `session.py`, `diary.py`,
`agent_tools.py`, `agent_prompt.py`; 373 tests pass in `harness/.venv`). A four-minute live smoke
on the existing Carbon save with `--isolated-state` and `openai/gpt-5.6-luna` completed ten
decisions with no errors: mail read, map consulted, ten diary notes, no breakers, no truncation,
$0.026. The Muse Spark probe is blocked: OpenRouter returns 403 until the account confirms the
18+ attestation at https://openrouter.ai/settings/preferences. The two-day run also needs a fresh
save created by hand (the bridge cannot type a farmer name on the character screen).

## Two-day run (2026-09-07, save Carbon_448479998, Muse Spark 1.3 Contributor, effort low)

Days Spring 2 and 3, three processes (two deliberate mid-day restarts). 14 of 15 acceptance checks
pass (`validate_days`). Both days ended in a verified sleep with a bedtime diary entry written by
him; day 3's first observation carried day 2's entry and automatic excerpts fired. Day 2: 24
decisions, nine parsnips planted and watered, Willy's rod collected, $0.03. Day 3: 41 decisions,
$0.15, rebuilt twice with no duplicated action. No breakers fired, no truncation, no model errors,
no oscillation. Cache hit 46% and 31%; p50 latency 6 to 7 s, p90 12 to 14 s.

The failed check: two harness scene changes on day 3. The first was a false positive at the
original 90-second clock while he wandered rainy Town waiting for Pierre's (fixed to 150 s). The
second came after twelve `idle`/`wait` decisions fishing without a bite: advice fired at eight,
the change at twelve. Waiting on a line produces no evidence in the fact set, so patient fishing
reads as a stall. Follow-up: do not count completed intentional waits (`idle`, `wait`) toward the
decision bound, as the old loop did.

## Implementation plan

1. Test discovery: lazy `dxcam` and `imageio_ffmpeg` imports; a `harness/.venv` with the
   declared dependencies is the test and run interpreter (`AUTOPLAY_PYTHON`).
2. Model probe and provider pin for Muse Spark 1.3 Contributor; client gains a transcript mode
   (`messages` list in, tool call out) alongside the existing single-turn call.
3. `diary.py` (storage, search, excerpts) and `session.py` (bundles, pruning, projection from
   events, exactly-once rule) with unit tests including synthetic crashes at every boundary.
4. `agent.py`: the loop, as a subclass of the existing harness reusing observation, reflexes,
   skills, execution dispatch, operator polling, checkpoints and telemetry. Behind `--agent`.
5. Offline replays and induced scenarios: loop, restart mid-day, blocked route, bedtime.
6. Private two-day run on a fresh save: day one is a fresh session; day two must start from the
   diary and the rebuilt state. Then compare against a two-role day.
7. Make it the default only after that run; delete the director path afterwards.

## Acceptance

- Two consecutive game days from morning to verified save on a fresh save, with the second
  morning's first observation carrying the first night's diary entry.
- One controlled mid-day restart with no duplicated action and a correct rebuilt transcript.
- No exact or same-target failed action attempted more than three times without new evidence.
- Recovery from an induced loop within 14 decisions and 120 seconds.
- No objective vacuum at any point; every replacement atomic.
- Diary text from day one demonstrably present in a day-two observation.
- Zero harness scene changes in an ordinary clean day; the backstop tested separately.
- Cost, latency per depth, truncation count and cache hit rate reported from provider usage.
- Human read of the narration for repetition and correspondence with executed actions.
