# Quest, perception and inventory follow-through

## Findings from the stopped recording

The recording `broadcast/local/recordings/2026-09-03 20-43-47.mkv` contains the game, overlay and audio. The intended ten-minute smoke ran approximately 20m37s before Rahul requested a stop. It is not a passed rehearsal gate.

The old bridge already exposed some missed text: Willy's rod dialogue at 15:21:17 UTC, the Bamboo Pole receipt at 15:21:31, Inventory Full at 15:25:03 and 15:30:43, and the Flower Dance announcement at 15:32:18. The problems included missing follow-through and retention, not simply absent perception. A bridge input returning `completed` meant the button was pressed, not that a parsnip entered the backpack.

The two runs used 26 and 161 logical model calls, costing $0.03729578 and $0.18566555. Total: $0.22296133. Replaying each event log through the overlay matched its API-cost sum and session summary, with zero missing cost reports. The footer reports the current harness run; a new run resets its counters.

## Implemented behaviour

| Observation | Change |
| --- | --- |
| Objectives ignore missions | Current ordinary journal quests appear in every decision's dynamic life context. A changed quest revision requires a review. The farmer selects a real quest with a concrete next step, or gives every postponed quest a reason and a currently false revisit condition. Completed quest evidence remains available after claiming a reward. |
| Random daily checklist | Morning starts preserve intentions. No compulsory fresh theme, minimum task count or automatic refill. |
| Morning mail ignored | On the farm/inside the farmhouse, morning decisions prioritize reaching the mailbox and reading every unread letter before chores or departure. Observing the empty mailbox completes that day's check. Inventory trouble and urgent sleep/save remain possible to resolve. |
| Transient text missed | The bridge samples displayed HUD messages, dialogue pages, letter pages, level-up text and newly received tools on game updates, including during model requests. It retains the latest 128 notices until Python observes them; the notebook persists observed text. Full text is retrieved in 4,000-character pages. |
| Rod or festival ignored | Retained announcements, completed conversations and new tools require an explicit act/defer decision. Deferrals resurface when their recorded condition becomes true. An accepted next step gets a chance to happen before another old discovery replaces it; a new HUD announcement interrupts it. |
| Full backpack, repeated parsnip pickup | Capacity, free slots and stack limits enter context. An unchanged inventory plus Inventory Full makes the pickup result blocked. Repeat collection is stopped; storage, selling, menus and conversations remain possible. Visible letter/chest pickup targets report whether the item fits. |
| Menu tabs unavailable to the agent | Normal-control tools open inventory, skills, social, map, crafting, collections and options. Visible menu targets and text expose inventory/chest slots, health/energy, skill levels, known social entries, map labels, recipes and letter attachments. Cursor-held chest items are reported. |
| Long unobservant travel | Navigation yields after 300 movement ticks, new encounters and location changes. Nested local skills also return after an eight-second observation boundary. Nearby NPCs and interactive tiles, including building doors, become decisions. |
| Right panel has spare space | Seven recent turns retained at the existing 20px speech font. Older successful-action footers are hidden to fit more conversation. Whole oldest cards are hidden if unusually long text exceeds the panel. |

The system prompt contains standing rules, not quest titles or progress. Quest data, notices, menu state and retrieved text are dynamic. Actor context cap: 6,000 estimated tokens; director: 10,000, using the existing 3.5 characters/token estimate. Reconstructed live inventory context with all four quests, menu targets and a near-full retrieval page used 5,897 actor / 8,904 director tokens. This budget excludes fixed instructions, tool schemas and images; it is not the model's total context window.

These changes expose information and require decisions; they do not guarantee interesting dialogue or successful fishing. Custom menus, minigames and progression systems outside the ordinary quest journal still need visual interpretation or further integration. Retention begins when the updated bridge is loaded; it cannot recover text that disappeared before capture.

## GLM configuration and caching

Default model: `z-ai/glm-5.3-flash`. Ordered allowlist: `deepinfra/fp8`, `nextbit/fp8`, `baseten/fp8`. Fallback enabled within that list; parameter support required. No price sorting. The public OpenRouter endpoint catalogue confirmed all three routes and cache-read pricing on 2026-09-03.

Actor and director use **low** reasoning. Z.AI documents **low, high and max**; an unsupported value can select max upstream, so the harness rejects medium before sending a request. [Official model card](https://huggingface.co/zai-org/GLM-5.3-Flash#note).

DeepInfra documents automatic prefix caching and an optional `prompt_cache_key`. Requests keep instructions/tool serialization stable, put slowly changing memory before current observations, and use a key derived from model/instructions/tools rather than time, quest progress or location. Actor/director sessions have stable IDs. GLM receives no OpenAI-specific cache breakpoints. Automatic caching remains available even if a gateway does not forward the optional key. [DeepInfra caching](https://docs.deepinfra.com/chat/prompt-caching).

An explicit provider order overrides OpenRouter's automatic sticky provider routing. DeepInfra is therefore attempted first each time; failover can encounter a cold cache. This preserves Rahul's requested routing preference. [OpenRouter caching and routing](https://openrouter.ai/docs/guides/best-practices/prompt-caching).

Use reported `usage.prompt_tokens_details.cached_tokens` for cache evidence. The footer sums `usage.cost` for actor, director, discarded decisions and failed response retries once. It does not multiply all tokens by DeepInfra's price or discount cached input twice. It retains full numeric precision internally and rounds only the displayed USD amount to six decimals. [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

No paid GLM request was made for this change. Payload tests verify configuration and cache-prefix stability; real cache hits, latency, provider failover and behavioural acceptance still require the next authorized run.

## Runtime and next validation

Validation: 251 offline tests pass. The C# bridge builds with zero errors and the existing compiler/analyzer version warning. Headless browser checks show seven complete turns at 1080p and 720p. No paid gameplay was run after this revision.

The updated bridge builds without deployment. The running game remains on the previous mod, held at Spring 24, 11:30 on the Farm with unsaved progress. Do not restore the stale Spring 23 harness checkpoint. Preserve or deliberately settle the current day before restarting to load the new mod.

For the next authorized recording:

1. Protect the current game state before restarting the game.
2. Deploy the updated bridge while the game is closed.
3. Start OBS recording with the audience overlay visible.
4. Run GLM with `--no-launch-game`, an explicit budget and `--max-minutes 10`.
5. Verify mail, quest selection, inventory recovery and one discovery decision in the logs and recording.
6. Compare provider attribution, cached tokens and API cost with the footer.
7. Stop recording after the harness stops.

The 30-minute rehearsal remains pending until this focused validation passes.
