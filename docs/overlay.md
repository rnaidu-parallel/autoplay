# Stream activity feed

The audience view places the current objective above the game, with two farmer messages in a sidebar.
The newest message appears at the bottom. Each contains a speaker, the last observed game time when the action
was inserted, a short public explanation, and an outcome. Raw tool names and arguments appear only in the operator view.
Messages use 24 px text, outcomes 18 px, and speaker/time labels 16 px. The older message uses a muted colour.

The objective uses two lines at 26 px, with a single fallback to 22 px for longer text. Excess text stays clipped.
Changes fade in and show "New goal" for four seconds; unchanged polls do not restart the effect. A missing objective
shows "Choosing what to do next." or "Deciding what to do today." during a director request.
The bottom of the sidebar shows elapsed thinking time, stalled, reconnecting, or stopped status.

The page checks the local feed every 500 ms. A message appears after the model returns its decision. The outcome
updates in place when the control finishes. Unchanged messages do not restart their animation. This is a live feed
of public explanations, not token-by-token private reasoning. Full events remain on disk.
Audience outcome wording describes the action result: "Action finished", "Couldn't" with a reason, or "Changed plans".
It does not imply that the objective or game save completed. The JSON feed retains the original outcome labels.

Game statistics, agenda panels, duplicate speech panels, and the bottom ticker are removed from the audience view.
The operator view retains guidance, Finish & Save, and Hold unsaved. It remains separate from the OBS browser source.

## Start the feed

Run this command from the repository root:

```powershell
.\harness\run-harness.ps1 overlay --run latest --port 8765
```

Open `http://127.0.0.1:8765/` for the audience view. Open `http://127.0.0.1:8765/?operator=1` for the controls.
See [operator controls](operator-controls.md) for command behavior and save verification.

## OBS composition

The output canvas stays at 1920×1080. The complete 16:9 game frame occupies 1536×864 at x=0, y=108.
The activity column occupies x=1536–1920 for the full height. The game is scaled, not cropped.
The objective fills the top 108 px band; a quiet stream description fills the bottom 108 px band.
No feed element covers the farmer, game clock, or hotbar.

Keep the audience Browser source at 1920×1080, above Game Capture. `broadcast/scene.cjs` applies both source
settings and the game bounds. It requires a running game when rebuilding capture; it never starts a broadcast.

## Preview and evidence

The local replay at `harness/runs/integration-qa/stream-preview-fable/preview.html` combines archived Spring 18 video
and its recorded decisions with this layout. It has play/seek and a disabled operator-view preview. It uses no model
calls. Historical behavior does not demonstrate the latest gameplay fixes.
