# Stream overlay

The overlay is a read-only companion process. It follows harness telemetry and state files. It does not launch or control Stardew Valley.

## Run the feed

From the repository root, run:

```powershell
cd .\harness
$env:PYTHONPATH = "."
python -m autoplay_harness overlay --run latest --port 8765 --state-dir harness/state
```

Replace `latest` with a run ID to replay and follow one run. The feed writes `harness/runs/<run-id>/overlay/state.json` and serves the overlay only on `http://127.0.0.1:8765/`.

## Add the browser source to OBS

1. Add a **Browser** source to the game scene.
2. Set the URL to `http://127.0.0.1:8765/`.
3. Set the width to `1920` and the height to `1080`.
4. Leave the browser background transparent.
5. Place the browser source above the game capture source.

The right column shows the game clock and resources, today's agenda, the active objective, the agent status, one recent lesson, and compact session totals. The Speech panel above the ticker shows the farmer's latest narration with the preceding line fading behind it. The bottom strip shows the five latest actor or director actions as three-line cards: narration, the tool and compact arguments, then its outcome. Director cards show the goal or milestone, and green outcomes completed, amber outcomes were blocked, and red outcomes were rejected or failed.
