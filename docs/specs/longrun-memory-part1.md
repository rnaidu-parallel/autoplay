# Spec: long-run memory, part 1 — notebook lessons/variety/roll-up, context caps, cache and latency report

Repo: E:\Claude\autoplay (Windows 11). This is PART 1 of the spec at `longrun-memory.md` in this same
directory: read that file fully for the intent, the measured numbers, and section F. Part 1 implements ONLY the
pieces that do not touch `runner.py`, because another session is editing `runner.py` right now. Do NOT open or
modify `runner.py`, `__main__.py`, `forever.py`, `tools.py`, `prompts.py`, `state_actor.py`, `overlay.py`, the
`overlay/` folder, C# sources, or `tests/test_runner.py`.

## Do in part 1

1. `notebook.py` (+ `tests/test_notebook.py`): sections A (category on items, `category_mix`, `recent_themes`;
   validation helpers as pure functions returning error lists so the runner can call them later:
   `variety_errors(day, items, theme, mode)` implementing the rules in A), B (lessons store: `add_lesson(key, text,
   kind, day)` with merge/count/cap 40, `top_lessons(n)`), C (role-specific `context(day, role)` with the actor and
   director shapes, `rollup_week()`, `weeks` entry). Keep the existing `context(day)` signature working (default
   role "director") so current callers do not break.
2. `world.py` (+ `tests/test_world.py`): the summary caps in section C.
3. `telemetry.py` (+ `tests/test_telemetry.py`): `learned` cap 8 and 160-char trim on record.
4. `report.py` (+ `tests/test_report.py`): section F.1 "Cache and latency" (per role: calls, prompt median/max,
   cached share, completion median, model latency p50/p95 from `attempts[].latency_ms`, bridge p50, cost per call,
   cost per in-game hour from day/time deltas, share of `inspect_scene` decisions). Validate against
   `harness/runs/baa41570-f885-4914-8a65-45e01f71a120/events.jsonl` and paste the section in your report.
5. `openrouter.py` (+ `tests/test_openrouter.py`): section F.2 ONLY — deterministic serialization of the system
   message and tools (sort_keys, fixed separators) and a test that two requests for the same role/mode produce
   byte-identical `messages[0]` and `tools`. No other change to openrouter.py.
6. `docs/harness.md`: the Memory and compaction and Daily planning updates from section E (describe the runner
   integration as "applied by the runner" even though part 2 wires it).

## Validation
```
cd E:\Claude\autoplay\harness && set PYTHONPATH=. && C:\Users\Rahul\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest tests.test_notebook tests.test_world tests.test_telemetry tests.test_report tests.test_openrouter -q
```
Run only those modules (the full suite may be in flux from the other session). Use a repo-local temp folder
named `harness/.tmp-longrun1` if the sandbox denies temp dirs and delete it after. Do NOT launch the game. No
model API calls. Do not pause for routine choices; list them.

## Report
What changed (per file); test tail; the printed Cache and latency section; `git status --porcelain`; choices.

## Hard rules
Do not commit, stage, stash, or touch git state. Do not create branches. Edit the working tree only.
