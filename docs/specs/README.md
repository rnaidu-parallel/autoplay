# Pending work specs

Status 2026-09-03: memory, narration, operator controls, and route/work fixes are implemented; 204 tests pass. Context JSON budgets are accepted. Four recorded
attempts were evaluated; none passed the 30-minute gate. Continuous time and short retry limits are implemented. See
[`../memory-narration-review.md`](../memory-narration-review.md) for evidence and the complete run command.

Self-contained briefs for the next packages, written 2026-09-03. Each is meant to be handed to a worker
(Codex `codex exec --sandbox workspace-write "Read and implement docs/specs/<file>"`) and reviewed by the
session before commit. Order:

1. `longrun-memory-part1.md` — notebook lessons/variety/roll-up, context caps, cache and latency report (no runner changes).
2. `narration-and-overlay-trace.md` — `say` on every actor tool, speech panel, three-line trace cards (independent of 1).
3. `longrun-memory-part2.md` — runner integration: variety validation, auto lessons, context budgets, stable block.
4. After channel connections and upload verification: one 30-minute OBS recording with the overlay feed running.
   Use the current command, approved budget, Finish & Save procedure, and checklist in [streaming.md](../streaming.md).
   `--max-minutes` is a deadline, not a graceful save request. The earlier $0.60 allowance has $0.272980 remaining.

`longrun-memory.md` is the full parent spec that parts 1 and 2 split.

Gotcha: when launching `codex exec` from a non-interactive shell, redirect stdin (`< /dev/null` or `< NUL`) or
Codex waits forever on "Reading additional input from stdin".
