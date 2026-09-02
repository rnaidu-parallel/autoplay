# Gameplay evaluation

Correctness is judged against observed game state. Model prose, a valid tool call, and a completed input are not proof that the intended task happened.

## What is checked now

| Layer | Current check | Limit |
| --- | --- | --- |
| Allowed control | Strict tool schema, allowed keys, tick limits, current-frame pointer coordinates, bounded action count | A valid action can still be a poor choice |
| Movement | Collision-aware navigation; unchanged-position guard for long movement holds; stop on blocks and transitions | Does not prove the route was efficient or strategically useful |
| Planting | Each requested tile must be observed empty tilled soil; verify a live crop on that exact tile and one seed consumed before continuing | Only the local planting operation has this crop/seed contract; generic C/click does not |
| Tilling | Each requested tile must appear in `tillableNearby`; stand beside it, verify the Hoe is selected, face it, swing, and verify the tile became empty tilled soil in `cropsNearby` | Proven live for three Farm tiles; it does not judge whether that soil was worth tilling |
| Watering | Each requested tile must be an observed crop with `watered` false; verify the Hoe/Can selection, the resulting `watered` state, and one unit of `wateringCanWater` spent; stop with `watering_can_empty` instead of refilling | Not yet proven live: no dry crop has been reachable in a validation run |
| Bedtime | Walk onto the exposed `bedTile`, answer the sleep question, and require `day == previous_day + 1` with `worldReady`, `playerFree`, `menu` none, and `location` FarmHouse | Proven live from inside the FarmHouse only; the Farm-to-FarmHouse hop is untested |
| Goal | Harness evaluates a structured success condition before decisions and after tool results | A weak condition can certify a weak objective; five crops does not prove an entire day was played well |
| Cost and pacing | Per-attempt response times, retries, tokens, cost, cache, rejected/blocked action rate | Low latency and cache hits are efficiency measures, not correctness |
| Evidence | Full state/result logs plus optional client screenshots | Fullscreen GDI video recording remains unreliable; long-session viewing quality is unvalidated |

The director proposes milestones. It does not grade its own gameplay or complete objectives. No additional LLM judge is used. A successful bootstrap and a verified final objective require no evaluation-model calls.

`report` includes the initial condition at start/end, final structured state, verified local seed plantings, and crop deltas within each location. It includes final tool-result state even when the action cap prevents another observation. Moving from a room with zero crops to a farm with existing crops must not earn planting credit. `wasted_decision_rate` counts reported rejected/blocked/timeout outcomes only; it misses valid but unproductive generic inputs.

Report provider availability separately from game competence. An HTTP 429 prevents a decision; it is not an incorrect movement choice. Truncated/invalid model output, rejected game targets, and ineffective input are different failure classes. `actor_model_calls` includes failed logical calls; `actor_decisions` counts successfully parsed decisions. The call cap includes failures, while physical retries remain separately bounded and recorded.

Latency reports now distinguish mean model response time from the full actor cycle (observation, request, and game action). A five-seed batch is one cycle with several ordinary inputs, not five cycles. `cache_by_role` separates actor/director reads and writes and excludes each role's first logical call in its `after_first_call_hit_rate`; this is not a guarantee that all subsequent calls are warm. State-only replay tests check tool choice and argument validity without executing controls. Only live runs prove crop/seed changes. Use `--actor-mode visual` to compare perception paths; preserve the model, reasoning effort, and game save.

## Controlled planting comparison

1. Close the game after each bounded trial. Do not perform an overnight save during this comparison.
2. Use the same saved game, objective, success condition, action cap, decision cap, and director interval.
3. Add `--isolated-state` to create the objective ledger under the run directory. This isolates harness memory; it does not reset the game save.
4. Record the model, provider, reasoning setting, prompt/tool versions, and initial state. Confirm the initial goal is false.
5. Verify each planted tile and seed decrement. Check health and unintended inventory changes. Evaluate completion separately from speed.
6. Compare useful progress per wall-clock minute, model calls per completed task, failed actions, and total cost.
7. Repeat from the same save before changing the default model. Small samples are acceptance tests, not a reliable ranking.

Example:

```powershell
.\harness\run-harness.ps1 run `
  --model google/gemini-3.7-flash --reasoning-effort low --isolated-state `
  --objective 'Plant five existing Parsnip Seeds on empty tilled Farm tiles. Use go_to_location to reach Farm, then plant_seeds for observed soil.' `
  --success-condition 'location is Farm, plantedCrops >= 5, worldReady is true' `
  --max-actions 12 --max-decisions 6 --director-interval 4 --save-frames
```

## Missing acceptance coverage

- Water dry crops; prove the intended tiles become watered and track water/stamina costs.
- Reach the farmhouse from the Farm exterior, and verify crop persistence across the night. Sleeping from inside the FarmHouse and loading the next day now passes.
- Recover from a blocked route or ineffective input without repeating it indefinitely.
- Complete shop/NPC/menu tasks using inventory, money, and dialogue evidence.
- Complete several consecutive days without passing out, damaging crops, abandoning goals, or wasting resources.
- Review reliable video for readable decisions, pauses, and coherent/interesting play. Streaming quality includes human judgment; it is not captured by a single success predicate.

Until these pass repeatedly, claim only the specific tested skill. Current evidence supports bounded navigation and verified planting, not general competent autonomous Stardew play.
