# GLM validation and Luna restoration — 2026-09-03

Rahul stopped the GLM comparison and requested restoration of the previous Luna setup. Default: `openai/gpt-5.6-luna`, OpenAI only, actor low / director medium, state-first decisions and existing explicit cache boundaries. Quest/mail/notices/inventory/overlay improvements remain.

## Findings

| Segment | Run | Logical calls | Median completed decision | API-reported cost |
| --- | --- | ---: | ---: | ---: |
| GLM low, initial | `79964fc8-45bc-48b6-8bbd-3f77bb1ca209` | 7 | 16.495 s | $0.013112495 |
| GLM low, retry feedback fixed | `b3edded2-4798-4f85-98b4-ed5f7b63edc8` | 9 | 13.900 s | $0.011318030 |
| GLM high, interrupted before action | `90d5833d-5c82-4f10-bca3-2d6340a1134d` | 1 | 19.625 s | $0.001850550 |
| GLM high, fresh saved morning | `dfd9d744-0479-49e7-ac79-b0c9fc829b03` | 3 | 21.564 s | $0.004523225 |

Total reported GLM cost: **$0.030804300**. Logical calls include model errors; decision latency statistics include completed decisions only. Usage aggregates physical retries once, including discarded decisions. No public stream started.

- Initial low-effort attempts repeatedly violated text limits and structured quest-deferral conditions. Retries previously sent the same request without explaining rejection. Added exact validation feedback and condition examples, preserving the cached system/tool prefix and strict validation. The next segment accepted nine logical decisions with one physical retry, but gameplay remained slow and ineffective around inventory.
- Menu targets were supplied without explicit array indexes. GLM repeatedly claimed to close inventory while selecting an item slot. Targets now carry `index`; instructions distinguish menu indexes from inventory slots. Tab and close clicks check their resulting page and report mismatches rather than input execution as success.
- A live manual control probe opened skills, social and map pages with readable context. One map-to-crafting click reached SocialPage instead; an Escape-based attach also failed. The attach path now tries the observed close control if Escape leaves GameMenu open. The deeper intermittent input/layout issue remains unproven; this probe does not establish autonomous tab use.
- High exhausted the 600-token actor output cap before returning an action; two attempts in the fresh run ended with `finish_reason=length`. The trial also encountered DeepInfra and Baseten rate limits and a NextBit invalid-request response. A proposed larger output allowance was withdrawn when Rahul cancelled. **No completed five-minute high comparison or adequately sized high-output trial occurred.** These results do not isolate intrinsic model quality from harness and provider issues.
- Actual caching worked on GLM low: 58,368 / 171,887 prompt tokens cached in the first segment (34.0%); 38,912 / 160,548 in the second (24.2%). High samples reported no cached tokens. Provider choice varied within the allowlist. Full fallback reliability was not established.

GLM supports low, high and max; medium is unsupported. [Official model card](https://huggingface.co/zai-org/GLM-5.3-Flash#note).

## Save and recordings

The old game was saved locally with zero model calls before deploying the updated bridge. Verified checkpoint: `0460c6ece3cb4c34bd8832846e2141d1`, save `ControlLab_447858816`, Spring 25 Year 1. All five game/harness hashes matched. Restarted GLM high from that saved morning; the user subsequently stopped it. The later Luna validation uses the same saved morning and matching memory.

Recordings are local under `broadcast/local/recordings/`:

- Initial low: `2026-09-03 22-18-07.mkv`, about 3m03s.
- Revised low: `2026-09-03 22-23-14.mkv`, about 2m35s.
- Interrupted high: `2026-09-03 22-27-33.mkv`, about 18s recorded.
- Fresh high: `2026-09-03 22-29-38.mkv`, about 1m43s.

Short setup-only recordings at 22-17-47, 22-26-10 and 22-29-16 are not gameplay validation. Ignored metadata, logs, control probe and composition screenshots: `broadcast/local/glm-20260903/`.

## Next validation

The user authorized a final 5–10-minute Luna recording, then streaming preparation. Results belong in `docs/luna-validation-2026-09-03.md`. A short test cannot establish all mail/event/fishing/quest outcomes unless those situations actually occur. The previously agreed 30-minute rehearsal and platform preparation remain open.
