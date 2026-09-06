# Stream incident review — 5 September 2026

Update, 6 September: [activity and encounter repairs](recovery-validation-2026-09-06.md)
are implemented and tested offline. The report below records the original incident;
live festival and next-day acceptance remain pending.

The stream was not ready for unattended play. The earlier 296-test result and mailbox checks did not validate festival progression, event exit, next-day intent expiry, or timely encounters. This review diagnoses the observed failures; gameplay repairs below are not implemented yet.

## Evidence

Local event logs under `harness/runs/`:

| Run | Actor calls | Median model / actor-cycle time | p95 model / actor-cycle time |
| --- | ---: | ---: | ---: |
| `3d418abd-50f3-4f59-a52c-418739733774` | 316 | 4.63 / 5.61 s | 7.28 / 9.00 s |
| `576150e1-33c5-4de6-9547-e6a6155e91cf` | 36 | 4.50 / 5.56 s | 6.32 / 8.07 s |
| `3994f2d3-05cd-47de-95df-17671be09015` | 162 | 4.11 / 5.29 s | 6.18 / 9.35 s |

Actor-cycle telemetry excludes outer-loop work and some director waits. These are not end-to-end viewer-command latency measurements. Tool status `completed` means a control finished, not that useful gameplay progress occurred.

The original run recorded 188 tool results in the festival's `Temp` location spanning 31.3 minutes. Across that run: 66 quest reviews, 43 journal checks, and 45 tool-result rejections containing “journal changed.” The first guided recovery spent another 3.6 minutes there before manual control was needed.

## Findings and repairs

### P1: Event guidance survives the event and becomes false authority

`runner.py` stores operator guidance as an unscoped string. It clears it upon verified objective completion, not upon event completion or a new day. The final recovery run recorded zero guidance-cleared events.

At Spring 14 06:40, step 44 explicitly justified continuing the egg hunt because the operator said it had started. Steps 82–120 misinterpreted the Community Center introduction as festival instructions and an egg-hunt maze. Step 141 still said “The hunt is underway” at 14:10 in Town.

My recovery guidance contributed directly to this failure. It should never have remained a current instruction after its event ended. `notebook.start_day` also carries unfinished agenda entries without an event-lifetime distinction. Retrieved life text has no context-expiry handling in the runner.

Repair: attach observed event/day scope to temporary guidance and event intentions. On a verified event exit or day transition, retire only the expired event work, clear its guidance/retrieved context, and refresh the plan from the current scene. Preserve unrelated unfinished missions. Add explicit event identity/phase to bridge observations; `eventUp` alone cannot distinguish the Egg Festival from a different cutscene involving Lewis. Do not detect expiry by searching prose for “egg.”

### P1: Encounter notices remain actionable after the person leaves

`life.observe` creates retained encounter notices, but does not expire them when the target disappears or the day/location changes. `life.respond` validates pending status, not current target availability. A fresh observation before execution therefore does not prevent accepting a stale notice.

Final run step 151 accepted “Approach Caroline” when the latest observed NPC list contained Lewis, not Caroline. Step 161 accepted “Approach Haley” when it contained Harvey, not Haley. A Spring 11 Bookseller “today” announcement was acted on at Spring 14, step 135.

Repair: give transient notices explicit validity scope and target identity. Revalidate at selection and immediately before movement/interaction. Mark unavailable opportunities as expired/missed with evidence; preserve historical memory without presenting it as a current opportunity. Date-limit time-sensitive announcements; do not expire persistent quest/tool discoveries indiscriminately.

### P1: Too many decisions intervene between noticing and doing

Each model decision usually costs 4–5 seconds before controls execute. Acknowledgement, quest review, director planning, navigation, and interaction become separate calls while villagers continue walking. At step 156, Vincent was still present when his notice was accepted; step 157 changed the objective to a Forest axe search, followed by navigation and more reviews. The greeting never became the immediate action.

`_actor_step` gives quest-review requirements priority over notice response outside events. Deferred quest conditions can repeatedly invalidate a review whenever their condition remains true. Condition syntax mistakes add more rejected calls. Operator commands are checked after the current model request returns; they do not cancel that request.

Repair: once an encounter is selected, execute a bounded approach-and-talk continuation against the live target before unrelated bookkeeping. Stop when the target is unavailable or a higher-priority safety/operator event occurs. Separate urgent interaction from background planning and reduce repeated review invalidation. Keep fresh-state checks and normal game time. Measure encounter-seen → first input → dialogue, and operator-submit → acknowledgement → first input; benchmark context/capture changes only after correctness.

### P1: Festival capability mismatch creates repeated impossible actions

The bridge collects NPCs from `location.characters`; the observed festival returned no nearby NPCs despite the visible crowd. It also exposed ordinary Town doors/warps in the festival scene. Navigation explicitly interrupts on `Game1.eventUp`. The actor still receives navigation tools and journal attention text during the event. `check_journal` reported released input without opening a journal; quest review then rejected the unread revision.

Repair: observe festival actors and current event capabilities. Offer tools appropriate to the actual event phase, and distinguish successful input delivery from opening the requested menu. Use normal controls for free festival movement; retain cutscene restrictions. Do not remove the global event guard or pretend a failed journal check succeeded.

### P1: Stall recovery measures local changes, not completion of the activity

`_stall_fingerprint` includes position, dialogue, and menu. Small movements or changed dialogue reset the counter. Cycle detection only recognizes exact periods of two to four repeated states within twelve samples. `idle`/`wait` completion also resets the retry no-progress counter. These mechanisms allowed a 31-minute unresolved festival before the harness finally stopped.

Repair: maintain an activity-level progress/deadline record that survives minor movement, tool alternation, and objective rewording. Count verified milestones separately from input changes. Bound attempts to reach the festival host/start/exit; hand off when exhausted. This must be tested with varied failures and small movements, not only identical repeated actions.

### Broadcast operation: Kick was deliberately disabled by the operator

This was my decision, not an ingest failure. I disabled Kick's synchronized start because its regional language label remained unresolved. I should have preserved the requested Twitch+Kick scope and resolved that conflict explicitly rather than narrowing the launch.

After stopping and verifying all OBS outputs inactive, I restored Kick's local synchronized start and stop settings; YouTube remains disabled. OBS is closed. Neither platform's title/bio nor Kick's language has been changed. A future launch must check and report both destination outputs; restored configuration is not proof of live ingest.

## Acceptance before another public test

1. Replay these logs in regression tests: stale Caroline/Haley, day-11 Bookseller, event guidance across exit/day change, quest-review loops, and wandering without activity progress.
2. Verify a festival encounter, host interaction, event completion, and next-day unrelated mission live. Current unsaved game remains held; do not reset it for testing.
3. Measure reaction latency and verify selected encounters lead to action while targets remain available.
4. Verify current picture/audio, overlay following a replacement run, and both Twitch and Kick outputs during an explicitly authorized broadcast. Keep chat binding separate from NPC interaction testing.

No gameplay code, live save, model routing, or public metadata was changed during this review. Kick's local start/stop configuration was the only operational repair.
