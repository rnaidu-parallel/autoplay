# Qwen 3.8 Flash and gameplay evaluation — 2026-09-02

GLM remains the default. Qwen is available as an optional low-reasoning Alibaba profile, but this trial was unreliable under the current provider and 600-token output limit. Outcome scoring uses deterministic game-state evidence; no extra LLM grader is involved.

## Controlled live trials

Both runs used the same day-10 save, five-Parsnip objective, 12-game-action cap, six-call configured cap, director interval four, diagnostic frames, and fresh isolated objective ledgers. No overnight save occurred between runs. Game launch and loading used no model calls. These are single acceptance trials, not a statistical model ranking.

| Measure | Qwen 3.8 Flash low | GLM 5.3 Flash low |
| --- | ---: | ---: |
| Verified crops | 4 | 5 |
| Initial objective reached | No | Yes |
| Autonomous wall time after loading | 259.393 s | 20.215 s |
| Successfully parsed actor decisions | 4 | 2 |
| Logical actor calls including failures | 9 | 2 |
| Actor latency p50 (parsed decisions, including their retries) | 18.820 s | 6.602 s |
| HTTP 429 responses | 29 | 0 |
| Input cache fraction | 72.6% | 0% |
| Reported cost including director/failed-response usage | $0.009007 | $0.002178 |
| Final seeds / stamina / health | 16 / 135 / 100 | 15 / 135 / 100 |

Qwen run `5c819efe-d98c-4e1c-9a13-ddca689e1277`; GLM run `b09c3d61-574f-4d89-ba4e-84628c45cef6`.

Qwen's nine logical calls exceeded the configured six because the old counter counted only successfully parsed decisions. This observed bug was fixed after that trial: failed logical actor calls now consume the cap, and the main loop stops at it. A regression test verifies stopping when every actor call fails. The GLM trial used the corrected cap. Physical retries remain separately bounded; director calls are separate.

## What Qwen's result means

- Availability: 29 HTTP 429 responses; error metadata identified Alibaba's upstream shared pool. No fallback route was used. This is provider availability, not an incorrect game decision.
- Output reliability: 42 physical attempts total; 29 HTTP failures, four accepted responses, nine rejected responses. Rejections included six `length` responses with no usable tool and three malformed argument responses at the 600-token cap. Five truncated responses reported all 600 tokens as reasoning; the other reported 593. Explicit low reasoning did not guarantee enough output budget for a tool call. No token cap or validation constraint was relaxed.
- Game execution: first planting batch verified `(71,27)`, then stopped when `(72,23)` produced no proven crop/seed change. A later retry of `(72,23)` succeeded. Last batch verified `(68,25)` and `(72,18)` before the action cap. Root cause of the ineffective first input at `(72,23)` is not established; do not attribute it solely to model intelligence. The local verifier caught it instead of reporting false success.
- Goal: Farm, four planted crops, seeds20→16. Five-crop condition remained false; no objective completion credit.
- GLM: one travel decision and one five-tile planting decision. Local planting controls took3.183 s; seeds20→15, crops5, stamina135/health100 unchanged. The harness recorded objective completion immediately after the final allowed action. Two concurrent director reviews were discarded; no actor pause awaited them.

High cache hit rate did not yield better gameplay. The two-call GLM actor sample is too short and cold to assess steady-state caching. Sparse tests also do not establish performance on watering, sleeping, shops, recovery, or longer runs.

## Evaluation changes

- `--isolated-state` uses a fresh ledger under each run directory. It leaves the shared ledger intact and does not reset the saved game. This removes inherited objective-history bias from new comparisons.
- Session-start records include initial objective and isolation mode.
- `report` now includes initial condition at start/end, verified local planting count, per-location crop changes, final structured state, and logical actor call count including failures.
- Final tool-result state counts even when no next observation occurs. Entering a farm with existing crops earns no planting credit in the per-location delta.
- All52 Python tests pass, including Qwen provider/low reasoning, ledger isolation, final-state evaluation, and call-cap failure handling. No C# source changed this turn. Both launched game processes exited; no FFmpeg remains.

See [evaluation contract](../../docs/evaluation.md) for the checks that exist and the missing acceptance cases. Broader game competence still requires repeated fixed-save scenarios, resource/safety outcomes, full day-cycle persistence, recovery tests, and reliable video review. The current system verifies specific actions and goals; it does not provide a comprehensive strategy or entertainment score.

Evidence: [metrics](metrics.json), full run events and sparse frames in the run directories above. Routing/capability sources: [OpenRouter Qwen model](https://openrouter.ai/qwen/qwen3.8-flash), [Qwen low/medium reasoning and multimodal support](https://docs.qwencloud.com/developer-guides/getting-started/latest-model), [Qwen thinking/tool-choice restrictions](https://docs.qwencloud.com/developer-guides/tool-calling/function-calling).
