# Pending work specs

Status 2026-09-03: parts 1, narration, and part 2 are implemented; 164 tests pass. Context JSON budgets are accepted; the 30-minute recorded
rehearsal and evaluation are in progress. See
[`../memory-narration-review.md`](../memory-narration-review.md) for evidence and the complete run command.

Self-contained briefs for the next packages, written 2026-09-03. Each is meant to be handed to a worker
(Codex `codex exec --sandbox workspace-write "Read and implement docs/specs/<file>"`) and reviewed by the
session before commit. Order:

1. `longrun-memory-part1.md` — notebook lessons/variety/roll-up, context caps, cache and latency report (no runner changes).
2. `narration-and-overlay-trace.md` — `say` on every actor tool, speech panel, three-line trace cards (independent of 1).
3. `longrun-memory-part2.md` — runner integration: variety validation, auto lessons, context budgets, stable block.
4. Then: a 30-minute recorded session (`run --max-minutes 30 --record-video --video-retention-segments 0 --budget-usd 0.60`
   with the overlay feed running) and the evaluation checklist in `docs/streaming.md`.

`longrun-memory.md` is the full parent spec that parts 1 and 2 split.

Gotcha: when launching `codex exec` from a non-interactive shell, redirect stdin (`< /dev/null` or `< NUL`) or
Codex waits forever on "Reading additional input from stdin".
