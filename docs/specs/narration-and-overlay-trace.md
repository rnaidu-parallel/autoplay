# Spec: narration channel and readable overlay trace

Repo: E:\Claude\autoplay (Windows 11). Read `harness/autoplay_harness/tools.py` (`function_tool`, `ACTOR_TOOLS`,
`DIRECTOR_TOOLS`), `prompts.py`, `state_actor.py` (`STATE_ACTOR_PROMPT`, `STATE_ACTOR_TOOLS`), `overlay.py`,
`overlay/index.html`, `tests/test_overlay.py`, and sample the events of
`harness/runs/baa41570-f885-4914-8a65-45e01f71a120/events.jsonl` (an `actor_decision` carries `tool`, `arguments`,
`content`, `reasoning`; a `tool_result` carries `tool`, `result.status`, `result.reason`).

Other sessions are editing `runner.py`, `__main__.py`, `forever.py`, `notebook.py`, `world.py`, `telemetry.py`,
`report.py`, `openrouter.py`, C# sources, and `tests/test_runner.py`. Do NOT touch any of those. The actor's
`arguments` are already recorded in telemetry verbatim, so no runner change is needed for narration.

## Why (Rahul)

Viewers should see the agent's own words next to what it does: a simple trace of "what it is thinking → the tool
it called → the action it took → what happened", readable at a glance on stream.

## Objective (verifiable)

### A. Narration argument on every actor tool (`tools.py`, `state_actor.py`, `prompts.py`)
- Add a required string property `say` (maxLength 140) to EVERY actor tool schema (both `ACTOR_TOOLS` and the
  state-first subset; `inspect_scene` too). Description: one short first-person sentence, in character as the
  farmer, saying what you are doing or noticing right now, for the audience; no tool names, no coordinates, no
  brackets. Director tools are unchanged.
- Prompts: one paragraph in `ACTOR_SYSTEM_PROMPT` and two sentences in `STATE_ACTOR_PROMPT` establishing the
  voice: a warm, slightly wry farmer settling into Pelican Town; present tense; vary phrasing; mention what is
  seen or intended, not mechanics; never mention being an AI, a harness, tools, or coordinates. Keep prompt
  edits minimal and at the END of each prompt (cache-friendly).
- Add a `GAME_BRIEF` sentence: the `say` line is shown to viewers; it must be honest about what the state shows.

### B. Overlay trace (`overlay.py`, `overlay/index.html`, `tests/test_overlay.py`)
- Each action entry gains `say` (from `actor_decision.arguments.say`, falling back to `content` when present) and
  `toolName` (raw tool name) in addition to `summary` and `outcome`.
- A new "Speech" panel above the ticker on the right column: the latest `say` in a large readable font (quote
  style), fading to the previous one; if empty, hide the panel.
- The ticker entries become three-line cards: line 1 the `say` (truncated to ~70 chars), line 2 the tool in
  monospace (`navigate_to (61,20)` style: tool name plus a compact argument gist), line 3 the outcome with color.
  Newest on the left. Director decisions appear as "Director: <goal or milestone>" cards with a distinct color.
- The "Now" panel adds a one-line "Thinking…" state with the role when `status == thinking`.
- Argument gist rules: tiles → `(x,y)` up to 3 then `+N`; location/destination → the name; buttons → joined keys;
  count/seed_slot → `×N`; others omitted.
- Keep everything else as is. Tests: `say` propagation, gist formatting, director card, speech panel data.

### C. Docs
- `docs/overlay.md`: describe the Speech panel and the three-line cards (four sentences).

## Validation
```
cd E:\Claude\autoplay\harness && set PYTHONPATH=. && C:\Users\Rahul\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest tests.test_overlay tests.test_state_actor -q
```
Then replay the sample run through the feed for a few seconds (`python -m autoplay_harness overlay --run baa41570-f885-4914-8a65-45e01f71a120 --port 8766`), fetch `/state.json` once, print the first two `actions` entries,
and terminate the feed. Do NOT launch the game. No model API calls. Do not pause for routine choices; list them.

## Files to touch
`tools.py`, `state_actor.py` (prompt + tool list only), `prompts.py`, `overlay.py`, `overlay/index.html`,
`tests/test_overlay.py`, `tests/test_state_actor.py` (schema test), `docs/overlay.md`.

## Hard rules
Do not commit, stage, stash, or touch git state. Do not create branches. Edit the working tree only.
