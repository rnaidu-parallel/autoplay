# Broadcast preflight — 2026-09-03

Result: local composition, recording audio, and three concurrent RTMP outputs verified. This was a short title-screen
test, not the 30-minute autonomous rehearsal. No public endpoints, keys, or model calls were used.

| Component | Evidence |
| --- | --- |
| OBS | Portable 32.2.2; official archive verified against the GitHub asset SHA-256 digest. |
| Multiple outputs | sorayuki release 0.7.4.3; Windows asset/internal version 0.7.4.0; loaded successfully. |
| GPU encoder | GTX 1660 Ti; NVENC H.264, CBR 6000 kbps, keyframe 60 frames, High profile, preset p5. |
| Composition | Game Capture plus transparent 1920×1080 audience overlay; process-specific audio. Cursor capture off. No desktop or microphone source. |
| Recording | `local/recordings/2026-09-03 15-00-55.mkv`: 11.93 seconds, H.264 High 1920×1080 30 FPS, AAC stereo 48 kHz. |
| Audio | Decoded mean −23.8 dBFS, peak −4.5 dBFS. Earlier title-screen samples silent; fresh unmuted launch verified audible game music. |
| Performance | Zero skipped encoding frames; zero skipped render frames in the final recording's OBS session. Short duration only. |
| Simultaneous delivery | Three localhost receivers on ports 19351–19353; each produced 447 frames, about 14.9 seconds, 11,188,272 bytes. Shared OBS video/audio encoders. |
| Local transport | Main output active 14,466 ms at status capture; zero skipped output frames, zero congestion, no reconnect. All three stopped together. |
| Overlay regression | Last two applied actor lines survive over eight director events; rejected lines excluded; new run clears speech. Full suite: 198 tests pass. |

Artifacts remain under ignored `broadcast/local/`: `composition.png`, recordings, receiver logs, and OBS logs.
The overlay replayed run `7a81817b-f9eb-46a3-ad39-47a0e48982c9` without paid calls. Its Spring 18 state was historical;
that check left the real save at Spring 20, checkpoint `aec76ef4c7174c46ac82d627a5d52f56`.
The later [gameplay check](../docs/readiness-2026-09-03.md) saved Spring 21.

OBS's first-run wizard initially blocked recording despite a successful API acknowledgement. The seeded profile now
marks that step complete. Recording checks verify that output actually becomes active.
The title music toggle used during diagnosis was restored to its original unmuted setting.

After testing, OBS and the game were closed, overlay/receiver helpers stopped, and the original Twitch/Kick/YouTube
configuration restored. Real stream keys remain empty. The test did not measure upload bandwidth, platform ingest,
long-run stability, or autonomous gameplay. See [the remaining gate](../docs/streaming.md).

Sources: [OBS release](https://github.com/obsproject/obs-studio/releases/tag/32.2.2),
[plugin release](https://github.com/sorayuki/obs-multi-rtmp/releases/tag/0.7.4.3),
[plugin author documentation](https://obsproject.com/forum/resources/multiple-rtmp-outputs-plugin.964/).
