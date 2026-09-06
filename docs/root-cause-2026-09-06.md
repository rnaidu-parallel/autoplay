# Why the last streams did not flow — root causes, 6 September 2026

Evidence: the ten live runs of 6 September (`bb458822` … `a118bed5`, `harness/runs/`), the
5 September festival runs, and a read-only probe of the game that was left open after the stream.

## The headline numbers

Across the ten live runs (55 real minutes, 411 actor decisions, 44 director calls, $0.69):

| Share of actor decisions | What the model call actually did |
| ---: | --- |
| 28.5% | Bookkeeping with no game input: `review_quests`, `check_journal`, `inspect_scene`, `change_objective`, `respond_to_notice`, `open_menu_tab` … |
| 18.0% | Re-deciding after a travel segment was interrupted ("yielded") by the harness |
| 11.9% | Rejected with `scene_changed_while_thinking` |
| 7.8% | Rejected by a harness rule (agenda id, quest review format, deferred target …) |
| 7.3% | Blocked navigation (`no_walkable_path`, `no_exit_to_location`) |
| 6.1% | Advancing one page of a cutscene or dialogue |
| **17.8%** | **A productive game action** |

Every one of the ten restarts had the same stop reason: `activity_stopped` — the activity
watchdog added on 6 September ended the run. None of the ten was a genuine deadlock.

## Root causes, in order of stream impact

### 1. The harness kills healthy play (all 10 restarts)

`progress.py` stops the run after 120 s / 24 decisions without a *new* milestone, and
"repeated evidence within the game day does not reset it". Clearing grass a second time, walking
home at 23:50, watching a cutscene, standing in an inventory menu: none of these count, so the
harness declared the character stalled while she was demonstrably doing the thing she said.
Run `bb458822` ended at 11:14:44 mid `clear_debris` with `tiles_cleared` in the result;
`befd31ca` ended while walking home to sleep; `cf65f4f8` ended inside the Community Center
cutscene. The older cycling watchdog (`_update_stall_watchdog`) had the same terminal `stop`.

A stream must never end because the harness disagrees with the character about what counts as
progress. Stalls are recovered by changing the character's mind, not by ending the run.

### 2. The game's own rules were invisible: the "unclosable" inventory menu

`a118bed5` ended with `GameMenu:0:InventoryPage` that no click or Escape could close. The vault
decision recorded this as "input not delivered". It was not. Stardew refuses to close the
inventory while an item is held on the cursor, and the state had said so the whole time:
`cursorItem = "Fiber"` from step 6 onward. The `drag` implementation cannot move items in a menu
(it presses the SMAPI input button each tick and never calls the page's click/hold/release), so
the model kept dragging, then clicked and picked a stack up, and the menu locked.

Placing the held stack into an empty slot through the bridge closed the menu instantly; the game
did not need a restart. Menu manipulation by pixel drag from a model with a five-second latency
and a downscaled frame is the wrong abstraction. Inventory operations need semantic bridge
commands (move, trash, drop, ship, close-menu-returning-held-item) and `cursorItem` must gate
"close menu".

### 3. Cutscenes and dialogue are played one page per model call

The Community Center introduction took 48 model calls and four real minutes (`cf65f4f8`
steps 44–92): the model pressed X, the page auto-advanced while it thought, the next decision
was rejected with `scene_changed_while_thinking`, and the narration produced 48 variations of
"Lewis is finishing his tour". Non-question dialogue has exactly one correct input. The harness
should advance it locally at reading pace, with one model reaction at the end, using the captured
transcript.

### 4. Navigation false negatives poison routing and create the Forest loop

- `FindExit` filters warps by `IsTileWalkable`, which offsets the player's *current* bounding box
  to the candidate tile. Map-edge warps (Bus Stop → Town at x=44) fail that test, so the bridge
  returns `no_exit_to_location`; holding D for 30 ticks worked (`befd31ca` s24). The harness then
  persisted `BusStop→Town: unreachable` and routed around it for the rest of the session.
- `FindExit` picks the *nearest* warp, not the nearest *reachable* one, and `go_to_location Farm`
  from the Forest always lands on the south entrance, which is cut off from the farmhouse by
  uncleared debris. `go_home_and_sleep` then routes Farm → Forest → … and back to the same
  entrance: 17 attempts across `cf65f4f8`, `c6af3fa3`, `56a98c5c`, and Neon passed out at 2 AM
  on stream. The location graph has no notion of an entry point or a pocket.
- Failed hops are remembered as `blocked_paths`/`unreachable_edges` from heuristics rather than
  from observed reachability, so a single bridge false negative becomes a day-long detour.

### 5. Attention yields chop travel into stutter steps and still miss the encounter

`AttentiveBridge` interrupts every navigation segment on any new NPC key, on a journal change,
or after 8 seconds. Crossing Town took eight model calls (`befd31ca` s34–s52); each pause costs
~5 s of a villager walking away, so the "encounter" reflex still reported `target_left`.
Greeting someone you pass is a reflex, not a deliberation.

### 6. Decision bureaucracy

`review_quests` is forced whenever `questRevision` changes and rejects on format (four
consecutive rejections in `bb458822` s11–s14); `check_journal` is forced before it; `inspect_scene`
costs a decision to earn a screenshot; `change_objective`/`set_objective` are rejected for an
unknown `agenda_id` (six rejections). The journal content is already in structured state;
opening it in the game proves nothing the bridge does not already know.

### 7. The operator fought the harness

Steers such as "Do not review quests, do not change objective" and "Stop clearing grass — it has
produced no progress" were reactions to the watchdog's false signal, and they consumed the
operator's attention for the whole stream.

### 8. The character has no voice, and the overlay shows plumbing

The persona is one adjective list; the only public output is a 140-character `say` every five
seconds, and the overlay prints "Action finished — control_released" plus token statistics.
Immersion needs a character with a point of view, a running thought rather than a caption per
tool call, and an overlay that shows the day, the place, and what she is thinking.

## What is *not* a root cause

- Model latency (Luna low: ~4 s median). It multiplies the waste above; it does not create it.
- Twitch/Kick ingest, OBS, recording: all held up on 6 September.
- The `notebook.tmp` rename race: fixed the same day and not seen again.

## Status, 7 September

Implemented and unit-tested (320 tests), not yet validated live; the game must be restarted to load the
new bridge.

- Bridge (C#): canonical-box walkability; flood-fill reachability; nearest *reachable* exit with
  `no_walkable_path_to_exit`; `exits` / `menuReadyToClose` / `shippingBinCount` state; `close_menu`,
  `inventory_move`, `inventory_trash`, `inventory_drop`, `ship_item`, `watch_dialogue`. Build clean.
- Harness: no terminal stop from either watchdog in continuous play (scene change instead); dialogue and
  cutscenes watched locally; greet-in-passing and bedtime reflexes; tool-specific staleness; mail, quests
  and notices demoted from gates to context; `review_quests`, `check_journal`, `respond_to_notice` and
  `drag` removed; agenda-id mismatches coerced instead of rejected; travel in 900-tick segments without
  sighting yields; pocket-aware routing from observed `exits`; `go_home_and_sleep` from anywhere.
- Character and overlay: a persona for Neon with a voice; `say` is her running thought (up to 200
  characters); the overlay shows day, place and weather, the thought as a subtitle, recent thoughts, a
  "listening" card during scenes, what a villager said, and reflex lines; token statistics only in the
  operator view; failure reasons in plain words.

Live check, 7 September (run `cd68ba4c`, attach, Spring 17): Bus Stop → Town through the map edge
worked first time; `close_menu` closed the inventory; `inventory_drop` reported exact items; the long
drought abandoned a stale objective and the director replanned without the run stopping; travel and
`navigate_to` behaved. Found and fixed during the run: a dropped item is picked straight back up, so
`inventory_drop` is no longer offered to the actor; the bridge clamped travel segments to 300 ticks
(now 900, needs a redeploy); state-only decisions away from the farm made the model spend every other
decision on `inspect_scene`, so those scenes now get a screenshot automatically; the greeting radius is
six tiles because villagers move faster than a three-tile check catches them. The run then froze for
good inside a screen grab (DXGI reported the output unsupported once the display had been idle), so
the harness now holds the display awake, bounds every grab to five seconds, and plays blind from
structured state when capture is unavailable; the second run (`6ff5f57a`) started blind and slept
Neon through the night normally. `watch_dialogue` handled Pierre's closed-sign notice in 1.7 s with one
in-character reaction. Not yet exercised live: a cutscene, the Farm south-entrance route home
(the pocket map now records the south side as cut off from the house), `ship_item`, and a completed
greeting.

## Fix plan (fundamental, not patch)

1. **Never end a run on a harness-judged stall.** Remove the terminal stop from both watchdogs;
   keep stall detection as a *replan trigger* only.
2. **Semantic menu and inventory control in the bridge**: `close_menu` (returns a held item
   first), `inventory_move`, `inventory_trash`, `inventory_drop`, `ship_item`; remove `drag` from the
   actor. State already exposes `cursorItem`; the harness uses it.
3. **Local dialogue and cutscene playback** at reading pace, one model reaction per scene, with
   the transcript in context.
4. **Reachability-aware navigation**: tile walkability from a canonical box; warps chosen by
   reachable path length; `warps[].reachable` in state; entry-aware routing in `world.py`; a
   bedtime reflex that gets Neon home before 1 AM without a model decision.
5. **Walk without stutter**: long segments, no yield on sightings; a local greet-in-passing reflex.
6. **Remove ceremony**: no forced journal/quest review, no agenda-id rejections, tool-specific
   staleness rules instead of blanket `scene_changed_while_thinking`.
7. **A character**: a persona bible, a thought stream instead of tool captions, and an overlay
   built around it.
