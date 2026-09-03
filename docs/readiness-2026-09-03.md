# Readiness — 3 September 2026

The local overlay and broadcast setup are prepared for simultaneous Twitch, Kick, and YouTube. Harness preparation
is complete for the next supervised rehearsal. Connect the channels and verify upload capacity before starting it.
One successful 30-minute recorded rehearsal remains the public-streaming gate.

## Implemented and verified

- `b3bf9f7`: portable OBS 32.2.2, multiple-output plugin, 1080p30 NVENC capture, game-process audio, audience overlay,
  separate operator view, and manual Break scene. Last two narration lines persist through director updates.
- `ad203b1`: failed paths scoped to the entrance used; route search can leave and re-enter a location. Travel discards
  observed blocked/missing exits and tries another route within its existing action budget. Failed command arguments
  are included in travel telemetry. Old entries without entrance information remain conservative until they expire.
- `seedsSown`: the game's cumulative planting statistic, independent of location. New model objectives use an increased
  count to prove planting. `plantedCrops` remains observable but is rejected in new model objectives. Other local crop
  predicates require the current location and unfinished work. Existing historical/user-supplied predicates are not rewritten.
- Dialogue response selection now performs the normal hover before clicking. This fixes questions where a click
  returned completed while no answer was selected. No dialogue outcome is injected directly.
- Interrupted original intentions survive a failed Finish & Save followed by restart/retry. The saved resume intention
  is omitted from the model-facing snapshot and does not accumulate nested save requests.
- 204 offline tests pass. C# release build and deployment pass with the existing CS9057 analyzer-version warning.

## Live evidence

| Check | Result |
| --- | --- |
| OBS recording | 11.93 seconds, 1920×1080 H.264 High at 30 FPS, AAC stereo 48 kHz. Audio mean −23.8 dBFS, peak −4.5 dBFS. Zero skipped encoding/render frames in the final short check. |
| Three outputs | Three local RTMP receivers received 447 frames each, about 14.9 seconds. Shared encoder, synchronized start/stop. Actual platform ingest and internet upload untested. |
| Route/save | `ccfe4c57-b2fb-43bd-8f95-1abeb15b7529`: Farm south entrance at 07:30 → Forest → Town → BusStop → Farm east → FarmHouse → bed; 22 controls; Spring 20 → 21, 06:00, saveCount increased. No model calls; $0. |
| Resume/dialogue | `faf9a20f-1876-44a1-938a-81404fc07e56`: loaded the matching Spring 21 checkpoint, selected No on the bed question in one response call, verified the question closed, then stopped. No farm work, new save, or model calls. |
| Save integrity | Current game file and SaveGameInfo hashes still match checkpoint `6394d027d2db4de480309f852d5668c6` after reload. |

The route check needed setup recovery. Run `3bfa52e8-2d37-41c9-adcf-7d61ae126321` stopped on Marnie's scripted
cat-adoption event. The developer advanced the dialogue, accepted the cat, and kept the suggested name **Dudley**.
These were manual setup actions, not autonomous evidence. The game then reached the south entrance in run
`a5f7be5d-3ca3-43b3-99da-9f5dff3bec03`. Its first save attempt learned blocked house/bus-stop/backwoods paths
and a nonexistent cave entrance, but stopped before replanning after the latter. The final change handles that case.
The successful run also discarded an unavailable greenhouse entrance before using the detour.

The first successful save produced checkpoint `cecb6da9fb474b0d8f1f1863ea19fe1f`. A leftover save-only objective
from the earlier preflight retry was retired, preserving all history; the original checkpoint had no active objective.
The matching game/harness pair was re-checkpointed as `6394d027d2db4de480309f852d5668c6`. The runtime now preserves
the original intention across such retries. Current save: `ControlLab_447858816`, Spring 21. All owned processes stopped.

These checks prove specific mechanisms. They do not prove sustained autonomous play, useful learned preferences,
unseen event handling, or a successful 30-minute session. The new planting predicate has offline arrival-versus-planting
coverage; the live cumulative count stayed at 16 across five locations. No new seed was planted in this preflight.

## Remaining before the rehearsal

1. Connect Twitch, Kick, and YouTube locally in OBS. Keep keys out of chat.
2. Verify stable upload capacity. Direct triple output uses 18.48 Mbps before overhead; 25–30 Mbps is the intended margin.
3. Confirm the model allowance when scheduling the rehearsal. $0.272980 remains from the earlier $0.60 cap; this preparation spent $0.

Follow [the setup and acceptance procedure](streaming.md). Do not start public streaming before the recorded rehearsal passes.
