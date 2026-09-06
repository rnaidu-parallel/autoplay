# Mailbox and general stall recovery — 5 September 2026

The mailbox failure is reproduced and fixed. Repeated failed tasks now release all
attention gates, including notices, even when no objective is active. Pending work
remains pending; rejected model output and unsuccessful input are never accepted
as completion.

## Causes and changes

- At Farm tile `(68,15)`, facing south, the cursor on `(68,14)` redirected the X
  action away from the mailbox at `(68,16)`. The bridge now exposes the mailbox's
  screen coordinates. `check_mail` uses the latest coordinates after navigation
  and facing, right-clicks once, and requires `LetterViewerMenu` to open.
- `location.IsFarm` also exposed outdoor mailbox coordinates inside FarmHouse.
  Mailbox targets are now limited to the Farm location type; the Python skill
  also rejects calls from other locations.
- The windowed probe exposed a separate shared input error: an SDL window pointer
  was passed to Win32 focus and client-to-screen APIs. The bridge now resolves and
  caches the process's native game HWND. This fixes the window-origin offset and
  the `foregroundIsGame` diagnostic. MonoGame documents the differing handle types
  in its [GameWindow reference](https://docs.monogame.net/api/Microsoft.Xna.Framework.GameWindow.html).
- Retry recovery previously required an active objective and released gates only
  when the last tool was `check_mail` or `check_journal`. It now also covers invalid
  quest/notice responses and alternating inspection/review requests. Mail, journal
  and notice restrictions share the persisted daily release. Prompt and overlay
  state reflect that release. Inventory and crop-zone restrictions remain enforced.
- Objective replacement no longer resets the final stall counter. Clock ticks,
  stamina consumption and animation flags do not prove progress. Mail, letter text
  and quest changes do. The watchdog also recognizes repeating sequences of two,
  three or four states after three repetitions.

## Recovery limits

1. After three failed attempts at the same target, or six decisions without
   observed progress, defer the current objective if one exists. Release attention
   gates for the current game day. Retain the failure evidence and request a new plan.
2. At eight stalled decisions, request a different tactic or objective.
3. At sixteen stalled decisions, attempt window refocus and the existing display
   recovery. Preserve the display mode during an attached run.
4. At twenty-four stalled decisions, stop autonomous decisions and mark the run
   `needs_attention`. Leave the unsaved game open and open its ordinary pause menu
   when possible. The forever supervisor does not restart this terminal state.

These counts are decision limits, not wall-clock deadlines. A short cycle needs
three repetitions before it starts accumulating stalled decisions. This bounds
the tested patterns; it is not proof against every possible gameplay loop.

## Validation

- 296 offline tests passed, including new cases for no-objective review loops,
  rejected notice/quest responses, persisted gate release, repeated routes/menu
  cycles, stamina-only changes, replanning, mailbox target freshness, and terminal
  handoff. Existing inventory, objective, operator and audience tests still pass.
- C# build passed and deployed the bridge locally. The pre-existing CS9057 analyzer
  version warning remains: analyzer compiler 4.9.0.0 versus SDK compiler 4.3.0.0.
- Live check used the existing Carbon save, Spring 13, without model calls or an
  overnight save. Both windowed and borderless modes reproduced the old X failure
  and opened a letter with the corrected skill. Unread mail fell 3→2 and then 2→1.
  Native foreground diagnostics were true in both modes.
- All save-file SHA-256 hashes matched before and after the final live check.
  Startup preferences were restored. Owned game processes were stopped.
- [Compact live evidence](../artifacts/recovery-2026-09-05/mail-evidence.json).
  Local detailed probe: `harness/runs/mail-recovery-probe/`.

A multi-day autonomous stream has not been rerun. These changes implement incident
recovery; the separate screenshot, skill-continuation and farming-throughput
recommendations from the review remain future work.
