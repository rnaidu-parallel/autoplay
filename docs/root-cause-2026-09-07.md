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
