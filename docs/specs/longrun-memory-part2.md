# Spec: long-run memory, part 2 — runner integration (variety, auto lessons, context budgets, cache ordering)

Repo: E:\Claude\autoplay (Windows 11). This is PART 2 of `longrun-memory.md` in this directory; read it
fully. Part 1 (already in the working tree) added to `notebook.py`: item `category`, `category_mix`, `recent_themes`,
`variety_errors(day, items, theme, mode)`, `add_lesson(key, text, kind, day)`, `top_lessons(n)`, `context(day,
role)`, `rollup_week()`; to `world.py`: summary caps; to `telemetry.py`: learned caps; to `report.py`: the cache and
latency section; to `openrouter.py`: deterministic serialization. Read those implementations first and use them
as they are (adapt names only if part 1 chose different ones; say so in the report).

Another session may be editing `tools.py`, `prompts.py`, `state_actor.py`, `overlay.py`. Do NOT edit those four
files; if the `plan_day` tool schema still lacks `category`, add it in `tools.py` ONLY if the file has no
uncommitted changes (`git status --porcelain harness/autoplay_harness/tools.py` empty); otherwise report it as a
follow-up. Same rule for the one-sentence director prompt addition in `prompts.py`.

## Do in part 2 (runner.py + tests/test_runner.py + docs)

1. Variety: call `notebook.variety_errors(...)` inside `_agenda_errors` for morning and refill modes; feedback
   lists the errors; retries/accept-with-gaps behaviour unchanged.
2. Auto lessons: in the actor result path, on any `rejected` result call `add_lesson("rejected:<reason>", <one-line
   human text>, "auto", day)`; on `stall_detected` → `stall:<tools>`; on `tick_budget_exhausted` → `timeout:<tool>`;
   on a `blocked_path` record → `blocked_path:<from>-><to>`; on `block_objective` after ≥ 8 decisions on that
   objective → `wasted_decisions:<goal[:40]>`. Human texts for known reasons: door_closed_until_<t> → "<dest> is
   closed until <t formatted>; plan visits after that."; outside_crop_zone → "Planting and tilling must stay in the
   crop zone."; bedtime_not_allowed_before_2000_unless_exhausted → "Bedtime is after 8 PM unless exhausted; fill
   the day."; default → "Avoid: <reason with underscores replaced>."
3. `reflect` tool: if part 1 did not add `lessons` to the tool schema (it could not, `tools.py` is off-limits), the
   runner accepts an optional `lessons` list in the arguments when present and records each as a reflection lesson;
   report the schema change as a follow-up if `tools.py` was untouchable.
4. Context: build `notebook` via `context(day, role)` per role; place `notebook` and `world` in the stable block
   right after the ledger (section D/F.3); apply the budget estimator and the trim order from section C with the
   `context_trimmed` telemetry event; constants `ACTOR_CONTEXT_BUDGET = 4500`, `DIRECTOR_CONTEXT_BUDGET = 8000`.
5. Weekly roll-up: call `notebook.rollup_week()` in `_plan_day_if_needed` before `start_day`.
6. Tests in `tests/test_runner.py`: variety feedback path; auto lesson on rejection with count increment; budget
   enforcement with a synthetic oversized state (assert trim order and the event); stable-block identity across two
   consecutive requests with unchanged notebook/world (use the fake client to capture contexts).
7. `docs/harness.md`: one paragraph under Memory and compaction describing the runner side (budgets, trim order,
   stable block).

## Validation
```
cd E:\Claude\autoplay\harness && set PYTHONPATH=. && C:\Users\Rahul\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest tests.test_runner tests.test_notebook -q
```
Repo-local temp folder `harness/.tmp-longrun2` if needed; delete after. Do NOT launch the game. No model API
calls. Do not pause for routine choices; list them.

## Report
What changed; test tail; measured estimated context sizes (actor, director) from the budget test; follow-ups.

## Hard rules
Do not commit, stage, stash, or touch git state. Do not create branches. Edit the working tree only.
