# Third stream: seventeen hours, forty-one findings — 9 September

A complete inventory of everything that went wrong on the third public stream, with the
evidence for each finding and its status. Earlier reports:
[6 September](root-cause-2026-09-06.md), [7 September](root-cause-2026-09-07.md).

## The session

Twitch and Kick ran from 2026-09-08 17:10 UTC to 2026-09-09 10:10 UTC, stopped on the
operator's call. Three harness runs, one deliberate game restart to deploy a bridge fix.

| | `287a55bc` | `30a69fdf` | `1335654a` | total |
| --- | --- | --- | --- | --- |
| window (UTC) | 16:59–17:39 | 17:46–23:34 | 23:35–10:06 | 17 h 07 m |
| start mode | `session.ps1 start` | `run --attach` | `run --forever` | |
| decisions | 112 | 1260 | 1818 | 3190 |
| cost | $0.18 | $1.44 | $3.92 | $5.54 |
| failed tool results | 43 (38 %) | 283 (22 %) | 310 (17 %) | 636 (20 %) |
| `stall_advice` | 7 | 63 | 150 | 220 |
| model errors | 1 | 4 | 12 | 17 |
| operator steers | 3 | 15 | 12 | 30 |
| in-game days | 3 | 22 | 25 | ~48 distinct |

Money went from 59 g to 2229 g. Seven pass-outs cost 563 g: summer 17→18 (−70),
18→19 (−63), 19→20 (−56), 20→21 (−51), 22→23 (−78), fall 3→4 (−129), fall 5→6 (−116).
Two of those, summer 17 in the Mountain and summer 20 in Town, were not noticed live.

One in five actions failed. A fifth of all decisions were spent on rejected, blocked or
interrupted results, and the rate barely improved across the three runs.

## Shipped during the stream

Four commits, 404 tests.

- `8f6e19f` — chat answers each message once, on the viewer's platform; audience requests
  expire on the clock.
- `61c9811` — an unaddressed line goes only where people have been talking.
- `f2ca1ef` — a request's outcome is told only where it was asked.
- `b19b2a7` — the cursor points at the faced tile before an action press; unanswered chat
  lines stay in view; reworded repeats are not relayed; relay gap 20 s → 5 s; the session
  script starts the chat agent on a ten-second window.

The bridge change in `b19b2a7` was built and deployed mid-stream at 05:04 IST, which cost
about ten minutes of game downtime and no broadcast interruption.

## Perception: what the model cannot see

This is the largest class and the cause of most lost time. In every case the model behaved
sensibly given what the state told it, and the state was silent about the thing that mattered.

**1. Mine ladders are absent from the underground state.** `exits` and `nearbyActions` are
both empty on `UndergroundMine*`, so the model has no tile for the ladder and steers from the
screenshot alone. It misjudged by one tile on five nights. Roughly sixteen `no_walkable_path`
results in run `30a69fdf` were attempts to path onto the ladder tile itself, which the
pathfinder refuses. Even after the cursor fix landed, the fall 9 exit took a three-minute
search of tiles 11, 8 and 9 before 10.

**2. The shipping bin is not an action.** Target `Farm(71,14)` failed 24 times across runs,
`(72,19)` 10 times, `(72,14)` 7, `(68,16)` 5. The farmer circles the house narrating that the
bin is not where he left it. The bin does not appear in `nearbyActions`.

**3. The mine entrance and elevator.** `Mine(23,9)` failed 22 times. The elevator does not
respond to an action press and is not an exit candidate.

**4. Festival maps blank the exits.** Under `eventUp` the bridge clears `exits`, so leaving a
festival is a blind hunt along the map edge. At the fall 16 Fair this produced 4 h 20 m of real
time, 509 decisions and $1.81 with the game clock frozen at 10:20 PM.

**5. An off-map warp is clamped onto whatever occupies the edge tile.** `GetExitCandidates` in
`src/Autoplay.GameBridge/ModEntry.cs` clamps a warp whose target lies past the map edge to the
edge tile itself. On the Beach the Town warp clamped to `(40,0)`, a lamp post.
`TryGetReachDistance` then reports it reachable because a neighbour is walkable, so the harness
hops at an unwalkable tile until it gives up. The real path is two tiles west. This caused the
fall 3 pass-out.

**6. A confirmation dialog is invisible to the tools.** The "Leave the festival?" box exposes
no `menuEntries` and no `dialogueResponses`, so `choose_dialogue_response` returns
`dialogue_response_unavailable`. Mouse clicks on it hit No three times. The model escaped only
by guessing the `Y` key.

**7. Blocked routes name places, never blockers.** `world.blockedNow` lists destinations such
as `["FarmHouse","Backwoods"]` but never the debris tiles responsible, so clearing a path never
presents itself as an option. On fall 20 the farmer edged along the pond shore, then walked
Forest → Town → Bus Stop to re-enter the farm from the north, arriving home at 8 PM, while
holding both an axe and a pickaxe.

**8. There is no festival calendar in context.** On fall 3 the farmer waited on the Beach from
6:50 PM for the Dance of the Moonlight Jellies, which occurs on summer 28, and passed out. The
season and day are in the state; the event dates are not.

**9. `look_at` rejects most of what is visible.** Nine rejections of
`Choose x and y from curiosities; that spot holds nothing`. The curiosity list is narrower than
what the screenshot shows, so looking at something is usually refused.

## Input and control

**10. The cursor stole every action press.** The bridge parks the game cursor one tile below
the farmer, and Stardew's action button acts on the cursor's tile whenever the cursor is within
a tile of the player. Every `press X` therefore hit the floor instead of the ladder, chest or
door the farmer faced. This is the single root cause behind four consecutive mine pass-outs and
every unexplained "the button did nothing" moment. Confirmed from the state: standing at
`(10,4)` facing up, `cursorTileX/Y` read `(10,5)`. Fixed and deployed; the first unaided ladder
exit followed on fall 9. *Status: fixed in `b19b2a7`, deployed and verified.*

**11. A control sequence aborts before its payload step.** When a walk step in
`control_sequence` is blocked, the sequence stops and the action step after it never fires. The
model's correct instinct, walk into position then act, therefore looked broken every time the
walk was blocked, which pushed it toward worse strategies. *Open.*

**12. One blocked move poisons that direction at that tile.** `blocked_movements` in
`harness/autoplay_harness/runner.py` records `(location, pixel, buttons)` and rejects the same
direction from the same tile forever after with `movement_previously_blocked`, even once the
obstruction is gone or the intent has changed. Seen at the mine ladder and at the Beach edge.
*Open.*

## Chat

**13. The channel account's own posts came back as viewer lines.** Replies posted through the
API arrived back through IRC as ordinary chat, so the farmer answered himself, and a request
was re-created from the bot's own outcome post. *Fixed in `8f6e19f`.*

**14. Every decision re-showed the last ten minutes of chat.** Combined with a prompt rule
never to leave a viewer unanswered, this produced about twelve near-identical replies per
platform inside ten minutes. *Fixed in `8f6e19f`.*

**15. Replies went to every platform, by three separate routes.** Named replies, unaddressed
status lines and request-outcome posts each had their own path and each ignored where the
viewer was. Kick received an entire Twitch conversation with no Kick chatters present.
*Fixed across `8f6e19f`, `61c9811`, `f2ca1ef`.*

**16. Audience requests never expired on the clock.** A request was keyed by in-game day only,
so one raised in a session that ended unsaved survived into the next run hours later and blocked
new requests, since only one is accepted at a time. Now missed after 15 minutes pending, failed
after 60 minutes bound, and expired immediately if it carries no timestamp. *Fixed in `8f6e19f`.*

**17. Showing a line once dropped it if the farmer was busy.** A viewer message that arrived
during a long local action was never shown again, so four messages went unanswered. A line now
stays in view until the farmer has posted to chat after it arrived. *Fixed in `b19b2a7`.*

**18. A steer worded as a per-action instruction became a spam loop.** Operator guidance
persists until replaced or the day rolls over, so "attach a chat reply on your very next action"
fired on every action: four reworded copies in 90 seconds. The relay now rejects a reworded
repeat of anything posted in the last ten minutes, but the discipline matters more than the
guard: **a steer must be worded as a one-time instruction.** *Guard fixed in `b19b2a7`; the
wording rule is operational.*

**19. Reply latency measured 40 to 107 seconds on live viewers.** The harness itself is not the
problem: from the "new in chat" note to the reply, the median is 9 seconds and the 90th
percentile 18. The delay is the batching window, the per-platform relay gap, provider stalls and
long local actions. Window 15 s → 10 s and gap 20 s → 5 s. *Partly fixed in `b19b2a7`.*

**20. A long local action blocks chat completely.** Worst observed gap from a viewer line to its
reply was 682 seconds, during a walk home. Navigation already yields for a newly noticed
encounter; it does not yield for a viewer message. *Open, and the largest remaining chat delay.*

**21. No audience request has ever been fulfilled.** All six entries in `audience.recent` are
missed, failed or stalled. The feature has never once completed end to end. *Open.*

## Model and API

**22. The HTTP 400 is the correction-retry path, not a flaky validator.** This has been
misdiagnosed since it first appeared. Every one of the four in-window 400s landed on
`response_attempt 2`, the retry issued after the model returned invalid tool arguments, and all
three HTTP attempts of that retry fail identically. Not one of roughly 3190 first attempts hit
it. The reported index is always the last element of the tools array: `[36]` with 37 tools, and
`[24]` in the pre-stream captures. The captured payload has 37 well-formed tools, each with
`type: "function"`, so the trigger is not the tool schema but the correction text appended to
the final user message by `_append_correction` in `harness/autoplay_harness/openrouter.py`. The
comment above the retry, that the validator "intermittently rejects a request it accepts seconds
later", is wrong, and the three retries it authorises always burn. One casualty: the only
`update_farm_plan` call of the session, which is why the farm plan is still null. *Open, and
now reproducible: replay any `.body.json` in the bad-requests directory.*

**23. Read timeouts follow request growth.** Six timeouts, all in run `1335654a` between 03:35
and 06:17 UTC, exactly where the request had grown: `request_bytes` from 148 KB at step 1 to
527 KB at step 1060, `turns` from 13 to 1585, prompt tokens from 23 k to 95 k, before a reset
brought it back down. *Open.*

**24. Three responses were discarded** with "Response provider does not match the official-only
route", meaning OpenRouter served a non-official provider for the model. *Open.*

**25. Two responses contained no tool call at all**, and several first attempts returned invalid
arguments: `tiles has too many items`, `missing say`, `missing count`, and one missing five
required fields at once. *Open.*

**26. The latency tail is long even when nothing fails.** Run `1335654a`: median 10.1 s, 90th
percentile 26 s, 99th 54 s, maximum 104 s, with 135 calls over 30 seconds and 12 over 60. All
were successful first attempts, so this is provider-side. *Open.*

## The loop: objectives, progress, stalls

**27. Arrival counts as progress, so the stall detector slept through a four-hour loop.** Every
`navigate_to` that returns `arrived` resets the progress tracker, even while the game clock is
frozen and nothing changes. The Fair loop produced 509 decisions and no stall advice. A frozen
clock with decisions still flowing should itself be the signal. *Open.*

**28. Objectives churn without finishing.** 107 objectives ended during the stream: 75
interrupted, 23 completed, 9 abandoned. By source, 75 were self-chosen against 21 from quests,
9 from events, 1 from chat and 1 from a letter. The same goals recur: "Go home and sleep" 19
times, "Get my bearings" 16. Seven of the eleven open quests were never attempted at all: the
coop, the mayor's shorts, Pam's ale, Demetrius's melon, George's pepper, Linus's basket and the
winter mystery. *Open; steered by hand on fall 20 and winter 1.*

**29. Mail carries no date and no read flag,** so a four-day-old letter re-planned a festival
that had already happened. *Open.*

**30. Inventory management blocks real work.** Seventeen `clear_debris` rejections for a full
inventory, six `action_budget_reached`, one `debris_not_cleared_within_swing_cap`. The farmer
trashes items ad hoc to make room rather than shipping or storing them. *Open.*

**31. The bedtime gate fights the agent.** Nine rejections of
`bedtime_not_allowed_before_2000_unless_exhausted`, nineteen `go_home_and_sleep` results of
`partial: night_did_not_finish`, plus `no_route` failures when sleep was attempted from
underground. *Open.*

**32. Menus and dialogue wedge.** Three `close_menu` results of `menu_still_open:GeodeMenu`.
The bridge log holds 57 `watch_dialogue_timeout reason=tick_budget_exhausted`, 12
`navigation_blocked`, 25 `navigation_interrupted` and 2 `wait_timeout condition=can_move=true`.
*Open.*

**33. Operator guidance is cleared at every day change.** `_expire_scene_work` scopes guidance
to the calendar day, which is deliberate, but it means a standing rule such as "leave the mines
by 5 PM" has to be re-sent every night. Twelve of the thirty steers were re-statements.
*Open by design; the fix is perception, not persistence.*

## Operations and observability

**34. The cost ledger recorded nothing.** `cost_ledger.json` reads zero cost, zero decisions and
an empty run list for a day that carried most of a $5.54 stream. Budget enforcement depends on
this file, so the budget was never actually enforced. *Open, and the highest-priority
infrastructure item.*

**35. The supervisor status file goes stale.** `forever_status.json` still reported `running`
with the last run id and zero restarts hours after everything had stopped. *Open.*

**36. Session logs are overwritten on every restart and were empty at the end.** All of
`harness.out.log`, `harness.err.log`, `chat.out.log` and `chat.err.log` were zero bytes when the
stream stopped, so the final run left no local diagnostics at all. *Open.*

**37. Attach and forever are mutually exclusive.** Restarting the harness against a running game
means giving up the supervisor, so run `30a69fdf` played for nearly six hours with nothing to
restart it had it crashed. *Open.*

**38. A hold does not release the harness process.** The run closes, but the Python process
blocks in `supervisor.wait_for_started_game()`. Both restarts required killing the Python
children individually, since the game is launched in a new process group rather than a job
object. *Open.*

**39. A live TypeError in the notebook.** `observe_life` at `harness/autoplay_harness/notebook.py:103`
raises `'<' not supported between instances of 'NoneType' and 'str'`, seventeen tracebacks in
`forever.log`. All timestamps predate this stream, but the code path is unchanged. *Open.*

**40. Run artifacts reached 8.9 GB** across about thirty run directories, dominated by video
segments. One pre-stream segment is truncated with no moov atom. *Open.*

**41. The newest video segment is always unplayable** until it is closed, so any tooling that
extracts a frame must use the second-newest file. Worth knowing: a desktop screen capture of the
fullscreen game returns a stale title splash, so the recording is the only way to see what the
farmer actually sees. *Documented, not a defect.*

**42. The README describes a model that is no longer used.** It states that gameplay defaults to
`openai/gpt-5.6-luna` through OpenAI only with actor and director reasoning split. The stream ran
entirely on `meta/muse-spark-1.3-contributor` as a single agent. Anyone reading the repository
cold is told the wrong thing about the most important configuration choice in it. *Open.*

## Suggested order of work

1. Perception, items 1 to 8. These caused the pass-outs, the four-hour Fair loop and most of the
   292 unwalkable-path results, and no amount of steering removes them.
2. The cost ledger, item 34, because the budget is currently unenforced.
3. The retry payload, item 22, which is small, reproducible and wastes a decision plus three
   HTTP calls every time it fires.
4. Navigation yielding for a viewer line, item 20, the largest remaining chat delay.
5. Objective and quest ranking, items 28 and 29.

## How this was produced

Aggregated from `events.jsonl` for the three runs, the harness state directory, the bridge log
and the session logs. The failure taxonomy comes from counting `tool_result` entries by tool,
status and reason; the latency figures from `model_ms` and the per-attempt diagnostics recorded
on each decision; the pass-outs from money deltas across day boundaries.
