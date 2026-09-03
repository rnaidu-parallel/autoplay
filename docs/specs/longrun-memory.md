# Spec: long-run play — variety across days, a lessons store, and hard context budgets

Repo: E:\Claude\autoplay (Windows 11). Read `docs/harness.md` (Memory and compaction, Daily planning and the
notebook, Prompt caching), `harness/autoplay_harness/notebook.py`, `runner.py` (`_context`, `_plan_day_if_needed`,
`_request_agenda`, `_agenda_errors`, `_reflect_before_sleep`, `_update_stall_watchdog`, the rejection paths in
`_execute_actor_tool` that return `{"status": "rejected", ...}`), `telemetry.py` (`context()` and `_compact`),
`world.py` (`summary`), `state_actor.py` (`compact_state`), `prompts.py`, `tools.py` (`plan_day`, `reflect`), and
`tests/test_notebook.py`, `tests/test_runner.py`.

Measured on run baa41570 (day 17, 142 calls): actor prompt median 6,020 tokens (max 13,091), cache hit 34%;
director prompt median 11,160 tokens, cache hit 24%; cost per actor call $0.0013. Earlier runs with the small
context: ~1,900 prompt tokens, ~50% cache, $0.0003 per call.

## Why (Rahul, 2026-09-03)

The stream never ends. The agent must not do the same thing every day, must not repeat mistakes, and its context
must keep updating rather than accumulating.

## Objective (verifiable)

### A. Variety (notebook + planning validation + prompt)
- Each agenda item gets a `category` from a fixed list: `farming, clearing, exploring, social, shopping, fishing,
  mining, foraging, crafting, event, home`. Add it to the `plan_day` tool schema (required per item).
- `Notebook.category_mix(day)` → counts by category for that day's items; `Notebook.recent_themes(n)`.
- Morning-plan validation adds: the theme must not equal any of the last 3 days' themes (case-insensitive, after
  trimming); at least 2 categories in today's plan must not be among yesterday's top-2 categories by count;
  no more than 3 items in the same category. Refills: at most 1 item in yesterday's dominant category. Feedback
  lists what to change. (Keep the existing rules: 5–8 items, evening slot on morning plans, exploration item
  while unvisited remains, no "go home" before evening.)
- `DIRECTOR_SYSTEM_PROMPT`: one short paragraph: vary the days (theme and categories), rotate through the
  category list over a week, use the season and weather, and prefer goals that produce something viewers can
  see change.

### B. Lessons store (harness-owned) + reflection
- `notebook.json` gains `lessons`: list of `{ "id", "text", "kind": "auto|reflection", "count", "first_day",
  "last_day", "key" }` capped at 40 (merge by `key`; when full, drop the oldest lowest-count auto lesson).
- Auto lessons recorded by the runner (with a stable `key` so repeats increment `count` instead of adding):
  `rejected:<reason>` for any tool result with status `rejected` (e.g. `door_closed_until_900`,
  `outside_crop_zone`, `bedtime_not_allowed_before_2000_unless_exhausted`, `stop_not_allowed_in_forever_mode`);
  `blocked_path:<from>-><to>`; `stall:<tools>` on `stall_detected`; `timeout:<tool>` on `tick_budget_exhausted`;
  `wasted_decisions:<objective>` when an objective is blocked after ≥8 decisions. Text is a one-line
  human sentence ("Pierre's shop is closed before 9:00 AM; plan town visits after 9.").
- `reflect` tool gains `lessons: [str]` (1–3 required). Reflection prompt sentence: name what was repeated today
  and what to do differently tomorrow.
- Context: `notebook.context(day)` includes `lessons`: the top 6 by `(count desc, last_day desc)`, text only.
- The runner already enforces several mechanically; add nothing new there.

### C. Hard context budgets
- Define per-role budgets in `runner.py` constants: actor ≤ 4,500 prompt tokens, director ≤ 8,000, estimated as
  `len(json) / 3.5` characters-per-token (document the estimator).
- `notebook.context(day)` for the ACTOR: today's theme + agenda items as `id|slot|status|goal[:70]` strings only,
  no yesterday, no farm plan zones (only the crop-zone rectangles as a compact list), lessons top 3. For the
  DIRECTOR: today's agenda, yesterday's reflection (≤ 400 chars), farm plan zones, lessons top 6, `learned` top 8,
  category mix of yesterday, recent themes (3).
- Weekly roll-up: when a day starts and there are ≥ 7 day entries older than yesterday, `Notebook.rollup_week()`
  replaces them with one `weeks` entry `{ "days": [a,b], "summary": <concatenated reflections trimmed to 600
  chars>, "categories": totals }`; day entries older than yesterday are then dropped from `days` (they remain in
  `weeks`). The director context shows the latest week summary (≤ 300 chars).
- `world.summary`: cap `unvisited` at 6, `exits` at 8, `routeHome` at 6 hops; `closedNow` ≤ 4; `blockedNow` ≤ 4.
- `telemetry.context()` (memory): keep the existing recent limit; ensure `learned` there is capped at 8 and
  each item ≤ 160 chars (trim on record).
- Enforcement: after building a context, if the estimate exceeds the budget, trim in this order until it fits:
  wiki results → memory recent to 6 → lessons to 3 → agenda goals to 40 chars → drop `nearbyObjects` beyond 12
  (actor state) → drop `cropsNearby` rows beyond 24. Record telemetry `context_trimmed` with what was cut. Never
  drop the objective ledger, `harnessLastResult`, or the active objective.
- Test: build contexts from a synthetic large state and notebook and assert both budgets hold and the trim
  order is followed.

### D. Prompt-cache friendliness
- Keep the stable prefix stable: system prompt, tool schemas, then the objective ledger (already split as
  `stable_context`); place `notebook` and `world` AFTER the ledger and BEFORE the fast-changing state, since they
  change a few times a day. Do not reorder anything else.

### E. Tests and docs
- `tests/test_notebook.py`: lessons merge/cap, category_mix, recent_themes, rollup_week.
- `tests/test_runner.py`: variety validation feedback; auto lesson recorded on a rejection with count increment;
  context budget enforcement.
- Existing tests pass.
- `docs/harness.md`: update Memory and compaction (lessons, weekly roll-up, budgets with the numbers) and Daily
  planning (categories and variety rules).

## Files to touch
`notebook.py`, `runner.py`, `tools.py`, `prompts.py`, `world.py` (summary caps only), `telemetry.py` (learned cap
only), `report.py` (section F), `openrouter.py` (ONLY deterministic JSON serialization for section F.2, nothing else),
`tests/test_notebook.py`, `tests/test_runner.py`, `tests/test_world.py` (caps), `tests/test_openrouter.py`, `tests/test_report.py`, `docs/harness.md`.

## Do NOT touch
`farming.py`, `objectives.py`, `recording.py`, `capture.py`, `supervisor.py`, `forever.py`,
`overlay.py`/`overlay/`, `__main__.py`, C# sources, other docs. No refactoring or reformatting of adjacent code;
match existing style; every changed line must trace to the objective. Do not pause for routine choices; list them.

## Validation (run yourself)
```
cd E:\Claude\autoplay\harness && set PYTHONPATH=. && C:\Users\Rahul\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests -q
```
Repo-local temp folder if needed; remove after. Do NOT launch or connect to the game. No model API calls.

## Report format
What changed (per file); Test tail; `git -C E:\Claude\autoplay diff --stat`; the measured estimated context
sizes for actor and director from the new test; Choices made.

## Hard rules
Do not commit, stage, stash, or touch git state. Do not create branches. Edit the working tree only. Pause only
if a hard invariant blocks you.

## F. Caching, latency, and cost review (added by Rahul 2026-09-03)

Measure first, then change only what the numbers justify. Read `openrouter.py` (request body, `session_id`, how
`tools` and the system prompt are sent, any `cache_control`), `runner.py` `_context` (the `stable_context` split),
and `report.py`.

1. `report.py`: add a "Cache and latency" section per role: calls, prompt median/max, cached-token share, completion
   median, model latency p50/p95 (from `attempts[].latency_ms`), bridge p50, cost per call, cost per in-game hour
   (from the day/time deltas), and the share of decisions that were `inspect_scene` (pure observation, no action).
2. Stable prefix: OpenAI prefix caching needs an identical prefix of ≥ 1024 tokens. Verify with a unit test that
   the request JSON for the same role and mode has byte-identical `messages[0]` (system) and `tools`, and that the
   first user message starts with the ledger block. Ensure JSON serialization uses `sort_keys=True` and fixed
   `separators` everywhere the context is built. The two actor variants (state-first and visual) are distinct
   prefixes; that is acceptable — do not merge them.
3. Semi-stable block: move `notebook` (agenda/theme/lessons) and `world` summary into the stable-context block
   right after the ledger, since they change only a few times a day; keep `harnessLastResult`, state, counters,
   and memory in the dynamic block. Add a test that two consecutive actor requests with unchanged notebook/world
   share the same stable block bytes.
4. Director cost: with the budgets in C the director prompt must drop below 8,000 tokens; verify in the test.
5. Do NOT change model, reasoning effort, image size, max_tokens, provider, or timeouts — those are Rahul's
   decisions; list any such candidate optimization with the measured evidence under "Choices made / proposals".
