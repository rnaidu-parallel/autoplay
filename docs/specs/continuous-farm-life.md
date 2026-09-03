# Continuous farm life: attention, quests and self-directed play

Status: implementation and acceptance specification, 2026-09-03. Based on Rahul's review of the recorded smoke and code at `48d79d8`. The first live smoke exposed missing follow-through; the revised implementation awaits a game restart and focused live validation. See [follow-through changes and model configuration](../life-followthrough-2026-09-03.md).

## Desired experience

The farmer has an ongoing life: people it knows, promises it has made, things it wants to understand, practical needs and projects that take several days. Waking up updates that context. It does not manufacture a new daily storyline. Travel creates opportunities to notice and choose. Sleep follows time, energy and circumstances; finishing a task list is not a reason to end the day.

Example, conditional on discoveries in this save: the farmer reads Robin's request, remembers it while exploring the forest, notices a villager, chooses to talk, finds something unfamiliar, then returns to its search or records why it will try again later. Tomorrow retains those intentions. This example describes possible behaviour, not a script to force.

## What the current code explains

| Feedback | Current mechanism | Planned change |
| --- | --- | --- |
| Only two turns visible | `overlay.py` truncates actor/operator history to two; `overlay/index.html` independently renders two at 24px with large gaps | Seven retained turns at 20px; compact history with whole-card overflow handling |
| Long walks without thinking | `world.py:travel_to` chains multiple locations; C# `StartNavigation` expands requested ticks by path length, up to 3,600 ticks | Short movement segments and real returns to the actor at encounters and arrivals |
| Arbitrary daily objectives | `_agenda_errors` requires 5–8 morning items; `variety_errors` requires a different theme and category mix; refill requires another 2–4 items | Persistent intentions, with no minimum task count, compulsory new theme or daily variety quota |
| Enters a place and immediately leaves | Exploration validation explicitly requires `location is <Name>`; arrival can satisfy the objective without examining the place | Arrival triggers an observation/decision; meaningful discovery distinguished from simply passing through |
| Ignores NPCs and objects | Nearby NPC data exists, but local travel only stops for menus/events/damage; object metadata mainly describes obstacles/actions | Notice relevant nearby changes during travel; actor chooses interact, observe, defer or continue |
| Ignores quests, journal icon and mail | Bridge reports quest/mail counts; no structured quest content/progress or opened-letter content | Reliable discovery, reading, memory and follow-through through ordinary game UI |

The identity prompt already encourages curiosity and changing one's mind. More exhortations alone cannot compensate for control loops that skip decisions or require artificial agenda items. Existing notebook carry-over also exists; preserve it while removing the daily list's authority over the farmer.

## 1. Make journal, mail and encounters visible

Extend the existing C# observations with the information a player can inspect: active quest IDs, titles, text, requirements, visible progress, deadline, completion and claimable reward when available. Read mail availability separately from the text and attachments of the currently opened letter. Capture visible journal/letter/menu controls and HUD notices needed to interact reliably. Determine the exact fields from the installed game/SMAPI API during implementation.

The exclamation icon by the clock opens the quest journal. Its presence alone is not proof of a newly arrived quest. Track a changed journal or pending reward separately from an unchanged icon. On the first resumed session, inspect the existing journal; afterward, revisit when its content changes, a relevant task advances, a reward becomes claimable, or the farmer needs its details.

Unread mail gets attention at the next reasonable opportunity near the farmhouse. Open the mailbox, read each letter, collect visible attachments if appropriate, and verify the resulting quest/inventory/mail state. If interrupted or inventory is full, retain the unfinished letter/reward task. Do not mark mail read, accept quests or award rewards by writing game state directly.

Add compact NPC context: stable identity, met before, spoken to today, relevant known request, and player-visible relationship state where available. Use existing nearby actions and a fresh screenshot for signs, boards, entrances, shop counters and unusual objects. Inspect one target at a time. Do not flood every request with the whole journal, every villager or the entire map. Keep the current context caps; retrieve details as needed. Observations must not disclose hidden schedules, undiscovered quests or spoiler outcomes.

Files: `src/Autoplay.GameBridge/BridgeProtocol.cs`, `ModEntry.cs`, Python observation/context and tool schemas. This requires a bridge rebuild and controlled game restart before live validation.

## 2. Return decisions to the farmer during travel

Add an explicit successful partial result for movement that yielded for attention. Preserve the intended destination and observed position, then recompute the next segment if the actor chooses to continue. A planned yield is neither a timeout nor a failed attempt; it must not consume the existing failure/deferral allowance.

Return to the actor with a fresh observation at:

- A location transition, once loading/control availability settles. Take a fresh visual look when the place is unfamiliar, relevant state changed, or an interaction is present.
- A newly noticed nearby villager, a known quest target, or a meaningful visible interaction/notification.
- The end of a short movement segment, even if no event occurred.
- An operator command, checked between ordinary controls throughout travel and farming skills.

Initial measurement targets: control checks after no more than about five seconds of local movement; an actor decision opportunity within ten seconds of ordinary uninterrupted travel. These are segment targets, not a guarantee about model/network latency, loading or uninterruptible game animation. The C# path-length expansion must respect segmentation; changing the Python tick argument alone is insufficient.

One encounter should not repeatedly interrupt every step. Remember that the farmer already considered this NPC/object under the same conditions. Reconsider after a meaningful change: new dialogue/day, new request, changed availability, or an explicit planned return. Ordinary grass, debris and familiar scenery should not cause constant model calls. The actor remains free to stop for them deliberately.

Keep local automation for repetitive chores. Check attention and operator input at safe boundaries inside those skills. Finish & Save and urgent return remain higher priority than optional curiosity; do not interrupt an actual save transition. Preserve the existing game/OBS ownership and attach recovery rules.

Files: `world.py`, `farming.py`, `runner.py`, bridge navigation. Test nested return-home travel as well as direct navigation so no long uninterruptible route remains hidden inside a skill.

## 3. Replace compulsory daily agendas with persistent intentions

Use the existing notebook and objective ledger; avoid a competing second planner. Keep practical projects, discovered quests and personal interests across days with stable identity. Record their source, why they matter, next useful step, observed progress, and any deadline or revisit condition. Preserve previous history and checkpoint compatibility with a small, tested migration of unfinished agenda entries.

Morning review becomes: what changed overnight, what still matters, what needs attention now? Mail, weather, crop readiness and deadlines can change priorities. An unchanged intention can continue for several days. Empty task lists do not trigger invented replacement chores.

Remove the 5–8/2–4 item requirements, mandatory new daily theme, compulsory category diversity and unvisited-location quota. These are deliberate policy changes requested by Rahul; retain validation of evidence, feasible actions, real deadlines, retry limits, safe return and verified completion. Retire conflicting prompt/schema/test requirements together.

The director reviews plans when circumstances warrant it: new quest or discovery, important resource change, repeated failure, completed intention, day transition or an actor request. It offers priorities without forcing a full-day timetable. The actor can pursue a detour or change its mind without a director approval call, while preserving the prior intention and a brief reason when the change matters.

Use intention types honestly. A delivery has an actual quest completion condition. A purchase has item/money evidence. Visiting someone has conversation evidence. Exploration can produce an observed discovery or an honest conclusion such as “closed today”; arriving at a map coordinate is only arrival. Do not invent measurable busywork just to give curiosity a numeric success predicate. Record optional exploration as considered/attempted/deferred when there is no completed external task.

Normal bedtime remains situational within existing time/health/energy and return constraints. Completing today's work does not command sleep, and staying awake must not require filling an arbitrary activity quota. Operator Finish & Save retains its explicit early-sleep override.

Files: `notebook.py`, `objectives.py`, `runner.py`, `prompts.py`, tool schemas and existing planner tests.

## 4. Make interactions produce an ongoing story

At an encounter, the farmer decides whether to talk, inspect, use, accept a request, postpone or continue. It need not interact with every object or buy something in every shop. Entering a new shop should at least give the model a chance to inspect its people, goods and purpose before choosing what happens next. Leaving without a purchase is valid; skipping the decision entirely is not.

Quests become a first-class source of intentions. Discover requests in mail, the journal, boards or actual encounters; compare requirements and deadlines with supplies, abilities and interests; accept feasible requests, carry out the work, return or deliver as required, verify completion and claim rewards. Start with journal reading, mail, introductions and reachable deliveries/gathering supported by existing controls. Fishing/mining/combat quests can be investigated or deferred until their controls are validated.

Retain meaningful experiences in the notebook: person/object/place, observed outcome, personal reaction and a concrete reason/condition to revisit. “Look into this when I have enough money” should resurface when that constraint changes. Repeated empty “maybe later” remarks should not accumulate as duplicate commitments. Separate “I would like to” from “I accepted/promised to.” New memories require observed evidence.

Narration explains a meaningful observation or choice in the farmer's voice. It may say what caught its attention, why it is changing course, or what it wants to remember. Avoid forcing a speech for every ordinary tile or identical continuation. Curiosity is permission to explore; it is not random action selection or a quota of NPC conversations. Known closed doors and unchanged blocked paths remain evidence to avoid repeating the same mistake.

## 5. Show more context on stream

Retain six recent farmer/operator turns in the feed and show roughly 4–6 complete compact cards. Start around 20px narration with tighter line spacing and 12–16px card gaps; keep the latest turn strongest and older turns readable. Long entries reduce the visible count rather than shrinking indefinitely. Keep full text in operator history. Consider more than six only if real 720p viewing remains comfortable.

Change the top band's label to “On my mind.” Show a concise current intention or immediate situation, such as “Looking for Robin's axe” or “Seeing what's inside this shop,” when supported by the current play. Keep detailed completion predicates and planner internals in the operator view. Do not replace clear goals with vague personality slogans, and do not present a hoped-for result as already achieved.

Update both backend retention and browser rendering. Preserve current objective/game/feed/footer bounds, latest-run switching, cost counters and clear waiting/save states. Check long narration, six mixed outcomes and 1080p→720p readability with existing replay fixtures before recording.

## Implementation order and validation

1. Build the compact six-turn overlay using archived telemetry. This provides an immediate visible improvement without paid gameplay.
2. Add journal/mail/encounter observations and ordinary UI verification. Validate changed versus unchanged journal, multi-letter reading and reward collection with offline fixtures and a controlled game check.
3. Implement movement segmentation and attention yields at both Python and C# levels. Test NPC detection mid-path, arrival at a new shop, safe continuation, nested skills, operator steer/hold and return/save. Record actual maximum command and decision gaps.
4. Revise persistent planning and interaction memory using those observations. Test continuity across two mornings, discovered-quest priority, believable deferral/revisit, no forced agenda refill, and migration/attach/checkpoint compatibility.
5. Run a focused recorded smoke, then the 30-minute rehearsal if the behaviours below pass. Choose an allowance from the remaining approved budget; measure added calls/cost rather than assuming the prior $0.095079 final-run cost still applies.

Acceptance is behavioural and evidence-based:

| Scenario | Pass evidence |
| --- | --- |
| Initial journal icon and unread mail | Farmer opens and reads them, learns actual contents, handles or deliberately defers pending actions; unchanged icon does not cause a loop |
| Villager appears during a walk | Local movement yields; next actor sees the encounter and chooses what to do; no repeated identical interruption |
| New area/shop | Model observes the settled scene before the next departure; interaction or reasoned continuation is possible |
| Discovered feasible request | Intention references the real quest; observed progress survives night/reattach; delivery/completion/reward verified when possible |
| Deferred curiosity | A specific remembered interest resurfaces when its stated condition changes |
| Two consecutive mornings | Unfinished intentions persist; new evidence can reprioritize; no compulsory new theme or list of filler tasks |
| Exploration/blocked route | Arrival alone does not prove an interaction; unchanged failed paths are not retried under renamed objectives |
| Hold/steer during a local skill | Acknowledged at the measured next safe boundary; pending stale action discarded; save priority preserved |
| Stream review | Readable seven-turn history when text fits, honest current intention, coherent detours and conversations, no capture/context regression, verified final save |

Use deterministic fixtures to guarantee rare conditions such as an NPC entering mid-path, unavailable mail rewards and overnight quest changes. The live run must remain autonomous; do not script a tour or fabricate interactions to satisfy the checks. If the current save lacks a suitable request, report that live scenario as untested and use an appropriate later session. Logs should explain attention yields and deferrals, show task/quest evidence, measure command latency, and keep counts/costs observable without turning activity counts into behavioural quotas.

## Boundaries and decision changes

This plan supersedes compulsory daily agenda/theme/variety rules and the two-turn audience-feed limit. It preserves ordinary game controls, truthful progress, finite context budgets, identity/memory, the explicitly selected provider policy, cost limits, verified saves, and Windowed attach recovery. It does not add another model role, unrestricted game-state writes, broad progression systems or new combat/fishing/mining implementations. Full API details and final typography are implementation choices to validate locally.

The deliverable is one integrated change to attention, memory, planning and presentation. The 30-minute/public-streaming gate remains pending until the resulting play is reviewed.

References: [recorded smoke](../smoke-2026-09-03.md), [operator controls](../operator-controls.md), [existing memory work](longrun-memory.md), [Stardew quest journal reference](https://stardewvalleywiki.com/Quests).
