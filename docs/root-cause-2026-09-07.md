# Repeated routes and Gemini trial — 7 September

## Evidence from the stopped Luna stream

Run `bb283f4d-e6fa-4c2d-95a1-b4ad9aa658c8`, code `bdf1013`, 349 model calls,
$0.5504093200000003. Twitch/Kick ran from 01:20:37 until the user's stop at
01:58:42 IST. A 321-second rehearsal did not expose the later navigation failures.
The transcript and previous [root-cause report](root-cause-2026-09-06.md) were reviewed.

The event log contains 23 `navigate_to/no_walkable_path` results, eight
`travel_to/no_route` results, six interrupted bedtime attempts, and repeated
cauliflower-row clicks. The final farmer position was held unsaved at Spring 21,
24:50, Farm. These are gameplay failures even though both broadcast outputs remained healthy.

## Causes and changes

1. **Successful crossings did not repair map memory.** `unreachable_edges` still contained
   BusStop→Town from day 3 and BusStop→Farm from day 14. Raw `go_to_location` crossed them,
   but `travel_to` continued rejecting their routes. Actual location transitions now remove
   contradicted failures. Old failed exits lapse after three days, like pocket observations.
2. **The route solver invented a shortcut through the same farm pocket.** It returned
   `Greenhouse → Farm → FarmHouse` from the isolated northwest farm because an unseen
   return entrance was treated as unrestricted. An exit reachable from an observed pocket
   now inherits that pocket's restrictions on return. The exact held-state replay now gives
   `Backwoods → Mountain → Town → BusStop → Farm → FarmHouse`.
3. **Direct travel bypassed the map.** Both model travel tools now use the same route planner,
   reachability memory, opening hours, and failed-door handling. The actor cannot escape a
   rejected route merely by switching between travel tool names.
4. **Every map boundary invited another model decision.** The attention wrapper interrupted
   multi-hop routes at map changes and movement segments. Travel now continues locally toward
   the requested destination. Operator commands, scenes, damage, and bounded control budgets
   still interrupt it. This also reduces the clock time lost to high-reasoning model calls.
5. **New screenshots defeated identical-action detection.** The fingerprint included
   `frame_id` and clock time. Both now stay outside the action comparison, except time remains
   relevant to explicit waits. Changed inventory remains evidence of a real purchase.
6. **The overnight earnings page was not advanced after the date changed.** Bedtime now uses
   the normal X input for ShippingMenu before and after day advancement; save verification remains.

No navigation collision, crop-zone, provider, or save-verification constraint was relaxed.
The map still cannot make physically blocked paths passable. Gemini's reasoning is not proof
that every remaining gameplay problem is resolved.

## Gemini 3.8 Flash cache gate

Model `google/gemini-3.8-flash`; Google AI Studio only; fallback disabled;
actor and director reasoning `high`. An explicit `cache_control` boundary covers fixed
instructions and tools. Changing objectives, state, and screenshots remain outside it.
High reasoning receives an 8,192-token completion allowance, including reasoning tokens.

| Prompt profile | Cached tokens per probe | Warm-call cache writes |
| --- | ---: | ---: |
| Visual actor | 9,476 | 0 |
| Compact actor | 6,518 | 0 |
| Director | 3,646 | 0 |

Each profile was called three times. The visual probe changed its context between calls.
Provider attribution was Google AI Studio throughout; the compact/director responses also
confirmed the exact model ID. Subsequent private gameplay confirmed cache hits with changing
live state and both actor/director prompts. Probe input sizes are not runtime context budgets.

References: [model listing](https://openrouter.ai/google/gemini-3.8-flash),
[cache response fields and Gemini boundaries](https://openrouter.ai/docs/guides/best-practices/prompt-caching).
Local evidence: `broadcast/local/gemini38-cache-probe.json`,
`broadcast/local/gemini38-cache-profiles.json`, and run
`899b7b90-3273-4054-b3b5-e254e9800c15/events.jsonl`.

## Validation and operation

- 328 Python tests pass. New regression cases cover route memory, pocket round-trips,
  locally continuous travel, operator interruption, click identity, and Gemini cache/provider settings.
- Three Node tests pass: one-hour stop, early credit stop, and refusal before gate acceptance.
- Private Gemini run: inherited late-night position passed out before completing the long route;
  fresh Spring 22 completed planting, mail, and verified objectives without a persistent loop
  in the first five recorded minutes. This is a short live check, not proof of a full clean day.
- Public overlay omits name, date/time, weather/location, timestamps, branding, duplicated current
  thought, and technical failure strings. Current intention and thoughts remain; operator details stay private.
- `broadcast/start-one-hour.cjs <run-id>` requires a matching accepted local gate, starts Twitch/Kick,
  records its fixed deadline, and stops outputs/recording after one hour or credit exhaustion/run closure.
  It queues Hold unsaved at shutdown. The same Luna monitor handles steering and overnight-page checks.
- The trial has a $7.40 total gameplay cap, below the $7.41363474 account balance at launch.
  The key's remaining daily limit was higher ($8.328385238); no top-up or quota-reset extension is authorized.


## Public trial outcome: stopped early

Twitch/Kick ran from 02:28:33 IST to 02:48:17 IST September 7 (about 19 minutes 44 seconds). Parent stopped for repeated bedtime failure that the supported steer could not resolve. Credits were not exhausted. All OBS outputs and recording were verified inactive; watchdog queued hold `3983a707c0d74cedab0d10afcc92e237`. Game remains open, unsaved Spring23 23:50 at Farm(40,64), inventory menu. No restart or live code changes.

Run `899b7b90-3273-4054-b3b5-e254e9800c15`: 85 actor/director responses, $1.8930872583333334 including private gameplay; separate cache probes excluded. Recording: `broadcast/local/recordings/2026-09-07 02-18-06.mkv`.

The first live night passed out after a Town `no_route` loop despite steer `80c44d4007c44f0184e2a954f0a7282d`. Fresh-day mail, movement, watering and Beach exploration progressed. Luna sent early return-home steer `7663fbdefc284548a6ff85bee4152d52` at Beach17:20. Town18:40 followed; route reached Farm's south pocket at20:00 and could not reach FarmHouse. Only Forest was locally reachable.

Detour steer `4d7f89251a804c99bb1e876cc44199e4` explicitly requested Forest→Town→BusStop→Farm. It was received, but the actor's bedtime tool profile exposed only `go_home_and_sleep`, `close_menu`, and `stop_session` (`runner.py` bedtime tool filter). At21:30 the actor reported navigation tools unavailable. Repeated home calls continued. At25:00 the local bedtime reflex also precedes model choice, so textual steering cannot bypass it. This is a harness/controller limitation; the trial does not establish that model weakness caused the failure.

Next repair options, not implemented during this stream: let the bedtime controller execute a pocket-aware detour while preserving the bedtime objective, or retain the necessary normal navigation tools when the home controller reports a blocked route. First replay this exact south-pocket state and inspect why the route graph failed to select the known detour. A future readiness gate must demonstrate successful return from that pocket and normal sleep; a five-minute daytime check was insufficient.


## Bedtime recovery repair after the trial

Removed the bedtime-only tool filter and automatic1AM sleep reflex. Bedtime remains dynamic guidance in visual and compact actor contexts; normal navigation remains available. Each sleep attempt is now a model action, so a blocked result returns to the actor rather than triggering an automatic retry on every loop iteration. Offline regression tests cover forced bedtime and23:30/25:10 in both actor modes, and a failed1AM home attempt returning its no_route evidence to the next decision. All329 Python tests pass; no paid gameplay rehearsal.

This repairs the two controller regressions; the underlying map detour from Farm's south pocket remains to be verified. User prefers paid gameplay validation on stream. Gemini low has not been measured: compare future live calls against Luna low median4.12s/p95 5.65s; notify Rahul if Gemini low is not faster. No new public stream started by this repair.
