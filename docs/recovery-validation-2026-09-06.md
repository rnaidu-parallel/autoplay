# Activity recovery and timely encounters — 6 September 2026

The prior mailbox, native-window, retry-gate and audience-binding fixes are preserved.
The festival/encounter findings from the [incident review](stream-incident-2026-09-05.md)
now have code repairs and regression coverage. Public-stream acceptance remains pending.

## Changed behavior

- An activity requests replanning after 45 seconds or eight decisions without a new
  game milestone. After 120 seconds or 24 decisions, autonomous input stops and the
  existing `needs_attention` handoff preserves the game. Checks occur at control-loop
  boundaries; an in-flight model/bridge request is not forcibly cancelled. These are
  thresholds, not an exact two-minute wall-clock cancellation guarantee.
- Small movements, menu toggles, dialogue prose, waiting and objective rewording do
  not reset this activity deadline. New inventory/resource/quest/crop/save evidence,
  first visits, first greetings and event-command progress do. Repeated evidence
  within the game day does not reset it. Existing local-action retry guards remain.
- Live encounters receive a state-only actor decision before journal or background
  planning. Choosing `act` immediately runs an approach-and-talk continuation, with
  no second model call. Each segment reobserves the target and checks operator input,
  scene changes and damage. The controller stops after eight seconds or 12 controls;
  pending bridge requests can finish before the deadline is checked again.
- The bridge resolves the selected NPC identity and adjacency again before ordinary
  right-click input. A held gift is replaced by a toolbar tool through ordinary input.
  An absent or inaccessible person becomes a missed opportunity; the actor cannot
  accept a stale pending encounter. Telemetry records seen-to-first-input and outcome
  timing. No live latency percentile is claimed yet.
- Temporary operator guidance and event intentions expire by observed event identity
  or game day. Event agenda items do not carry into tomorrow. Unrelated intentions,
  quests, tools and historical records remain. Dated HUD opportunities expire; old
  HUD/dialogue excerpts leave current context. Deferred quest conditions trigger once.
- Event observations include identity, command phase, festival actors and movement
  capability. Ordinary map exits and incompatible navigation/journal tools are hidden
  during events. Normal movement/dialogue controls remain; the global event navigation
  guard is preserved. A journal keypress succeeds only if `QuestLog` actually opens.
- The existing wiki tool is available for unknown mechanics. One lookup is allowed
  between game milestones, with one search fallback and one page by default. Each
  HTTP request has a three-second socket timeout. Failed lookups return a blocked
  result; they neither crash gameplay nor count as progress.
- Chat provider exhaustion is recorded in `chat/state.json`; the reader continues
  to the next window. Binding remains subject to the real-chat shadow gate.

## Evidence

- All 316 Python tests passed: 296 existing tests plus 20 new recovery cases.
- The new cases cover the recorded stale Caroline/Haley and day-11 Bookseller
  failures, moving-target continuation, operator interruption, actual journal opening,
  event expiry, preserved unrelated work, actor tool selection, wiki failure and chat
  provider recovery, plus varied wandering and repeated resource/location states.
- Recorded festival states are retained in
  `harness/tests/fixtures/festival-stall-replay.json`. The test replays their original
  timestamps and checks the activity monitor, without game input or model calls:

  | Recorded run | Replan | Stop at next sampled observation |
  | --- | ---: | ---: |
  | `3d418abd-50f3-4f59-a52c-418739733774` | 50.034 s | 126.371 s / 18 samples |
  | `576150e1-33c5-4de6-9547-e6a6155e91cf` | 49.833 s | 128.979 s / 14 samples |

  This is an offline counterfactual of the recorded states, not a live festival pass.
- C# build passed and deployed locally while the game was closed. Installed DLL
  SHA-256 matches the build: `CD216CBEC04E0D56320C15B44395A94238BAE4C54751F089F130244CE7121636`.
  The existing CS9057 analyzer/compiler warning remains.
- A real `Egg Festival` wiki query returned a page and 4,000-character extract in
  1.03 seconds. No model calls, game-save changes or broadcasts were made.

## Remaining acceptance

Run a supervised rehearsal with the updated bridge. Verify a festival host interaction,
event completion/exit, and an unrelated next-day mission. Measure encounter latency
and inspect missed outcomes. Check recording picture/audio, the overlay, and both
Twitch and Kick outputs during an authorized broadcast. Review a real Twitch shadow
session before binding; Kick chat ingestion is still absent.
