# Broadcast setup and acceptance gate

For the preferred title, bio, and nonregional language setting, use
[public channel copy](channel-copy.md). The account values recorded below are historical;
the new copy has not yet been published from this session.

Updated 2026-09-03. Target: simultaneous Twitch, Kick, and YouTube. Twitch and Kick are the priorities.
Rahul's gate is **finish preparation → one successful 30-minute recorded rehearsal → public streaming**.
This replaces the earlier six-hour and two-test-stream proposal. No public broadcast has started.

## Current readiness

| Item | Status |
| --- | --- |
| Audience overlay and operator controls | Lean activity column: previous/latest actor action, public rationale, tool and outcome. Private operator controls remain separate. |
| OBS composition | 1920×1080 output; full game scaled to 1536×864 beside a 384-pixel activity column. Revised layout checked in archived-video browser replay; prior OBS audio/capture proof below predates this layout. |
| Local recording | Verified: H.264 High, 1080p30, stereo AAC 48 kHz; 11.93 seconds with audible game music. |
| Three simultaneous outputs | Verified against three local RTMP receivers for 14.9 seconds; one shared encoder. Actual platform ingest remains untested. |
| Twitch and Kick connections | Configured in portable OBS; both saved keys matched their dashboards. Kick shares the OBS encoders and synchronized start/stop. Actual platform ingest remains untested. |
| YouTube connection | Pending. Empty target retained with automatic start disabled. |
| Upload capacity | Not measured. Twitch + Kick need 12.32 Mbps; three outputs need 18.48 Mbps, before protocol overhead. |
| Gameplay preparation | Entrance-aware return/save verified live. New-work and retry guards pass 204 offline tests. Dialogue selection verified live. |
| 30-minute rehearsal | On hold until preparation is complete. |

The 2026-09-03 [recorded smoke](smoke-2026-09-03.md) completed 10 minutes plus a verified save, with the current overlay and game audio. Its final run had no capture/context/provider errors. Blocked routes and unfinished harvesting remain quality findings; the 30-minute gate is still pending.

Evidence: [broadcast preflight](../broadcast/preflight-2026-09-03.md). The short capture used the title screen and a
historical overlay feed. It did not load or change the save. A later route/save check advanced Spring 20 to 21.
See [current readiness and gameplay evidence](readiness-2026-09-03.md).

## Prepare OBS

This repository uses a separate portable OBS installation. Downloads, profiles, keys, logs, and recordings stay
under ignored `broadcast/local/`. Keep that directory private.

1. Open PowerShell at `E:\Claude\autoplay`.
2. Close this portable OBS instance before installing or updating its files.
3. Install the pinned packages if they are missing:

   ```powershell
   .\broadcast\setup.ps1
   ```

4. Seed the profile if it is missing. Existing local configuration is preserved.

   ```powershell
   $broadcastPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
   & $broadcastPython .\broadcast\configure.py
   ```

5. Open `broadcast\local\obs-studio\bin\64bit\obs64.exe` in File Explorer.
6. Confirm profile **Autoplay** and scene collection **Autoplay**.
7. Start the overlay in another terminal:

   ```powershell
   cd E:\Claude\autoplay
   .\harness\run-harness.ps1 overlay --run latest --port 8765
   ```

8. If the capture scene needs rebuilding, open the game first. Then run:

   ```powershell
   $broadcastNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
   & $broadcastNode .\broadcast\scene.cjs
   ```

The scene script configures capture only. It does not start gameplay, recording, or streaming.
Use `http://127.0.0.1:8765/` in OBS. Open `http://127.0.0.1:8765/?operator=1` separately for controls.
The **Break** scene provides a holding screen; switching to it is manual.
For a short local recording check, run `& $broadcastNode .\broadcast\preflight.cjs` with the game and overlay open.
It records 15 seconds and prints the file path. Play the file to check sound and picture.

## Connect the channels locally

1. In OBS **Settings → Stream**, connect Twitch or enter its key locally.
2. Open the **Multiple output** dock. Edit **Kick**.
3. Copy Kick's server URL and key from its Creator Dashboard into the local fields.
4. When adding YouTube, copy its server URL and key from YouTube Studio into its target.
5. For configured extra outputs, retain **Get from OBS** video/audio and synchronized start/stop. Keep automatic start disabled on empty targets.
6. Set the title and Stardew Valley category on each platform.
7. Leave all outputs stopped until the rehearsal passes.

Never paste keys into chat or commit the local profile. Enable YouTube Live early if needed; first-time activation
can take up to 24 hours. [YouTube setup](https://support.google.com/youtube/answer/2907883?hl=en)
Kick's dashboard supplies both its URL and key. [Kick setup](https://help.kick.com/en/articles/7066931-how-to-stream-on-kick-com)

All outputs use NVENC H.264, 1920×1080 at 30 FPS, CBR 6000 kbps, two-second keyframes, and stereo AAC 160 kbps.
Local MKV recording shares that encoder. This keeps encoding load low.

Checked on 2026-09-03: Twitch and Kick account `can_we_reverse_entropy`; title
**An AI learns to farm | Autonomous Stardew Valley**; category **Stardew Valley**.
Twitch language is English; Kick retains English (India). Twitch uses automatic server selection,
low latency, saved broadcasts, automatic VOD publication, and enabled clips. Enhanced Broadcasting
is off. Kick uses the dashboard's RTMPS endpoint and the shared OBS video/audio encoders.
OBS remains open with streaming and recording stopped. No internet ingest or current scene/audio test
was performed during account setup.

The configuration fits Kick's H.264/CBR requirements. YouTube recommends 10 Mbps for H.264 1080p30;
the shared 6 Mbps setting is a compromise for the priority platforms and needs visual evaluation.
[Kick requirements](https://help.kick.com/en/articles/7066931-how-to-stream-on-kick-com),
[YouTube recommendations](https://support.google.com/youtube/answer/2853702?hl=en)

Three copies of 6000+160 kbps equal 18.48 Mbps before overhead. Aim for a stable 25–30 Mbps upload as an
engineering margin. Local loopback does not measure internet upload or platform ingest. No paid relay is configured.

Keep Twitch's picture and experience at least equal to the other destinations. Do not put merged cross-platform
chat on Twitch or direct Twitch viewers away to another simultaneous stream.
[Twitch simulcasting terms](https://legal.twitch.com/en/legal/terms-of-service/#11-simulcasting)
If the channel belongs to the Kick Partner Program, check its multistream toggle and payout terms.
[Kick partner multistreaming](https://help.kick.com/en/articles/11091744-multistreaming-on-the-kick-partner-program)

## Run the rehearsal after preparation

Do not run this section yet. Connect the channels and verify upload capacity first.
The earlier $0.60 model allowance has $0.272980 remaining. A larger allowance needs Rahul's approval.
The broadcast preflight used no model calls.

1. Confirm the game save matches the latest checkpoint. See [operator controls](operator-controls.md).
2. Set `$sessionBudget` to the approved model allowance.
3. Start SMAPI separately, choose **Windowed** mode, and load the matching save. Then start the harness from the repository root:

   ```powershell
   .\harness\run-harness.ps1 run --continuous --resume-checkpoint --no-launch-game --budget-usd $sessionBudget
   ```

4. Start the overlay feed if it is not running.
5. Check the OBS preview and **Game audio** meter.
6. Select **Start Recording**. Leave streaming stopped.
7. Record 30 minutes of autonomous play. Note every operator intervention.
8. After the measured window, select **Finish & Save** in the operator panel.
9. Wait for **Saved and stopped** and its checkpoint ID.
10. Stop OBS recording. Report the save tail separately from the measured 30 minutes.
11. Review the recording, event log, control receipts, and model cost report.
12. Close the game after the checkpoint is verified. `--keep-game-open` leaves it open for handoff.
13. Stop the overlay and close OBS after review.

Do not use `--max-minutes 30` as the save signal: it is a deadline, not a request to return home.
For a mid-day Python repair, use `--attach` instead of `--resume-checkpoint`. See [repair and reattach](operator-controls.md#repair-python-during-a-recording-or-stream). Attached runs preserve the display mode. Fullscreen desktop capture on this machine failed while OBS continued to show the game; the former WinRT fallback could return an old title splash.
If a limit ends the harness before saving, keep the game open and complete the save before closing it.
Finish & Save does not stop OBS or the broadcasts.

Acceptance checks:

- At least 30 continuous minutes with readable narration/action trace and audible game sound.
- No artificial clock freezing, pass-outs, or repeated failed-action loop lasting over two minutes.
- Blocked travel leads to an alternative route or activity; the farmer returns home and saves autonomously.
- New-work claims use evidence of new work. Existing crops do not prove new planting.
- Curiosity and remembered experiences appear naturally; skills do not confine the farmer's choices.
- Guidance and Finish & Save receive acknowledgements and produce the expected behavior.
- No unplanned manual gameplay rescue. Deliberate control checks and the save tail are reported separately.
- No sustained recording/rendering skips, desktop capture, or white OS cursor.
- Spend stays within the approved allowance; the next session loads the matching checkpoint.

## Go live after the rehearsal passes

1. Review the acceptance result with Rahul.
2. Start the game, harness, and overlay with the approved session settings.
3. Check the OBS preview, game audio, and each configured destination.
4. Select **Start Streaming**. Kick starts with Twitch. YouTube starts only after its target is configured and automatic start is enabled.
5. Verify picture and audio on each platform. Local preflight does not prove platform acceptance.
6. At session end, select **Finish & Save** and wait for the checkpoint.
7. Select **Stop Streaming** in OBS. Confirm every configured output stopped.

Public chat control, progression-state expansion, and fishing/mining/combat coverage remain separate work.
