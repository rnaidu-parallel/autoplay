# Stream activity feed

The audience view shows the farmer's previous and latest action. Each entry contains a short public explanation,
the tool with compact arguments, and its recorded outcome. The header shows when the actor is choosing an action,
when the director is planning, and when the session is acting, stopped, or reconnecting.

The page checks the local feed every 500 ms. A message appears after the model returns its decision. The outcome
updates in place when the control finishes. Unchanged messages do not restart their animation. This is a live feed
of public explanations, not token-by-token private reasoning. Full events remain on disk.

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
The activity column occupies x=1536–1920 for the full height. The game is scaled, not cropped. Black space above
and below the game preserves its aspect ratio. No feed element covers the farmer, game clock, or hotbar.

Keep the audience Browser source at 1920×1080, above Game Capture. `broadcast/scene.cjs` applies both source
settings and the game bounds. It requires a running game when rebuilding capture; it never starts a broadcast.

## Preview and evidence

The local replay at `harness/runs/integration-qa/stream-preview-lean/preview.html` combines archived Spring 18 video
and its recorded decisions with this layout. It has play/seek and a disabled operator-view preview. It uses no model
calls. Historical behavior does not demonstrate the latest gameplay fixes.
