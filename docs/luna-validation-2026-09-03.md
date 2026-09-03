# Final quick Luna validation — 2026-09-03

**Result: partial improvement; gameplay acceptance has not passed.** Recording: `broadcast/local/recordings/2026-09-03 22-35-28.mp4` (original MKV retained), **5m11.29s**, 1920×1080 H.264 30 FPS, stereo AAC 48 kHz. OBS composition includes game, narration and cost footer. A 12-second audio sample measured −20.8 dB mean / −5.0 dB peak. OBS reported one skipped output frame over 9,351 frames.

Run `78362627-35df-476e-ab8c-33e1466d14e4`; `openai/gpt-5.6-luna`, OpenAI only, actor low / director medium. Recording 17:05:29–17:10:40 UTC. Seven-minute ceiling; stopped after five minutes when repeated dialogue provided a clear quality finding. No manual gameplay rescue or operator steering; only the end hold.

## Measurements

- 53 logical model calls / 53 physical requests, all accepted tool responses; zero provider errors or output truncations. Median completed decision 4.307 s, maximum 6.900 s. Revised GLM-low median was 13.900 s in a different gameplay segment, not a controlled identical-state benchmark.
- API cost **$0.09181590**; footer **$0.091816**. Decimal event sum, telemetry summary and overlay agree to the displayed precision. 754,020 prompt tokens, 396,265 cached (**52.55%**); 7,120 completion tokens. No hardcoded model pricing used.
- 51 tool results: 16 completed, 12 yielded, 9 recorded, 6 rejected, 4 blocked, 4 visual-review requests. An accepted model response is not evidence of successful gameplay.
- Initial Spring 25, 6:00, FarmHouse; final 11:30, Beach (38,0). Health100, energy270, money5 unchanged. Introductions advanced **6/28 → 7/28**. No item disposal, harvest, catch or new save.

## Feedback coverage

| Feedback | Actual observation | Assessment |
| --- | --- | --- |
| More narration visible | Final composition shows seven complete messages at the existing font, plus cost/cache footer. | Observed. |
| Zombie traversal | Movement and location changes yielded for decisions; farmer changed tactics after blocked BusStop and Penny approaches. | Improved, but repeated travel narration remains monotonous. |
| Quests and sensible objectives | Four live quests available; six quest reviews; Penny greeting advanced Introductions. | Partial. Fishing was incorrectly labeled an Introductions objective; repeated quest reviews interrupted travel. |
| Journal/exclamation | Quest changes entered dynamic context. | Incomplete. No `check_journal` call; `review_quests` was accepted during dialogue and marked the new revision read without opening the journal. |
| Morning mail | Observed zero unread letters on Farm before departure; satisfied the empty-mailbox rule. | Empty-mail case observed; opening/accepting new mail untested. |
| NPCs, curiosity, buildings | Farmer noticed Penny, approached, greeted and reacted to her library dialogue. | NPC behavior observed. Building/shop exploration and question-mark interaction untested. |
| Other menu tabs | Autonomous `open_menu_tab` opened inventory; selected rod, noticed blocked close, returned rod to its slot, then closed. | Inventory recovery observed. Autonomous skills/social/map/crafting use not demonstrated. Prior manual probe opened skills/social/map; one crafting click mismatched. |
| Fishing rod follow-through | Acknowledged rod, inspected it, chose first fishing attempt and reached Beach. | Partial. No cast or fishing minigame occurred. |
| Full inventory / failed parsnip pickup | Capacity/free slots supplied; initial and final bag full. | Storage/selling/harvest error recovery not exercised. A cursor-held rod temporarily freed a slot, then was returned. |
| Dialogue, mail, transient announcements | Penny's displayed pages persisted in the notebook, including the library comment and final apology. | Capture observed. Action policy flawed: retained completed dialogue caused repeated attempts to advance/reopen it for about a minute. Farmer eventually left without operator coaching. New mail/festival announcements untested. |

All original feedback is represented in implementation or an explicit remaining gap; this recording does **not** establish that every behavior works.

## Concrete remaining work

1. Distinguish a completed conversation from the currently open dialogue. Re-reading retained text must not manufacture another advance-dialogue task or reopen the same completed exchange.
2. Keep a personal pursuit separate from a quest it does not advance. Quest review must not relabel a fishing plan as Introductions; changed-journal reads need an executor-level precondition.
3. Verify autonomous useful tab use and full-bag storage/selling, then a first cast. Do not count a manual tab probe or merely noticing the rod as acceptance.

Before this run: added rejection feedback, explicit menu entry indexes, tab/close result checks, attach close fallback and concrete tab-use guidance. **253 tests passed.** No additional changes were injected during gameplay.

## Current runtime and streaming preparation

The harness and its overlay process are stopped; OBS recording and public streaming are off. Game PID12900 remains held in the native inventory menu at Spring25, 11:30, Beach to preserve unsaved Penny progress. Latest verified checkpoint remains `0460c6ece3cb4c34bd8832846e2141d1` (morning). **Attach to retain the live afternoon state; do not restore the morning checkpoint into this running game.**

Read-only local OBS checks: Twitch key configured; Kick key/server configured with synchronized start/stop; YouTube key/server empty and synchronized start disabled. Canvas/output1920×1080,30 FPS. These checks prove saved configuration, not platform ingest. Upload capacity and platform video/audio acceptance remain unverified. Public streaming has not started.

The previous plan called for one reviewed30-minute private rehearsal after preparation. This short diagnostic did not pass that gate; address the observed gameplay defects before scheduling it. Twitch/Kick can be prepared first; YouTube still requires local account setup.

Evidence: ignored `broadcast/local/luna-final-20260903/` metadata, audit, screenshots and media verification; raw run events under `harness/runs/78362627-35df-476e-ab8c-33e1466d14e4/`. GLM findings: [GLM validation](glm-validation-2026-09-03.md).
