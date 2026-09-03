# Deterministic control surface

This checklist tracks controls verified through the C# SMAPI bridge. Build success alone does not count as runtime verification.

| Capability | Status | Evidence or next test |
| --- | --- | --- |
| Bridge load | Verified | SMAPI 4.5.2 loaded `Autoplay Game Bridge 0.1.0` without errors. |
| Console command registration | Verified | `help autoplay` returned the bridge command syntax. |
| Title-screen state | Verified | `autoplay state` reported `world_ready=false menu=TitleMenu`. |
| Game-window focus | Verified | The bridge observed `game_active=false`, attached to the foreground window thread, restored its own window, and then moved from pixel `576` to `544` with `game_active=true`. The harness also recovered when Chrome or ChatGPT owned the foreground lock by locating and temporarily raising the Stardew client window. |
| True full-screen capture and controls | Verified | `bridge-test --fullscreen` reported `graphicsFullScreen=true`, `windowedBorderless=false`, and a `1920x1080` viewport; the game-only screenshot was exactly `1920x1080`. Cursor movement, left click, drag, scroll, bounded wait, and keyboard press completed before the original `windowMode=1` setting was restored. |
| One-tick button press | Verified | `autoplay press D` changed facing direction on open floor. |
| Bounded button hold | Verified | `autoplay hold A 30` moved the player from tile `(9,9)` to `(6,9)`. |
| Control cancellation | Verified | A 300-tick hold stopped with all 300 ticks remaining and no movement. |
| Overlapping commands | Verified | While a 60-tick `D` hold was pending, an immediate `W` press was rejected with `control_busy`; it did not replace the active control. |
| Post-input timing | Verified | Input release reports `reason=control_released` instead of claiming completion. At the title screen, `wait menu=TitleMenu 120` completed immediately, `wait menu=none 5` timed out with final state, a control submitted during a wait was rejected as busy, and `stop` cancelled the wait. Re-test `wait menu=none 120` against the observed delayed `DialogueBox` close when that interaction is next exercised. |
| Movement and collision | Verified | Four directions and simultaneous diagonal movement worked. Bed collision blocked vertical movement while still changing facing. Walking through the farmhouse exit changed location from `FarmHouse` to `Farm`. |
| Run/walk modifier | Verified | At default auto-run, `D` moved about 153 pixels in 30 ticks; `D+LeftShift` moved about 56 pixels. `LeftShift` therefore selects walking in this configuration. |
| Facing without movement | Pending | Determine whether a dedicated deterministic operation is required. |
| Action and interaction | Partial | `X` opened and advanced mailbox dialogue. Facing the exterior farmhouse door and pressing `X` entered the house. NPC interactions and non-binary confirmations remain untested. |
| Tool use | Partial | One-tick `C` used the axe, reduced stamina from 270 to 268, set `using_tool=true`, and later released cleanly. A 30-tick hold did not repeatedly spend stamina. Charged tools, explicit targeting, and release timing remain untested. |
| Inventory | Partial | `D2` selected the hoe and `D1` restored the axe. Item movement and toolbar scrolling remain untested. |
| Menus | Partial | `E` opened `GameMenu:0:InventoryPage`; `F` toggled `QuestLog`; `M` opened `GameMenu:3:MapPage`; `Escape` closed menus. Configured cancel key `V` did not close `QuestLog`. Lists and text entry remain untested. |
| Cursor targeting | Partial | `cursor 100 100` reported exact screen coordinates. The bridge measured map-tab bounds, moved to their center, and `MouseLeft` changed `InventoryPage` to `MapPage`. It also clicked `Load` and the `BridgeTest` save from the title screen. World targeting and zoom changes remain untested. |
| System cursor visibility | Verified on title and Load menu | Bridge control hides the white system cursor and retains the game's pointer. Native cursor-image checks and a normal Load click passed; original game settings restore on stop. |
| Dialogue | Partial | `DialogueBox` made `player_free=false`; `X` advanced mailbox text. Question responses are now exposed by text and index, and `choose_dialogue_response` invokes the exact game component. NPC dialogue and non-binary choices remain untested. |
| Cutscenes | Pending | Test detection, advancement, and loss of player control. |

## Continuous-run evidence

| Capability | Status | Evidence |
| --- | --- | --- |
| Deterministic save bootstrap | Verified | A fresh full-screen run opened Load and selected `BridgeTest` with two bridge actions and zero model decisions. |
| Continuous clock and bounded idle | Verified in a 14.5-minute recording | Harness-owned freezing removed on 2026-09-03 at Rahul's request. All 368 loaded-world observations reported simulationPaused=false; native menu pauses remain. `idle` waits without key input. |
| Nearby object metadata | Verified | Farm state reported Weeds, Stone, and Twig tiles with Scythe, Pickaxe, and Axe recommendations. |
| Keyboard sequence | Verified | The actor selected `control_sequence` and traversed multiple tiles in one model turn. |
| Collision-aware navigation | Superseded | The harness-side 25x25 grid navigation was verified but failed 75% of calls in the continuous run because `isTilePassable` ignores trees, debris, buildings, and characters. Replaced by bridge-side `navigate` and `go_to_location` using the player's bounding box and `isCollidingPosition`; builds clean, runtime re-test pending. |
| Bridge-side navigation and location transit | Pending | Run `bridge-test`, then a bounded run with `navigate_to` on the Farm and `go_to_location BusStop` / `FarmHouse`; confirm arrival reasons, door action, and stall detection. |
| Richer structured state | Pending | Confirm `cropsNearby`, `npcsNearby`, `shopItems` (with screen centers at Pierre's), `dialogueText`, `inventoryCounts`, and zoom-corrected `nearbyObjects` screen coordinates in a saved observation. |
| Door interaction | Verified | Facing the exterior farmhouse door and pressing `X` changed location from `Farm` to `FarmHouse`. |
| Dialogue selection | Verified | Question responses are exposed by index and the bridge invokes the selected response's real clickable component. |
| Objective cycle | Verified | Objective 17 completed only after `time>=610`, `location=FarmHouse`, and `playerFree=true`; objective 18 was then created and play continued. |
| Debris clearing | Verified | The continuing objective selected the Scythe, cleared weeds, and increased Fiber from zero to three. |
| Full-screen recording | Verified | Segmented H.264 capture produced valid 1920-by-1080 MP4 files; a 1:51 action-focused review copy was rendered from two segments. |
