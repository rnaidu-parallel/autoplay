# First live stream review: 2026-09-07

Nine hours on Twitch and Kick, 19:10 to 04:36 UTC. Neon played Spring 6 to Summer 10, 33 game days,
about 15 minutes per day. Sources: the event-log digest (`harness/runs/review-2026-09-07/`), a Codex
review (gpt-5.6-sol, high effort) of logs and code, a Sonnet review of the narration, and my own pass
over the worst days.

## By the numbers

| Measure | Value |
| --- | --- |
| Decisions | 2,006 (69% movement tools; 351 of those blocked or rejected) |
| Model cost | $2.11, 47.5M prompt tokens, 61% cache hit, p50 latency 7.4 s low / 11.4 s medium |
| Harness interventions | 57 stall advisories, 24 objective replacements, 30 circuit breakers |
| Operator steers | 20 (5 of them in one 20-minute stretch at the Flower Dance) |
| Process restarts | 19, from a bridge connection reset, a Meta 400 storm and a notebook TypeError |
| Pass-outs | 2 (Spring 23 in the doorway, Summer 4 in Town) |
| Money | 200 at start, 1,225 peak on Spring 16, 59 at the end |
| Longest days | Spring 24 at 54 min (Flower Dance bridge), Spring 13 at 38 min (Egg Festival crowd) |
| Narration | 63 consecutive repeated openings, 133 lines in exact-repeat groups, 3 questions in 2,006 lines |

The verdict shared by all three reviews: the model is fine, but it is being asked to solve UI and
movement problems the bridge could answer semantically. Almost every long, boring stretch traces to
one of six mechanical gaps below, not to a bad decision.

## Gameplay changes, ranked by impact

### 1. Verified shopping instead of raw clicks
- **Evidence.** Spring 19: he wanted one cauliflower seed, clicked the shop row 15 times narrating
  "that click didn't take", and spent 1,200 g of 1,225 g. The crop needed 12 days, it was Spring 19,
  and it died on Summer 1. The circuit breaker could not trip because money changed on every click.
- **Change.** `buy_shop_item(index, quantity, max_total)` in the harness that clicks internally and
  verifies money and inventory deltas, returning `purchased`, `total_spent`, `remaining_money`.
  Reject generic clicks on sale rows. Add season, growth days, regrowth and "can it mature before the
  season ends" to each `shopItems` entry from the bridge.
- **Risk.** Limited-stock and variant shops; stop on the first unexpected delta.

### 2. People and festivals as semantic actions
- **Evidence.** Spring 13 (Egg Festival) used 75 raw control sequences and six objective flips to
  greet people in a crowd. Spring 24 (Flower Dance) used 120 holds, 8 objective replacements and
  5 steers to cross a one-tile plank bridge the grid navigator does not know. Introductions was
  chased for parts of ten days, with 15-plus lines of "same bottom names again" scrolling the social
  tab, because nothing tells him who he has not met.
- **Change.** Expose `talk_to_npc(name)` (the harness already has `approach_and_talk` as a reflex)
  and allow it when `festival && eventCanMove`. Add `unmetVillagers` to the bridge state so
  Introductions needs no scrolling. Make festival maps navigable (the plank bridge tile) or add a
  `navigate_to_npc`. Treat `festival` as well as `eventUp` as "never replace the objective".
- **Risk.** NPCs move; verify by identity and dialogue state, with a short deadline.

### 3. Close the bridge's hidden-item gaps
- **Evidence.** Spring 16: the crafted scarecrow sat invisibly on the cursor for ten minutes, the
  menu would not close, two steers were needed. Robin's axe ate parts of five days because ground
  quest items are never listed in surroundings.
- **Change.** One `heldItem` bridge field covering CraftingPage, ShopMenu and ItemGrabMenu, and a
  `close_menu` that returns it safely. `groundItems` for all collectible and quest objects plus a
  verified `pick_up_ground_item(id)`.
- **Risk.** Menu internals are version-sensitive; keep adapters narrow, add fixtures.

### 4. Quest truth and cross-day memory
- **Evidence.** He declared Shane's leek delivered and moved on; the quest stayed open to the end. He
  tried the locked Wizard tower most mornings and re-selected the axe hunt after writing in his diary
  that he was dropping it. Only the last three objectives are shown and same-target history resets
  each day.
- **Change.** For objectives with `source: quest:<id>`, attach the journal status to the outcome and
  surface a contradiction when a "completed" claim meets an open quest (advice, not enforcement).
  Keep a per-source history in the observation: days attempted, last real progress, last failure,
  last explicit backoff, cleared by a new enabling fact (a letter, a new item).
- **Risk.** Journal updates can lag; present as evidence to reconcile.

### 5. A durable answer to inventory pressure
- **Evidence.** The bag was full in 395 of 412 observations on Spring 13 and every observation on
  Spring 20, 21 and Summer 8. He trashed wood, stone, sap, fish, forage and seeds 26 times.
- **Change.** `ship_item` walks to the bin from anywhere on the Farm. Expose chest contents and add
  `store_item`. Surface capacity pressure and the backpack upgrade as a practical need. Prompt: trash
  only genuinely disposable items.
- **Risk.** Logistics can eat the day; batch deposits.

### 6. Local recovery before objective replacement
- **Evidence.** 24 harness replacements with "Do something clearly different, somewhere else", most
  of which he immediately reversed. Replacement also clears blocked-movement memory, so the failed
  tactic comes back.
- **Change.** Two stages: first keep the objective and block the repeated semantic target or action
  class; replace the objective only if a distinct recovery also yields nothing. Key movement by
  destination or NPC, not by exact ticks.
- **Risk.** A broader breaker can block a valid second try; clear it on any real state change.

### 7. Farm economy that actually funds the character
- **Evidence.** 33 days ended with 59 g, two blueberry plants and one potato. The farm plan tool was
  never called. Watering happened, planting barely did. Summer 4 ended in a pass-out that cost 6 g at
  the clinic.
- **Change.** A dawn habit in the prompt: crops first, then one money action (ship forage or fish)
  before town. Surface "days of cash left" and the season calendar in the observation. Reuse the
  existing `update_farm_plan` by mentioning it in the morning note when the plan is empty.
- **Risk.** Too much routine dulls the stream; keep it to the first game hour.

### 8. Small fixes with clear evidence
- The TV is boxed in by furniture: 14 failed curiosity approaches and 22 narrated lines about it.
  Drop the TV curiosity when no adjacent tile is passable, or fix the tile check.
- `wait` never worked: 3 rejected by the scene-change breaker, 2 timed out. Either make it a real
  bounded wait or remove it.
- Identical diary notes were written three times in a row on Spring 23 while sleep kept failing.
  Deduplicate consecutive identical diary text.
- Bridge or capture connection resets (`WinError 10054`) hit the fatal path. Normalise known I/O
  `OSError`s to the recoverable type and reconnect.
- Soak-test what is already fixed (can refill, reel-in, pet nudge, null notices, title-keyed special
  orders) rather than redesigning bedtime again. Add a `latestSafeDeparture` estimate from route
  length and recent p90 latency.

### 9. Open questions to decide
- **Meta HTTP 400.** Fourteen rejections in one hour, none in the next three. The index in the error
  matched the second-to-last tool in both tool-list sizes, but Codex points out it also matched a
  stable early message in the rebuilt transcript. Both readings fit the data. Reproduce with a saved
  body before changing anything; the harness now dumps full bodies.
- **Caps and checks.** Depth still sets output caps of 2,000, 6,000 and 12,000 tokens and
  `set_objective` still accepts a `check` expression. Neither caused a failure this run. Remove or
  keep deliberately.

## Viewership changes, ranked

### 1. A game-facing overlay
The bottom bar spends five fields on duration, calls, cost, tokens and cache. The rail plus bands leave
the game 64% of the canvas. Day, time, weather, money and energy are in the feed but never drawn.
Render season, day, time, weather, money, energy, the current goal and quest progress; move model
metrics and raw failure reasons to the operator view; show viewer-safe failure text instead of
`no_walkable_path`; shrink the rail to the current line and one previous line.

### 2. Speak for beats, not for steps
Every tool requires `say`, so motion telemetry becomes dialogue: "Made it" 97 times, "I'm right"
42, "Marlon's right" 29, "that scarecrow" 27. Make `say` optional and ask for speech on a reaction,
choice, discovery, setback, promise, milestone or question to chat, keeping the last caption up while
silent actions run. Add an output-side suppressor that collapses near-identical consecutive lines
without re-running the decision. Prompt additions:
- "Never say menu, tooltip, cursor, click, tab or scroll. Describe what your hands and eyes do."
- "If your last three lines share a sentence, react to being stuck rather than to the obstacle."
- Diary field: "Never mention the stream, the broadcast or being watched." (The bedtime note on
  Summer 9 said "per stream wrap".)

### 3. Hard-template operator messages
The first steer of the night opened with the operator's first name and Neon repeated it on air.
Every later steer was signed "Stream operator" and none leaked. Redaction is in place; also template
every steer as "Stream operator: ..." inside the harness so a slip can never reach the model.

### 4. Show the arc
The public overlay renders only the goal title, and sometimes the harness placeholders ("Get my
bearings"). Show the goal, one next milestone in his words, relevant journal progress, a visible
blocked or waiting reason, and the last completed milestone. Add a spoken dawn plan (one or two
sentences) and keep the bedtime diary as the recap.

### 5. Moments and clips
Derive moment events from quest revision, festival start and end, new items, level-ups, large money
changes, first visits and pass-outs. Show a short toast and write a clip marker. Let his next normal
line react; no extra narration call.

### 6. Pacing as a story-beat budget
Track seconds since the last story beat (goal set, conversation, discovery, purchase, harvest, quest
progress, event phase, meaningful failure). After about 90 s without one, ask him to name the obstacle
and pick a new tactic while keeping the objective. Advice only.

### 7. Audience participation, measured
Only 3 of 2,006 lines were questions and none addressed chat. Kick chat ingestion is not implemented
yet, only Twitch. Nudge one question to chat every 20 to 30 minutes, enable one announced viewer
challenge at a time, log impressions to completion, and compare with retention and chat rate. Add
Kick chat once Twitch binding is stable.

## Where else to stream

Beyond Twitch, Kick and YouTube: Facebook Gaming, TikTok LIVE (needs 1,000 followers for the key),
Trovo, DLive, Rumble, X live video, Instagram Live, Steam Broadcasting, Picarto, Nimo TV, SOOP
(formerly AfreecaTV, Korea), Bilibili and Douyu (China), VK Play Live, and a self-hosted Owncast
page. OBS's Multiple Output dock already handles several RTMP targets at once; Restream or Streamyard
can fan out further without adding local encoding load. TikTok and Instagram want a vertical feed, so
they need a second, portrait scene.

## Acceptance run before the next stream

Two to three hours, unattended, on the same save, passing all of:
- zero raw shop-row clicks and no purchase of a crop that cannot mature;
- no NPC target attempted more than three times;
- no objective replacement during a festival;
- no unacknowledged contradiction between a "completed" claim and an open quest;
- no unhandled restart, no pass-out;
- at most one operator steer per ten game days;
- no line on stream naming a UI element, the harness, or a real person.

## What we had wrong

- Summer 1's spend (225 to 65) was two blueberry packets, planted and watered at once. Reasonable.
- The two immediate crash-restarts after the notebook TypeError were superseded runs, not new errors.
- The 400 index reading is unresolved, not settled.

## Decisions and what was built (2026-09-08)

Decisions after the review: same Meta model with no fallback; fix root causes, not incidents; no
new automatic behaviours in the harness; the overlay never duplicates the game's own HUD; journal
quests are passive by default with one active objective; chat can steer anything; no acceptance
soak before the next stream.

Built, all tests green (393):
- Every game action reports `effects` (money, bag, cursor, place, menu, dialogue, energy) and the
  prompt tells him to read them before repeating anything. This replaces the shop, held-item and
  festival-hold incidents with one perception.
- The bridge reports the cursor item from any menu page and `close_menu` returns it; curiosities
  carry `reachable`.
- Quests carry per-quest `history` (days worked, last outcome, last note) from the ledger; a
  quest objective called completed while the journal lists it open is disputed with a note.
- Stall handling is advice only; the harness sets an objective only for bedtime.
- `say` is optional and reserved for beats; the prompt bans interface words and stream mentions
  in the diary; operator guidance is always prefixed "Stream operator:" and redacted.
- Chat: Kick ingestion, recent chat lines in the observation, several requests per day.
- Prompt: passive journal, money and the farm, speaking.
Not built, by decision: a purchase tool, an unmet-villagers field (`npcsNearby.met` already
exists), festival map fixes, local-recovery stages, story-beat timers, overlay HUD fields.
