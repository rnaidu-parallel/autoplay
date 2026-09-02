using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Menus;
using StardewValley.Monsters;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

namespace Autoplay.GameBridge;

public sealed class ModEntry : Mod
{
    private static readonly HashSet<SButton> AgentButtons = new()
    {
        SButton.W,
        SButton.A,
        SButton.S,
        SButton.D,
        SButton.LeftShift,
        SButton.X,
        SButton.C,
        SButton.E,
        SButton.Escape,
        SButton.F,
        SButton.M,
        SButton.Y,
        SButton.N,
        SButton.Tab,
        SButton.D0,
        SButton.D1,
        SButton.D2,
        SButton.D3,
        SButton.D4,
        SButton.D5,
        SButton.D6,
        SButton.D7,
        SButton.D8,
        SButton.D9,
        SButton.OemMinus,
        SButton.OemPlus
    };

    private IModHelper helper = null!;
    private BridgePipeServer pipeServer = null!;
    private BridgeRequestEnvelope? activePipeRequest;
    private SButton[] heldButtons = Array.Empty<SButton>();
    private int ticksRemaining;
    private int focusWarmupTicks;
    private bool cursorPending;
    private bool cursorMoveApplied;
    private int pendingCursorX;
    private int pendingCursorY;
    private int stateDelayTicks;
    private string? delayedStateReason;
    private bool logStateAfterTick;
    private Func<bool>? waitCondition;
    private string? waitDescription;
    private int waitTimeoutTicks;
    private int waitTicksRemaining;
    private SButton pendingClickButton = SButton.None;
    private bool pendingMenuLeftRelease;
    private int pendingMenuX;
    private int pendingMenuY;
    private SButton physicalMouseButton = SButton.None;
    private bool dragPending;
    private bool dragActive;
    private int dragStartX;
    private int dragStartY;
    private int dragEndX;
    private int dragEndY;
    private int dragTicksTotal;
    private int dragTicksElapsed;
    private SButton dragButton = SButton.None;
    private int scrollTicksRemaining;
    private int scrollDelta;
    private int idleTicksRemaining;
    private bool focusPending;
    private bool? originalPauseWhenOutOfFocus;
    private bool? originalSimulationPaused;
    private bool simulationOwned;
    private string? delayedStatus;
    private List<Point>? navigationPath;
    private int navigationIndex;
    private int navigationTicksRemaining;
    private int navigationStallTicks;
    private Point navigationLastPixel;
    private string navigationLocationName = string.Empty;
    private string? navigationTargetLocation;
    private bool navigationRequiresAction;
    private Point? navigationExitPush;
    private int navigationActionPhase;
    private int navigationActionTicks;
    private int saveCount;
    private BridgeWorldMap? worldMapCache;
    private int worldMapVersion;

    private const int NavigationArrivalTolerance = 6;
    private const int NavigationStallLimit = 20;
    private const int NavigationExpansionLimit = 8000;

    public override void Entry(IModHelper helper)
    {
        this.helper = helper;
        this.pipeServer = new BridgePipeServer();
        this.pipeServer.Start();
        this.Monitor.Log("Autoplay deterministic bridge loaded.", LogLevel.Info);
        this.Monitor.Log($"Autoplay harness pipe ready name={BridgePipeServer.PipeName}", LogLevel.Info);

        helper.ConsoleCommands.Add(
            "autoplay",
            "Deterministic bridge controls.\n"
                + "Usage:\n"
                + "  autoplay state\n"
                + "  autoplay bindings\n"
                + "  autoplay tabs\n"
                + "  autoplay responses\n"
                + "  autoplay cursor <screen_x> <screen_y>\n"
                + "  autoplay press <SButton[+SButton...]>\n"
                + "  autoplay hold <SButton[+SButton...]> <ticks>\n"
                + "  autoplay wait <field=value> <timeout_ticks>\n"
                + "  autoplay stop",
            this.OnCommand
        );

        helper.Events.GameLoop.SaveLoaded += this.OnSaveLoaded;
        helper.Events.GameLoop.Saved += this.OnSaved;
        helper.Events.GameLoop.UpdateTicking += this.OnUpdateTicking;
        helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;
    }

    private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
    {
        this.worldMapCache = null;
        this.LogState("save_loaded");
    }

    private void OnSaved(object? sender, SavedEventArgs e)
    {
        this.saveCount++;
    }

    private void OnCommand(string command, string[] args)
    {
        if (args.Length == 0)
        {
            this.Monitor.Log("Usage: autoplay <state|bindings|tabs|responses|cursor|press|hold|wait|stop>", LogLevel.Info);
            return;
        }

        switch (args[0].ToLowerInvariant())
        {
            case "state":
                this.LogState("requested");
                break;

            case "bindings":
                this.LogBindings();
                break;

            case "tabs":
                this.LogMenuTabs();
                break;

            case "responses":
                this.LogDialogueResponses();
                break;

            case "cursor" when args.Length == 3
                && int.TryParse(args[1], out int cursorX)
                && int.TryParse(args[2], out int cursorY)
                && cursorX >= 0
                && cursorY >= 0:
                this.StartCursorMove(cursorX, cursorY);
                break;

            case "press" when args.Length == 2 && TryParseButtons(args[1], out SButton[] pressButtons):
                this.StartHold(pressButtons, 1);
                break;

            case "hold" when args.Length == 3
                && TryParseButtons(args[1], out SButton[] holdButtons)
                && int.TryParse(args[2], out int ticks)
                && ticks is >= 1 and <= 600:
                this.StartHold(holdButtons, ticks);
                break;

            case "wait" when args.Length == 3
                && TryCreateWaitCondition(args[1], out Func<bool>? condition, out string? description)
                && int.TryParse(args[2], out int timeoutTicks)
                && timeoutTicks is >= 1 and <= 600:
                this.StartWait(condition, description, timeoutTicks);
                break;

            case "stop":
                this.RestorePauseWhenOutOfFocus();
                this.RestoreSimulationPause();
                this.StopOperation("stopped");
                this.CompletePipeRequest("stopped", pauseAfter: false);
                break;

            default:
                this.Monitor.Log(
                    "Invalid command. Use 'help autoplay' for valid syntax. Hold duration must be 1-600 ticks.",
                    LogLevel.Warn
                );
                break;
        }
    }

    private void StartHold(SButton[] buttons, int ticks)
    {
        if (this.IsOperationBusy())
        {
            this.LogOperationBusy();
            return;
        }

        this.heldButtons = buttons;
        this.ticksRemaining = ticks;
        this.focusWarmupTicks = 2;
        this.logStateAfterTick = false;
        this.Monitor.Log($"control_started buttons={string.Join('+', buttons)} ticks={ticks}", LogLevel.Info);
    }

    private void StartCursorMove(int x, int y)
    {
        if (this.IsOperationBusy())
        {
            this.LogOperationBusy();
            return;
        }

        this.cursorPending = true;
        this.cursorMoveApplied = false;
        this.pendingCursorX = x;
        this.pendingCursorY = y;
        this.focusWarmupTicks = 2;
        this.Monitor.Log($"cursor_started screen_x={x} screen_y={y}", LogLevel.Info);
    }

    private void StartClick(int x, int y, SButton button)
    {
        this.cursorPending = true;
        this.cursorMoveApplied = false;
        this.pendingCursorX = x;
        this.pendingCursorY = y;
        this.pendingClickButton = button;
        this.focusWarmupTicks = 2;
        this.Monitor.Log($"click_started screen_x={x} screen_y={y} button={button}", LogLevel.Info);
    }

    private void StartDrag(int startX, int startY, int endX, int endY, SButton button, int ticks)
    {
        this.cursorPending = true;
        this.cursorMoveApplied = false;
        this.pendingCursorX = startX;
        this.pendingCursorY = startY;
        this.focusWarmupTicks = 2;
        this.dragPending = true;
        this.dragStartX = startX;
        this.dragStartY = startY;
        this.dragEndX = endX;
        this.dragEndY = endY;
        this.dragTicksTotal = ticks;
        this.dragTicksElapsed = 0;
        this.dragButton = button;
        this.Monitor.Log(
            $"drag_started start_x={startX} start_y={startY} end_x={endX} end_y={endY} button={button} ticks={ticks}",
            LogLevel.Info
        );
    }

    private void StartScroll(string direction, int steps)
    {
        this.scrollTicksRemaining = steps;
        this.scrollDelta = direction.Equals("up", StringComparison.OrdinalIgnoreCase) ? 120 : -120;
        this.focusWarmupTicks = 2;
        this.Monitor.Log($"scroll_started direction={direction} steps={steps}", LogLevel.Info);
    }

    private void StartIdle(int ticks)
    {
        if (this.IsOperationBusy())
        {
            this.LogOperationBusy();
            return;
        }

        this.idleTicksRemaining = ticks;
        this.focusWarmupTicks = 2;
        this.Monitor.Log($"idle_started ticks={ticks}", LogLevel.Info);
    }

    private void SelectDialogueResponse(int index)
    {
        if (Game1.activeClickableMenu is not DialogueBox dialogueBox
            || !dialogueBox.isQuestion
            || index < 0
            || index >= dialogueBox.responses.Length
            // responseCC stays null until the question box finishes opening; reading it there
            // threw inside the update tick and left the pipe request permanently unanswered.
            || index >= (dialogueBox.responseCC?.Count ?? 0))
        {
            this.CompletePipeRequest("rejected", "dialogue_response_unavailable");
            return;
        }

        ClickableComponent component = dialogueBox.responseCC![index];
        int x = component.bounds.Center.X;
        int y = component.bounds.Center.Y;
        dialogueBox.receiveLeftClick(x, y, true);
        dialogueBox.releaseLeftClick(x, y);
        this.ScheduleState("dialogue_response_selected");
    }

    private void StartWait(Func<bool> condition, string description, int timeoutTicks)
    {
        if (this.IsOperationBusy())
        {
            this.LogOperationBusy();
            return;
        }

        this.waitCondition = condition;
        this.waitDescription = description;
        this.waitTimeoutTicks = timeoutTicks;
        this.waitTicksRemaining = timeoutTicks;
        this.Monitor.Log($"wait_started condition={description} timeout_ticks={timeoutTicks}", LogLevel.Info);
    }

    private void StartFocus()
    {
        this.focusPending = true;
        this.focusWarmupTicks = 2;
    }

    private void StopOperation(string reason)
    {
        if (this.heldButtons.Length > 0)
            this.Monitor.Log(
                $"control_{reason} buttons={string.Join('+', this.heldButtons)} ticks_remaining={this.ticksRemaining}",
                LogLevel.Info
            );

        if (this.waitCondition is not null)
            this.Monitor.Log(
                $"wait_{reason} condition={this.waitDescription} ticks_remaining={this.waitTicksRemaining}",
                LogLevel.Info
            );

        this.heldButtons = Array.Empty<SButton>();
        this.ticksRemaining = 0;
        this.cursorPending = false;
        this.cursorMoveApplied = false;
        this.focusWarmupTicks = 0;
        this.stateDelayTicks = 0;
        this.delayedStateReason = null;
        this.logStateAfterTick = false;
        this.waitCondition = null;
        this.waitDescription = null;
        this.waitTimeoutTicks = 0;
        this.waitTicksRemaining = 0;
        this.pendingClickButton = SButton.None;
        this.pendingMenuLeftRelease = false;
        if (this.physicalMouseButton != SButton.None)
        {
            ReleasePhysicalMouseButton(this.physicalMouseButton);
            this.physicalMouseButton = SButton.None;
        }
        this.dragPending = false;
        this.dragActive = false;
        this.dragTicksTotal = 0;
        this.dragTicksElapsed = 0;
        this.dragButton = SButton.None;
        this.scrollTicksRemaining = 0;
        this.scrollDelta = 0;
        this.idleTicksRemaining = 0;
        this.focusPending = false;
        this.delayedStatus = null;
        this.ClearNavigation();
    }

    private void ClearNavigation()
    {
        this.navigationPath = null;
        this.navigationIndex = 0;
        this.navigationTicksRemaining = 0;
        this.navigationStallTicks = 0;
        this.navigationTargetLocation = null;
        this.navigationRequiresAction = false;
        this.navigationExitPush = null;
        this.navigationActionPhase = 0;
        this.navigationActionTicks = 0;
    }

    private void StartNavigation(
        Point target,
        int maxTicks,
        string? targetLocation,
        bool requiresAction,
        Point? exitPush = null
    )
    {
        if (!Context.IsWorldReady || !IsPlayerFreeStrict())
        {
            this.CompletePipeRequest("rejected", "player_not_free");
            return;
        }

        GameLocation location = Game1.currentLocation;
        List<Point>? path = FindPath(location, Game1.player.TilePoint, target, allowUnwalkableTarget: targetLocation is not null);
        if (path is null)
        {
            this.CompletePipeRequest("blocked", "no_walkable_path");
            return;
        }

        this.ResumeSimulation();
        this.navigationPath = path;
        this.navigationIndex = 0;
        this.navigationTicksRemaining = maxTicks;
        this.navigationStallTicks = 0;
        this.navigationLastPixel = Game1.player.StandingPixel;
        this.navigationLocationName = location.NameOrUniqueName;
        this.navigationTargetLocation = targetLocation;
        this.navigationRequiresAction = requiresAction;
        this.navigationExitPush = exitPush;
        this.navigationActionPhase = 0;
        this.navigationActionTicks = 0;
        this.focusWarmupTicks = 2;
        this.Monitor.Log(
            $"navigation_started target=({target.X},{target.Y}) steps={path.Count} max_ticks={maxTicks} "
                + $"target_location={targetLocation ?? "none"} requires_action={requiresAction} "
                + $"exitPush={(exitPush is Point push ? $"({push.X},{push.Y})" : "none")}",
            LogLevel.Info
        );
    }

    private void StartLocationTransit(string targetLocation, int maxTicks)
    {
        if (!Context.IsWorldReady || !IsPlayerFreeStrict())
        {
            this.CompletePipeRequest("rejected", "player_not_free");
            return;
        }

        (Point tile, bool requiresAction, Point? exitPush)? exit = FindExit(Game1.currentLocation, targetLocation);
        if (exit is null)
        {
            this.CompletePipeRequest("blocked", "no_exit_to_location");
            return;
        }

        this.StartNavigation(
            exit.Value.tile,
            maxTicks,
            targetLocation,
            exit.Value.requiresAction,
            exit.Value.exitPush
        );
    }

    private void DriveNavigation()
    {
        if (this.focusWarmupTicks-- > 0)
        {
            if (!Game1.game1.IsActive)
                ActivateGameWindow();
            return;
        }

        string currentLocation = Game1.currentLocation.NameOrUniqueName;
        if (this.navigationTargetLocation is not null
            && currentLocation.Equals(this.navigationTargetLocation, StringComparison.OrdinalIgnoreCase))
        {
            this.FinishNavigation("completed", "arrived_in_target_location");
            return;
        }

        if (currentLocation != this.navigationLocationName)
        {
            this.FinishNavigation("interrupted", "location_changed");
            return;
        }

        if (Game1.isWarping)
        {
            this.navigationStallTicks = 0;
            return;
        }

        if (this.navigationActionPhase == 0 && (!IsPlayerFreeStrict() || Game1.activeClickableMenu is not null || Game1.eventUp))
        {
            this.FinishNavigation("interrupted", "player_not_free");
            return;
        }

        if (this.navigationTicksRemaining-- <= 0)
        {
            this.FinishNavigation("timeout", "tick_budget_exhausted");
            return;
        }

        if (this.navigationActionPhase > 0)
        {
            if (this.navigationActionPhase == 4)
                this.DriveEdgeWarp();
            else
                this.DriveDoorAction();
            return;
        }

        List<Point> path = this.navigationPath!;
        Point standing = Game1.player.StandingPixel;
        while (this.navigationIndex < path.Count && IsWithinTolerance(standing, path[this.navigationIndex]))
            this.navigationIndex++;

        if (this.navigationIndex >= path.Count)
        {
            if (!this.navigationRequiresAction && this.navigationExitPush is not null)
            {
                this.navigationActionPhase = 4;
                this.navigationActionTicks = 0;
                return;
            }

            if (this.navigationRequiresAction)
            {
                this.navigationActionPhase = 1;
                this.navigationActionTicks = 0;
                return;
            }

            this.FinishNavigation("completed", "arrived");
            return;
        }

        Point waypoint = path[this.navigationIndex];
        int deltaX = (waypoint.X * Game1.tileSize) + (Game1.tileSize / 2) - standing.X;
        int deltaY = (waypoint.Y * Game1.tileSize) + (Game1.tileSize / 2) - standing.Y;
        if (Math.Abs(deltaX) > NavigationArrivalTolerance)
            this.helper.Input.Press(deltaX > 0 ? SButton.D : SButton.A);
        if (Math.Abs(deltaY) > NavigationArrivalTolerance)
            this.helper.Input.Press(deltaY > 0 ? SButton.S : SButton.W);

        if (standing == this.navigationLastPixel)
        {
            if (++this.navigationStallTicks >= NavigationStallLimit)
            {
                this.FinishNavigation("blocked", $"no_progress_toward_({waypoint.X},{waypoint.Y})");
                return;
            }
        }
        else
        {
            this.navigationStallTicks = 0;
        }

        this.navigationLastPixel = standing;
    }

    private void DriveEdgeWarp()
    {
        this.navigationActionTicks++;
        Point push = this.navigationExitPush!.Value;
        if (push.X < 0)
            this.helper.Input.Press(SButton.A);
        else if (push.X > 0)
            this.helper.Input.Press(SButton.D);
        else if (push.Y < 0)
            this.helper.Input.Press(SButton.W);
        else
            this.helper.Input.Press(SButton.S);

        if (this.navigationActionTicks >= 16)
            this.FinishNavigation("blocked", "edge_warp_did_not_trigger");
    }

    private void DriveDoorAction()
    {
        // Phase 1: walk into the door above the standing tile. Phase 2: press the action key. Phase 3: wait for the warp.
        this.navigationActionTicks++;
        switch (this.navigationActionPhase)
        {
            case 1:
                if (this.navigationActionTicks == 1)
                {
                    Point standTile = Game1.player.TilePoint;
                    int doorScreenX = (int)((((standTile.X * Game1.tileSize) + (Game1.tileSize / 2))
                        - Game1.viewport.X) * Game1.options.zoomLevel);
                    int doorScreenY = (int)(((((standTile.Y - 1) * Game1.tileSize) + (Game1.tileSize / 2))
                        - Game1.viewport.Y) * Game1.options.zoomLevel);
                    Game1.setMousePosition(doorScreenX, doorScreenY);
                }
                this.helper.Input.Press(SButton.W);
                if (this.navigationActionTicks >= 16)
                {
                    this.navigationActionPhase = 2;
                    this.navigationActionTicks = 0;
                }
                return;

            case 2:
                if (this.navigationActionTicks == 4)
                    this.helper.Input.Press(SButton.X);
                if (this.navigationActionTicks >= 5)
                {
                    this.navigationActionPhase = 3;
                    this.navigationActionTicks = 0;
                }
                return;

            default:
                if (this.navigationActionTicks >= 90)
                    this.FinishNavigation("blocked", "door_action_did_not_change_location");
                return;
        }
    }

    private void FinishNavigation(string status, string reason)
    {
        this.Monitor.Log($"navigation_{status} reason={reason} ticks_remaining={this.navigationTicksRemaining}", LogLevel.Info);
        this.ClearNavigation();
        this.ScheduleCompletion(status, reason);
    }

    private static bool IsWithinTolerance(Point standingPixel, Point tile)
    {
        int centerX = (tile.X * Game1.tileSize) + (Game1.tileSize / 2);
        int centerY = (tile.Y * Game1.tileSize) + (Game1.tileSize / 2);
        return Math.Abs(standingPixel.X - centerX) <= NavigationArrivalTolerance
            && Math.Abs(standingPixel.Y - centerY) <= NavigationArrivalTolerance;
    }

    private static bool IsTileWalkable(GameLocation location, Point tile)
    {
        Rectangle box = Game1.player.GetBoundingBox();
        Point playerTile = Game1.player.TilePoint;
        box.Offset((tile.X - playerTile.X) * Game1.tileSize, (tile.Y - playerTile.Y) * Game1.tileSize);
        return !location.isCollidingPosition(box, Game1.viewport, true, 0, false, Game1.player);
    }

    private static List<Point>? FindPath(GameLocation location, Point start, Point target, bool allowUnwalkableTarget)
    {
        int width = location.Map.Layers[0].LayerWidth;
        int height = location.Map.Layers[0].LayerHeight;
        bool InBounds(Point point) => point.X >= 0 && point.Y >= 0 && point.X < width && point.Y < height;

        if (start == target)
            return new List<Point>();
        if (!allowUnwalkableTarget && (!InBounds(target) || !IsTileWalkable(location, target)))
            return null;

        Point[] deltas = { new(0, -1), new(-1, 0), new(0, 1), new(1, 0) };
        var previous = new Dictionary<Point, Point> { [start] = start };
        var queue = new Queue<Point>();
        queue.Enqueue(start);
        int expanded = 0;
        while (queue.Count > 0 && expanded < NavigationExpansionLimit)
        {
            Point current = queue.Dequeue();
            expanded++;
            foreach (Point delta in deltas)
            {
                var next = new Point(current.X + delta.X, current.Y + delta.Y);
                if (previous.ContainsKey(next))
                    continue;
                bool isTarget = next == target;
                if (!isTarget && (!InBounds(next) || !IsTileWalkable(location, next)))
                    continue;

                previous[next] = current;
                if (isTarget)
                {
                    var path = new List<Point>();
                    for (Point step = next; step != start; step = previous[step])
                        path.Add(step);
                    path.Reverse();
                    return path;
                }

                queue.Enqueue(next);
            }
        }

        return null;
    }

    private static (Point tile, bool requiresAction, Point? exitPush)? FindExit(
        GameLocation location,
        string targetLocation
    )
    {
        Point playerTile = Game1.player.TilePoint;
        int width = location.Map.Layers[0].LayerWidth;
        int height = location.Map.Layers[0].LayerHeight;
        var warp = location.warps
            .Where(candidate => candidate.TargetName.Equals(targetLocation, StringComparison.OrdinalIgnoreCase))
            .Select(candidate =>
            {
                var tile = new Point(
                    Math.Clamp(candidate.X, 0, width - 1),
                    Math.Clamp(candidate.Y, 0, height - 1)
                );
                bool outside = tile.X != candidate.X || tile.Y != candidate.Y;
                Point? exitPush = outside
                    ? new Point(Math.Sign(candidate.X - tile.X), Math.Sign(candidate.Y - tile.Y))
                    : null;
                return new { tile, exitPush };
            })
            .Where(candidate => IsTileWalkable(location, candidate.tile))
            .OrderBy(candidate => Math.Abs(candidate.tile.X - playerTile.X) + Math.Abs(candidate.tile.Y - playerTile.Y))
            .FirstOrDefault();
        if (warp is not null)
            return (warp.tile, false, warp.exitPush);

        foreach (var building in location.buildings)
        {
            // The main farmhouse keeps its interior as a top-level location, so indoors.Value is
            // null there and only the building's indoors name identifies the door's destination.
            string? indoors = building.indoors.Value?.NameOrUniqueName ?? building.GetIndoorsName();
            if (indoors is null || !indoors.Equals(targetLocation, StringComparison.OrdinalIgnoreCase))
                continue;
            Point door = building.humanDoor.Value;
            return (new Point(building.tileX.Value + door.X, building.tileY.Value + door.Y + 1), true, null);
        }

        (Point tile, bool requiresAction, Point? exitPush)? best = null;
        int bestDistance = int.MaxValue;
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                foreach ((string kind, string layer) in new[] { ("Action", "Buildings"), ("TouchAction", "Back") })
                {
                    string? action = location.doesTileHaveProperty(x, y, kind, layer);
                    if (string.IsNullOrWhiteSpace(action))
                        continue;
                    string[] parts = action.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                    bool isWarp = parts.Length >= 4
                        && (parts[0] is "Warp" or "LockedDoorWarp" or "WarpWomensLocker" or "WarpMensLocker")
                        && parts[3].Equals(targetLocation, StringComparison.OrdinalIgnoreCase);
                    if (!isWarp)
                        continue;
                    bool requiresAction = kind == "Action";
                    var tile = new Point(x, requiresAction ? y + 1 : y);
                    int distance = Math.Abs(tile.X - playerTile.X) + Math.Abs(tile.Y - playerTile.Y);
                    if (distance < bestDistance)
                    {
                        bestDistance = distance;
                        best = (tile, requiresAction, null);
                    }
                }
            }
        }

        return best;
    }

    private BridgeWorldMap GetWorldMap()
    {
        if (this.worldMapCache is not null)
            return this.worldMapCache;

        var locations = Game1.locations.ToList();
        var knownLocations = new Dictionary<string, GameLocation>(StringComparer.OrdinalIgnoreCase);
        foreach (GameLocation location in locations)
            knownLocations.TryAdd(location.NameOrUniqueName, location);
        for (int index = 0; index < locations.Count; index++)
        {
            foreach (var building in locations[index].buildings)
            {
                string? indoorsName = building.indoors.Value?.NameOrUniqueName ?? building.GetIndoorsName();
                if (string.IsNullOrWhiteSpace(indoorsName) || knownLocations.ContainsKey(indoorsName))
                    continue;
                GameLocation? indoors = building.indoors.Value ?? Game1.getLocationFromName(indoorsName);
                if (indoors is not null)
                {
                    knownLocations.Add(indoors.NameOrUniqueName, indoors);
                    locations.Add(indoors);
                }
            }
        }

        var edges = new List<BridgeWorldMapEdge>();
        var seenEdges = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        void AddEdge(BridgeWorldMapEdge edge)
        {
            if (knownLocations.ContainsKey(edge.To)
                && seenEdges.Add($"{edge.From}\u001f{edge.To}\u001f{edge.Kind}"))
            {
                edges.Add(edge);
            }
        }

        foreach (GameLocation location in locations)
        {
            string from = location.NameOrUniqueName;
            foreach (Warp warp in location.warps)
            {
                AddEdge(new BridgeWorldMapEdge
                {
                    From = from,
                    To = warp.TargetName,
                    X = warp.X,
                    Y = warp.Y,
                    Kind = "warp"
                });
            }

            foreach (var building in location.buildings)
            {
                string? indoors = building.indoors.Value?.NameOrUniqueName ?? building.GetIndoorsName();
                if (string.IsNullOrWhiteSpace(indoors))
                    continue;
                Point door = building.humanDoor.Value;
                AddEdge(new BridgeWorldMapEdge
                {
                    From = from,
                    To = indoors,
                    X = building.tileX.Value + door.X,
                    Y = building.tileY.Value + door.Y,
                    Kind = "door",
                    RequiresAction = true
                });
            }

            int width = location.Map.Layers[0].LayerWidth;
            int height = location.Map.Layers[0].LayerHeight;
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    string? action = location.doesTileHaveProperty(x, y, "Action", "Buildings");
                    if (string.IsNullOrWhiteSpace(action))
                        continue;
                    string[] parts = action.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length < 4
                        || parts[0] is not ("Warp" or "LockedDoorWarp" or "WarpWomensLocker" or "WarpMensLocker"))
                    {
                        continue;
                    }
                    int? openTime = null;
                    int? closeTime = null;
                    if (parts[0] == "LockedDoorWarp" && parts.Length >= 6)
                    {
                        openTime = int.Parse(parts[4]);
                        closeTime = int.Parse(parts[5]);
                    }
                    AddEdge(new BridgeWorldMapEdge
                    {
                        From = from,
                        To = parts[3],
                        X = x,
                        Y = y,
                        Kind = "action_warp",
                        RequiresAction = true,
                        OpenTime = openTime,
                        CloseTime = closeTime
                    });
                }
            }
        }

        this.worldMapCache = new BridgeWorldMap
        {
            Nodes = locations.Select(location => new BridgeWorldMapNode
            {
                Name = location.NameOrUniqueName,
                IsOutdoors = location.IsOutdoors,
                IsFarm = location is Farm
            }).ToArray(),
            Edges = edges
        };
        this.worldMapVersion++;
        return this.worldMapCache;
    }

    private void OnUpdateTicking(object? sender, UpdateTickingEventArgs e)
    {
        this.StartNextPipeRequest();

        if (this.pendingMenuLeftRelease)
        {
            Game1.activeClickableMenu?.releaseLeftClick(this.pendingMenuX, this.pendingMenuY);
            this.pendingMenuLeftRelease = false;
            this.ScheduleState("menu_click_released");
            return;
        }

        if (this.focusPending)
        {
            ActivateGameWindow();
            if (this.focusWarmupTicks-- > 0)
                return;

            this.focusPending = false;
            this.ScheduleState("focused");
            return;
        }

        if (this.cursorPending)
        {
            ActivateGameWindow();
            if (this.focusWarmupTicks-- > 0)
                return;

            if (!this.cursorMoveApplied)
            {
                Game1.setMousePosition(this.pendingCursorX, this.pendingCursorY);
                SetPhysicalCursorPosition(this.pendingCursorX, this.pendingCursorY);
                if (this.pendingClickButton != SButton.None || this.dragPending)
                {
                    this.cursorMoveApplied = true;
                    return;
                }
            }

            this.cursorPending = false;
            this.cursorMoveApplied = false;

            if (this.pendingClickButton != SButton.None)
            {
                if (Game1.activeClickableMenu is not null)
                {
                    Microsoft.Xna.Framework.Vector2 menuPosition = Utility.ModifyCoordinatesForUIScale(
                        new Microsoft.Xna.Framework.Vector2(this.pendingCursorX, this.pendingCursorY)
                    );
                    int menuX = (int)menuPosition.X;
                    int menuY = (int)menuPosition.Y;
                    if (this.pendingClickButton == SButton.MouseLeft)
                    {
                        Game1.activeClickableMenu.receiveLeftClick(menuX, menuY, true);
                        this.pendingMenuLeftRelease = true;
                        this.pendingMenuX = menuX;
                        this.pendingMenuY = menuY;
                    }
                    else
                        Game1.activeClickableMenu.receiveRightClick(menuX, menuY, true);
                    this.Monitor.Log(
                        $"menu_click screen_x={this.pendingCursorX} screen_y={this.pendingCursorY} ui_x={menuX} ui_y={menuY}"
                    );
                    this.pendingClickButton = SButton.None;
                    if (!this.pendingMenuLeftRelease)
                        this.ScheduleState("menu_click_released");
                    return;
                }

                this.physicalMouseButton = this.pendingClickButton;
                this.pendingClickButton = SButton.None;
                PressPhysicalMouseButton(this.physicalMouseButton);
                return;
            }

            if (this.dragPending)
            {
                this.helper.Input.Press(this.dragButton);
                this.dragPending = false;
                this.dragActive = true;
                return;
            }

            this.ScheduleState("cursor_set");
            return;
        }

        if (this.physicalMouseButton != SButton.None)
        {
            ReleasePhysicalMouseButton(this.physicalMouseButton);
            this.physicalMouseButton = SButton.None;
            this.ScheduleState("click_released");
            return;
        }

        if (this.dragActive)
        {
            ActivateGameWindow();
            this.dragTicksElapsed++;
            float progress = (float)this.dragTicksElapsed / this.dragTicksTotal;
            int x = (int)MathF.Round(this.dragStartX + ((this.dragEndX - this.dragStartX) * progress));
            int y = (int)MathF.Round(this.dragStartY + ((this.dragEndY - this.dragStartY) * progress));
            Game1.setMousePosition(x, y);
            this.helper.Input.Press(this.dragButton);

            if (this.dragTicksElapsed >= this.dragTicksTotal)
            {
                this.dragActive = false;
                this.dragButton = SButton.None;
                this.ScheduleState("drag_released");
            }

            return;
        }

        if (this.scrollTicksRemaining > 0)
        {
            ActivateGameWindow();
            if (this.focusWarmupTicks-- > 0)
                return;

            MouseEvent(0x0800, 0, 0, this.scrollDelta, UIntPtr.Zero);
            this.scrollTicksRemaining--;
            if (this.scrollTicksRemaining == 0)
                this.ScheduleState("scroll_completed");
            return;
        }

        if (this.idleTicksRemaining > 0)
        {
            if (!Game1.game1.IsActive)
                ActivateGameWindow();
            if (this.focusWarmupTicks-- > 0)
                return;

            this.idleTicksRemaining--;
            if (this.idleTicksRemaining == 0)
                this.ScheduleState("idle_completed");
            return;
        }

        if (this.navigationPath is not null)
        {
            this.DriveNavigation();
            return;
        }

        if (this.heldButtons.Length == 0 || this.ticksRemaining <= 0)
        {
            if (this.stateDelayTicks > 0 && --this.stateDelayTicks == 0)
                this.logStateAfterTick = true;
            return;
        }

        if (!Game1.game1.IsActive)
            ActivateGameWindow();
        if (this.focusWarmupTicks-- > 0)
            return;

        foreach (SButton button in this.heldButtons)
            this.helper.Input.Press(button);

        this.ticksRemaining--;

        if (this.ticksRemaining == 0)
        {
            this.heldButtons = Array.Empty<SButton>();
            this.ScheduleState("control_released");
        }
    }

    private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
    {
        if (this.waitCondition is not null)
        {
            if (this.waitCondition())
            {
                string condition = this.waitDescription ?? "unknown";
                int elapsedTicks = this.waitTimeoutTicks - this.waitTicksRemaining;
                this.ClearWait();
                this.Monitor.Log($"wait_completed condition={condition} elapsed_ticks={elapsedTicks}", LogLevel.Info);
                this.LogState("wait_completed");
                this.CompletePipeRequest("completed");
                return;
            }

            if (--this.waitTicksRemaining == 0)
            {
                string condition = this.waitDescription ?? "unknown";
                this.ClearWait();
                this.Monitor.Log($"wait_timeout condition={condition}", LogLevel.Warn);
                this.LogState("wait_timeout");
                this.CompletePipeRequest("timeout");
                return;
            }
        }

        if (!this.logStateAfterTick)
            return;

        this.logStateAfterTick = false;
        string reason = this.delayedStateReason ?? "operation_completed";
        string status = this.delayedStatus ?? "completed";
        this.delayedStateReason = null;
        this.delayedStatus = null;
        this.LogState(reason);
        this.CompletePipeRequest(status, reason: reason);
    }

    private void LogState(string reason)
    {
        string menu = GetMenuState();

        if (!Context.IsWorldReady)
        {
            this.Monitor.Log($"state reason={reason} world_ready=false menu={menu}", LogLevel.Info);
            return;
        }

        var tile = Game1.player.TilePoint;
        var position = Game1.player.Position;
        ICursorPosition cursor = this.helper.Input.GetCursorPosition();
        string tool = Game1.player.CurrentTool?.Name ?? "none";
        this.Monitor.Log(
            $"state reason={reason} world_ready=true game_active={Game1.game1.IsActive} "
                + $"player_free={Context.IsPlayerFree} can_move={Context.CanPlayerMove} "
                + $"menu={menu} location={Game1.currentLocation.NameOrUniqueName} tile_x={tile.X} tile_y={tile.Y} "
                + $"pixel_x={(int)position.X} pixel_y={(int)position.Y} facing={Game1.player.FacingDirection} "
                + $"toolbar_index={Game1.player.CurrentToolIndex} tool={tool} using_tool={Game1.player.UsingTool} "
                + $"stamina={(int)Game1.player.Stamina} time={Game1.timeOfDay} "
                + $"cursor_screen_x={(int)cursor.ScreenPixels.X} cursor_screen_y={(int)cursor.ScreenPixels.Y} "
                + $"cursor_world_x={(int)cursor.AbsolutePixels.X} cursor_world_y={(int)cursor.AbsolutePixels.Y} "
                + $"cursor_tile_x={(int)cursor.Tile.X} cursor_tile_y={(int)cursor.Tile.Y} "
                + $"cursor_grab_x={(int)cursor.GrabTile.X} cursor_grab_y={(int)cursor.GrabTile.Y} "
                + $"event_up={Game1.eventUp} minigame={Game1.currentMinigame?.GetType().Name ?? "none"} "
                + $"cursor_item={Game1.player.CursorSlotItem?.DisplayName ?? "none"}",
            LogLevel.Info
        );
    }

    private static string GetMenuState()
    {
        if (Game1.activeClickableMenu is TitleMenu && TitleMenu.subMenu is not null)
            return $"TitleMenu:{TitleMenu.subMenu.GetType().Name}";

        if (Game1.activeClickableMenu is GameMenu gameMenu)
        {
            string page = gameMenu.currentTab >= 0 && gameMenu.currentTab < gameMenu.pages.Count
                ? gameMenu.pages[gameMenu.currentTab].GetType().Name
                : "unknown";
            return $"GameMenu:{gameMenu.currentTab}:{page}";
        }

        if (Game1.activeClickableMenu is DialogueBox dialogueBox)
            return $"DialogueBox:question={dialogueBox.isQuestion}:selected={dialogueBox.selectedResponse}:responses={dialogueBox.responses?.Length ?? 0}";

        return Game1.activeClickableMenu?.GetType().Name ?? "none";
    }

    private void LogBindings()
    {
        this.LogBinding("move_up", Game1.options.moveUpButton);
        this.LogBinding("move_down", Game1.options.moveDownButton);
        this.LogBinding("move_left", Game1.options.moveLeftButton);
        this.LogBinding("move_right", Game1.options.moveRightButton);
        this.LogBinding("run", Game1.options.runButton);
        this.LogBinding("action", Game1.options.actionButton);
        this.LogBinding("use_tool", Game1.options.useToolButton);
        this.LogBinding("menu", Game1.options.menuButton);
        this.LogBinding("journal", Game1.options.journalButton);
        this.LogBinding("map", Game1.options.mapButton);
        this.LogBinding("cancel", Game1.options.cancelButton);
        this.LogBinding("chat", Game1.options.chatButton);
        this.LogBinding("emote", Game1.options.emoteButton);
    }

    private void LogMenuTabs()
    {
        if (Game1.activeClickableMenu is not GameMenu gameMenu)
        {
            this.Monitor.Log("tabs unavailable menu_is_not_GameMenu", LogLevel.Warn);
            return;
        }

        for (int index = 0; index < gameMenu.tabs.Count; index++)
        {
            ClickableComponent tab = gameMenu.tabs[index];
            this.Monitor.Log(
                $"tab index={index} active={index == gameMenu.currentTab} name={tab.name} id={tab.myID} "
                    + $"x={tab.bounds.X} y={tab.bounds.Y} width={tab.bounds.Width} height={tab.bounds.Height}",
                LogLevel.Info
            );
        }
    }

    private void LogDialogueResponses()
    {
        if (Game1.activeClickableMenu is not DialogueBox dialogueBox || !dialogueBox.isQuestion)
        {
            this.Monitor.Log("responses unavailable menu_is_not_question_dialogue", LogLevel.Warn);
            return;
        }

        for (int index = 0; index < dialogueBox.responses.Length; index++)
        {
            Response response = dialogueBox.responses[index];
            ClickableComponent? component = index < dialogueBox.responseCC.Count
                ? dialogueBox.responseCC[index]
                : null;
            string bounds = component is null
                ? "bounds=unavailable"
                : $"x={component.bounds.X} y={component.bounds.Y} width={component.bounds.Width} height={component.bounds.Height}";
            this.Monitor.Log(
                $"response index={index} selected={index == dialogueBox.selectedResponse} key={response.responseKey} "
                    + $"text={response.responseText} hotkey={response.hotkey} {bounds}",
                LogLevel.Info
            );
        }
    }

    private void StartNextPipeRequest()
    {
        if (this.activePipeRequest is not null || this.IsOperationBusy())
            return;

        if (!this.pipeServer.TryDequeue(out BridgeRequestEnvelope? envelope) || envelope is null)
            return;

        this.activePipeRequest = envelope;
        BridgeRequest request = envelope.Request;
        string type = request.Type.ToLowerInvariant();

        switch (type)
        {
            case "observe":
                this.DisablePauseWhenOutOfFocus();
                this.PauseSimulation();
                this.StartFocus();
                return;

            case "world_map" when Context.IsWorldReady:
                this.CompleteWorldMapRequest(this.GetWorldMap());
                return;

            case "focus":
                this.StartFocus();
                return;

            case "press" when TryParseAgentButtons(request.Buttons, out SButton[] pressButtons):
                this.ResumeSimulation();
                this.StartHold(pressButtons, 1);
                return;

            case "hold" when request.Ticks is >= 1 and <= 600
                && TryParseAgentButtons(request.Buttons, out SButton[] holdButtons):
                this.ResumeSimulation();
                this.StartHold(holdButtons, request.Ticks);
                return;

            case "move_cursor" when this.IsValidScreenPoint(request.X, request.Y):
                this.ResumeSimulation();
                this.StartCursorMove(request.X, request.Y);
                return;

            case "click" when this.IsValidScreenPoint(request.X, request.Y)
                && TryParsePointerButton(request.Button, out SButton clickButton):
                this.ResumeSimulation();
                this.StartClick(request.X, request.Y, clickButton);
                return;

            case "drag" when request.Ticks is >= 1 and <= 120
                && this.IsValidScreenPoint(request.StartX, request.StartY)
                && this.IsValidScreenPoint(request.EndX, request.EndY)
                && TryParsePointerButton(request.Button, out SButton dragButton):
                this.ResumeSimulation();
                this.StartDrag(
                    request.StartX,
                    request.StartY,
                    request.EndX,
                    request.EndY,
                    dragButton,
                    request.Ticks
                );
                return;

            case "scroll" when request.Steps is >= 1 and <= 20:
                if (request.Direction.Equals("up", StringComparison.OrdinalIgnoreCase)
                    || request.Direction.Equals("down", StringComparison.OrdinalIgnoreCase))
                {
                    this.ResumeSimulation();
                    this.StartScroll(request.Direction, request.Steps);
                    return;
                }
                break;

            case "wait" when request.Ticks is >= 1 and <= 600
                && TryCreateWaitCondition(
                    $"{request.Field}={request.Value}",
                    out Func<bool>? waitCondition,
                    out string? waitDescription
                ):
                this.ResumeSimulation();
                this.StartWait(waitCondition, waitDescription, request.Ticks);
                return;

            case "idle" when request.Ticks is >= 1 and <= 600:
                this.ResumeSimulation();
                this.StartIdle(request.Ticks);
                return;

            case "choose_dialogue_response":
                this.ResumeSimulation();
                this.SelectDialogueResponse(request.Index);
                return;

            case "navigate" when request.Ticks is >= 1 and <= 600:
                this.StartNavigation(new Point(request.X, request.Y), request.Ticks, targetLocation: null, requiresAction: false);
                return;

            case "go_to_location" when request.Ticks is >= 1 and <= 600 && !string.IsNullOrWhiteSpace(request.Location):
                this.StartLocationTransit(request.Location.Trim(), request.Ticks);
                return;

            case "set_display_mode" when request.Mode.Equals("windowed", StringComparison.OrdinalIgnoreCase):
                Game1.options.setWindowedOption("Windowed");
                this.ScheduleState("display_mode_windowed");
                return;

            case "set_display_mode" when request.Mode.Equals("fullscreen", StringComparison.OrdinalIgnoreCase):
                Game1.options.setWindowedOption("Fullscreen");
                this.ScheduleState("display_mode_fullscreen");
                return;

            case "set_display_mode" when request.Mode.Equals("borderless", StringComparison.OrdinalIgnoreCase):
                Game1.options.setWindowedOption("Windowed Borderless");
                this.ScheduleState("display_mode_borderless");
                return;

            case "stop":
                this.RestorePauseWhenOutOfFocus();
                this.RestoreSimulationPause();
                this.StopOperation("stopped");
                this.CompletePipeRequest("stopped", pauseAfter: false);
                return;
        }

        this.CompletePipeRequest("error", "invalid_or_disallowed_request");
    }

    private void CompletePipeRequest(string status, string? error = null, bool pauseAfter = true, string? reason = null)
    {
        if (this.activePipeRequest is null)
            return;

        BridgeRequestEnvelope request = this.activePipeRequest;
        this.activePipeRequest = null;
        if (pauseAfter)
            this.PauseSimulation();
        request.Completion.TrySetResult(new BridgeResponse
        {
            Id = request.Request.Id,
            Status = status,
            Error = error,
            Reason = reason,
            State = this.CaptureState()
        });
    }

    private void CompleteWorldMapRequest(BridgeWorldMap worldMap)
    {
        if (this.activePipeRequest is null)
            return;
        BridgeRequestEnvelope request = this.activePipeRequest;
        this.activePipeRequest = null;
        request.Completion.TrySetResult(new BridgeResponse
        {
            Id = request.Request.Id,
            Status = "completed",
            State = this.CaptureState(),
            Nodes = worldMap.Nodes,
            Edges = worldMap.Edges
        });
    }

    private bool IsValidScreenPoint(int x, int y)
    {
        var viewport = Game1.graphics.GraphicsDevice.Viewport;
        return x >= 0 && y >= 0 && x < viewport.Width && y < viewport.Height;
    }

    private GameStateSnapshot CaptureState()
    {
        ICursorPosition cursor = this.helper.Input.GetCursorPosition();
        string menu = GetMenuState();
        IReadOnlyList<BridgeDialogueResponse> dialogueResponses = CaptureDialogueResponses();
        bool nightActive = Game1.farmEvent is not null || Game1.freezeControls
            || Game1.activeClickableMenu is SaveGameMenu;

        if (!Context.IsWorldReady)
        {
            return new GameStateSnapshot
            {
                WorldReady = false,
                GameActive = Game1.game1.IsActive,
                SimulationPaused = Game1.paused,
                PlayerFree = IsPlayerFreeStrict(),
                CanMove = Context.CanPlayerMove && !nightActive,
                NightActive = nightActive,
                SaveCount = this.saveCount,
                WorldMapVersion = this.worldMapVersion,
                GraphicsFullScreen = Game1.graphics.IsFullScreen,
                WindowedBorderless = Game1.options.windowedBorderlessFullscreen,
                ViewportWidth = Game1.graphics.GraphicsDevice.Viewport.Width,
                ViewportHeight = Game1.graphics.GraphicsDevice.Viewport.Height,
                Menu = menu,
                CursorScreenX = (int)cursor.ScreenPixels.X,
                CursorScreenY = (int)cursor.ScreenPixels.Y,
                CursorWorldX = (int)cursor.AbsolutePixels.X,
                CursorWorldY = (int)cursor.AbsolutePixels.Y,
                CursorTileX = (int)cursor.Tile.X,
                CursorTileY = (int)cursor.Tile.Y,
                CursorGrabX = (int)cursor.GrabTile.X,
                CursorGrabY = (int)cursor.GrabTile.Y,
                EventUp = Game1.eventUp,
                Minigame = Game1.currentMinigame?.GetType().Name ?? "none",
                DialogueResponses = dialogueResponses
            };
        }

        IReadOnlyList<BridgeInventoryItem> inventory = Game1.player.Items
            .Select((item, slot) => (item, slot))
            .Where(entry => entry.item is not null)
            .Select(entry => new BridgeInventoryItem
            {
                Slot = entry.slot,
                IsSeed = entry.item!.Category == StardewValley.Object.SeedsCategory,
                QualifiedId = entry.item!.QualifiedItemId,
                Name = entry.item.DisplayName,
                Stack = entry.item.Stack,
                Quality = entry.item is StardewValley.Object itemObject ? itemObject.Quality : 0
            })
            .ToArray();

        string weather = Game1.isLightning
            ? "storm"
            : Game1.isRaining
                ? "rain"
                : Game1.isSnowing
                    ? "snow"
                    : "sun";
        var tile = Game1.player.TilePoint;
        var position = Game1.player.Position;
        IReadOnlyList<BridgeWarp> warps = Game1.currentLocation.warps
            .Select(warp => new BridgeWarp
            {
                X = warp.X,
                Y = warp.Y,
                TargetName = warp.TargetName,
                TargetX = warp.TargetX,
                TargetY = warp.TargetY
            })
            .ToArray();
        GameLocation location = Game1.currentLocation;
        const int nearbyRadius = 12;
        bool IsNearby(int x, int y) => Math.Abs(x - tile.X) <= nearbyRadius && Math.Abs(y - tile.Y) <= nearbyRadius;
        var obstacles = new List<(int x, int y, string name, string tool)>();
        foreach (var entry in location.Objects.Pairs)
        {
            if (IsNearby((int)entry.Key.X, (int)entry.Key.Y))
                obstacles.Add(((int)entry.Key.X, (int)entry.Key.Y, entry.Value.DisplayName, RecommendedToolFor(entry.Value.Name)));
        }

        var cropsNearby = new List<BridgeCrop>();
        int tilledTiles = 0, plantedCrops = 0, wateredCrops = 0, harvestableCrops = 0;
        foreach (var entry in location.terrainFeatures.Pairs)
        {
            int x = (int)entry.Key.X;
            int y = (int)entry.Key.Y;
            switch (entry.Value)
            {
                case HoeDirt dirt:
                    tilledTiles++;
                    bool watered = dirt.state.Value == HoeDirt.watered;
                    bool ready = dirt.readyForHarvest();
                    bool dead = dirt.crop?.dead.Value == true;
                    if (dirt.crop is not null && !dead)
                    {
                        plantedCrops++;
                        if (watered)
                            wateredCrops++;
                        if (ready)
                            harvestableCrops++;
                    }
                    if (IsNearby(x, y))
                    {
                        cropsNearby.Add(new BridgeCrop
                        {
                            X = x,
                            Y = y,
                            ScreenX = (int)(((x * Game1.tileSize + Game1.tileSize / 2) - Game1.viewport.X) * Game1.options.zoomLevel),
                            ScreenY = (int)(((y * Game1.tileSize + Game1.tileSize / 2) - Game1.viewport.Y) * Game1.options.zoomLevel),
                            Crop = dirt.crop is null ? null : ItemRegistry.GetDataOrErrorItem(dirt.crop.indexOfHarvest.Value).DisplayName,
                            Watered = watered,
                            ReadyToHarvest = ready,
                            Dead = dead
                        });
                    }
                    break;

                case Tree tree when IsNearby(x, y):
                    obstacles.Add((x, y, tree.stump.Value ? "Tree Stump" : "Tree", "Axe"));
                    break;

                case FruitTree when IsNearby(x, y):
                    obstacles.Add((x, y, "Fruit Tree", "Interact"));
                    break;

                case Grass when IsNearby(x, y):
                    obstacles.Add((x, y, "Grass", "Scythe"));
                    break;

                case Flooring:
                    break;

                default:
                    if (IsNearby(x, y))
                        obstacles.Add((x, y, entry.Value.GetType().Name, "Interact"));
                    break;
            }
        }

        foreach (ResourceClump clump in location.resourceClumps)
        {
            int x = (int)clump.Tile.X;
            int y = (int)clump.Tile.Y;
            if (!IsNearby(x, y))
                continue;
            (string name, string tool) = clump.parentSheetIndex.Value switch
            {
                ResourceClump.stumpIndex => ("Large Stump", "Axe"),
                ResourceClump.hollowLogIndex => ("Hollow Log", "Axe"),
                ResourceClump.boulderIndex => ("Boulder", "Pickaxe"),
                ResourceClump.meteoriteIndex => ("Meteorite", "Pickaxe"),
                ResourceClump.mineRock1Index or ResourceClump.mineRock2Index
                    or ResourceClump.mineRock3Index or ResourceClump.mineRock4Index => ("Large Rock", "Pickaxe"),
                _ => ("Resource Clump", "Interact")
            };
            obstacles.Add((x, y, $"{name} {clump.width.Value}x{clump.height.Value}", tool));
        }

        foreach (LargeTerrainFeature feature in location.largeTerrainFeatures)
        {
            int x = (int)feature.Tile.X;
            int y = (int)feature.Tile.Y;
            if (IsNearby(x, y))
                obstacles.Add((x, y, feature is Bush ? "Bush" : feature.GetType().Name, "Impassable"));
        }

        float zoom = Game1.options.zoomLevel;
        IReadOnlyList<BridgeWorldObject> nearbyObjects = obstacles
            .Select(entry => new BridgeWorldObject
            {
                X = entry.x,
                Y = entry.y,
                Name = entry.name,
                RecommendedTool = entry.tool,
                ScreenX = (int)((((entry.x * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.X) * zoom),
                ScreenY = (int)((((entry.y * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.Y) * zoom)
            })
            .OrderBy(entry => Math.Abs(entry.X - tile.X) + Math.Abs(entry.Y - tile.Y))
            .ThenBy(entry => entry.Y)
            .ThenBy(entry => entry.X)
            .Take(40)
            .ToArray();

        IReadOnlyList<BridgeNpc> npcsNearby = location.characters
            .Where(npc => IsNearby(npc.TilePoint.X, npc.TilePoint.Y))
            .Select(npc => new BridgeNpc
            {
                Name = npc.displayName ?? npc.Name,
                Kind = npc is Monster ? "Monster" : npc.IsVillager ? "Villager" : npc.GetType().Name,
                X = npc.TilePoint.X,
                Y = npc.TilePoint.Y
            })
            .Take(20)
            .ToArray();

        IReadOnlyList<BridgeShopItem> shopItems = CaptureShopItems();

        var inventoryCounts = new Dictionary<string, int>();
        foreach (BridgeInventoryItem item in inventory)
            inventoryCounts[item.Name] = inventoryCounts.GetValueOrDefault(item.Name) + item.Stack;

        const int navigationRadius = 12;
        int navigationOriginX = tile.X - navigationRadius;
        int navigationOriginY = tile.Y - navigationRadius;
        int mapWidth = location.Map.Layers[0].LayerWidth;
        int mapHeight = location.Map.Layers[0].LayerHeight;
        var navigationRows = new List<string>();
        var nearbyActions = new List<BridgeMapAction>();
        var tillable = new List<BridgeTile>();
        for (int y = navigationOriginY; y <= tile.Y + navigationRadius; y++)
        {
            var row = new StringBuilder();
            for (int x = navigationOriginX; x <= tile.X + navigationRadius; x++)
            {
                bool inBounds = x >= 0 && y >= 0 && x < mapWidth && y < mapHeight;
                bool passable = inBounds && IsTileWalkable(location, new Point(x, y));
                row.Append(passable ? '.' : '#');
                if (!inBounds)
                    continue;

                // makeHoeDirt's own rules: diggable back tile with no terrain feature or object.
                // IsTileWalkable adds the game's collision test, so characters, clumps, and
                // buildings are excluded and the tile is reachable. The player is ignored, so a
                // tile stays listed while it is being stood on.
                var tileVector = new Vector2(x, y);
                if (passable
                    && location.doesTileHaveProperty(x, y, "Diggable", "Back") is not null
                    && !location.terrainFeatures.ContainsKey(tileVector)
                    && !location.Objects.ContainsKey(tileVector))
                {
                    tillable.Add(new BridgeTile
                    {
                        X = x,
                        Y = y,
                        ScreenX = (int)((((x * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.X) * zoom),
                        ScreenY = (int)((((y * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.Y) * zoom)
                    });
                }

                foreach (string layer in new[] { "Buildings", "Back", "Front" })
                {
                    string? action = Game1.currentLocation.doesTileHaveProperty(x, y, "Action", layer);
                    if (!string.IsNullOrWhiteSpace(action))
                        nearbyActions.Add(new BridgeMapAction { X = x, Y = y, Kind = "Action", Value = action });
                    string? touchAction = Game1.currentLocation.doesTileHaveProperty(x, y, "TouchAction", layer);
                    if (!string.IsNullOrWhiteSpace(touchAction))
                        nearbyActions.Add(new BridgeMapAction { X = x, Y = y, Kind = "TouchAction", Value = touchAction });
                }
            }
            navigationRows.Add(row.ToString());
        }

        IReadOnlyList<BridgeTile> tillableNearby = tillable
            .OrderBy(entry => Math.Abs(entry.X - tile.X) + Math.Abs(entry.Y - tile.Y))
            .ThenBy(entry => entry.Y)
            .ThenBy(entry => entry.X)
            .Take(40)
            .ToArray();

        WateringCan? wateringCan = Game1.player.Items.OfType<WateringCan>().FirstOrDefault();
        BridgeTile? bedTile = null;
        if (location is FarmHouse farmHouse)
        {
            Point bed = farmHouse.GetPlayerBedSpot();
            bedTile = new BridgeTile
            {
                X = bed.X,
                Y = bed.Y,
                ScreenX = (int)((((bed.X * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.X) * zoom),
                ScreenY = (int)((((bed.Y * Game1.tileSize) + (Game1.tileSize / 2)) - Game1.viewport.Y) * zoom)
            };
        }

        return new GameStateSnapshot
        {
            WorldReady = true,
            GameActive = Game1.game1.IsActive,
            SimulationPaused = Game1.paused,
            PlayerFree = IsPlayerFreeStrict(),
            CanMove = Context.CanPlayerMove && !nightActive,
            NightActive = nightActive,
            SaveCount = this.saveCount,
            WorldMapVersion = this.worldMapVersion,
            GraphicsFullScreen = Game1.graphics.IsFullScreen,
            WindowedBorderless = Game1.options.windowedBorderlessFullscreen,
            ViewportWidth = Game1.graphics.GraphicsDevice.Viewport.Width,
            ViewportHeight = Game1.graphics.GraphicsDevice.Viewport.Height,
            Menu = menu,
            Location = Game1.currentLocation.NameOrUniqueName,
            TileX = tile.X,
            TileY = tile.Y,
            PixelX = (int)position.X,
            PixelY = (int)position.Y,
            Facing = Game1.player.FacingDirection,
            ToolbarIndex = Game1.player.CurrentToolIndex,
            Tool = Game1.player.CurrentTool?.Name ?? "none",
            UsingTool = Game1.player.UsingTool,
            Health = Game1.player.health,
            MaxHealth = Game1.player.maxHealth,
            Stamina = (int)Game1.player.Stamina,
            MaxStamina = (int)Game1.player.MaxStamina,
            Money = Game1.player.Money,
            Time = Game1.timeOfDay,
            Day = Game1.dayOfMonth,
            Season = Game1.currentSeason,
            Year = Game1.year,
            Weather = weather,
            QuestCount = Game1.player.questLog.Count,
            MailCount = Game1.mailbox.Count,
            CursorScreenX = (int)cursor.ScreenPixels.X,
            CursorScreenY = (int)cursor.ScreenPixels.Y,
            CursorWorldX = (int)cursor.AbsolutePixels.X,
            CursorWorldY = (int)cursor.AbsolutePixels.Y,
            CursorTileX = (int)cursor.Tile.X,
            CursorTileY = (int)cursor.Tile.Y,
            CursorGrabX = (int)cursor.GrabTile.X,
            CursorGrabY = (int)cursor.GrabTile.Y,
            EventUp = Game1.eventUp,
            Minigame = Game1.currentMinigame?.GetType().Name ?? "none",
            CursorItem = Game1.player.CursorSlotItem?.DisplayName,
            DialogueText = Game1.activeClickableMenu is DialogueBox activeDialogue ? activeDialogue.getCurrentString() : null,
            TilledTiles = tilledTiles,
            PlantedCrops = plantedCrops,
            WateredCrops = wateredCrops,
            HarvestableCrops = harvestableCrops,
            WateringCanWater = wateringCan?.WaterLeft,
            WateringCanMax = wateringCan?.waterCanMax,
            Inventory = inventory,
            InventoryCounts = inventoryCounts,
            Warps = warps,
            NearbyObjects = nearbyObjects,
            CropsNearby = cropsNearby,
            TillableNearby = tillableNearby,
            BedTile = bedTile,
            NpcsNearby = npcsNearby,
            ShopItems = shopItems,
            NearbyActions = nearbyActions,
            NavigationOriginX = navigationOriginX,
            NavigationOriginY = navigationOriginY,
            NavigationRows = navigationRows,
            DialogueResponses = dialogueResponses,
            Diagnostics = new BridgeDiagnostics
            {
                IsInBed = Game1.player.isInBed.Value,
                IsWarping = Game1.isWarping,
                FreezePause = Game1.player.freezePause,
                CanMoveRaw = Game1.player.CanMove,
                UsingTool = Game1.player.UsingTool,
                HasController = Game1.player.controller is not null,
                MovementDirections = Game1.player.movementDirections.ToArray(),
                IsMoving = Game1.player.isMoving(),
                PassedOut = Game1.player.passedOut,
                GamePaused = Game1.paused,
                FreezeControls = Game1.freezeControls,
                FadeToBlack = Game1.fadeToBlack,
                GlobalFade = Game1.globalFade,
                DialogueUp = Game1.dialogueUp,
                EventUp = Game1.eventUp,
                FarmEventActive = Game1.farmEvent is not null,
                LocationEventActive = Game1.currentLocation.currentEvent is not null,
                IsActive = Game1.game1.IsActive,
                ForegroundIsGame = GetForegroundWindow() == Game1.game1.Window.Handle,
                ActiveMenu = Game1.activeClickableMenu?.GetType().Name,
                NewDaySyncActive = CaptureNewDaySyncActive(),
                PressedKeys = Game1.GetKeyboardState().GetPressedKeys().Select(key => key.ToString()).ToArray()
            }
        };
    }

    private static bool? CaptureNewDaySyncActive()
    {
        var field = typeof(Game1).GetField(
            "newDaySync",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static
        );
        return field is null ? null : field.GetValue(null) is not null;
    }

    private static bool IsPlayerFreeStrict()
    {
        return Context.IsPlayerFree && !Game1.freezeControls && Game1.farmEvent is null
            && Game1.activeClickableMenu is not SaveGameMenu;
    }

    private static IReadOnlyList<BridgeShopItem> CaptureShopItems()
    {
        if (Game1.activeClickableMenu is not ShopMenu shop)
            return Array.Empty<BridgeShopItem>();

        var items = new List<BridgeShopItem>();
        for (int index = 0; index < shop.forSale.Count && items.Count < 40; index++)
        {
            ISalable salable = shop.forSale[index];
            ItemStockInformation? stock = shop.itemPriceAndStock.TryGetValue(salable, out ItemStockInformation? found) ? found : null;
            int price = stock?.Price ?? -1;
            int available = stock?.Stock is int count && count != int.MaxValue ? count : -1;
            int row = index - shop.currentItemIndex;
            int screenX = -1;
            int screenY = -1;
            if (row >= 0 && row < shop.forSaleButtons.Count)
            {
                Vector2 center = Utility.ModifyCoordinatesFromUIScale(
                    new Vector2(shop.forSaleButtons[row].bounds.Center.X, shop.forSaleButtons[row].bounds.Center.Y)
                );
                screenX = (int)center.X;
                screenY = (int)center.Y;
            }

            items.Add(new BridgeShopItem
            {
                Index = index,
                Name = salable.DisplayName,
                Price = price,
                Stock = available,
                ScreenX = screenX,
                ScreenY = screenY
            });
        }

        return items;
    }

    private static IReadOnlyList<BridgeDialogueResponse> CaptureDialogueResponses()
    {
        if (Game1.activeClickableMenu is not DialogueBox dialogueBox || !dialogueBox.isQuestion)
            return Array.Empty<BridgeDialogueResponse>();

        return dialogueBox.responses
            .Select((response, index) => new BridgeDialogueResponse
            {
                Index = index,
                Key = response.responseKey,
                Text = response.responseText,
                Selected = index == dialogueBox.selectedResponse
            })
            .ToArray();
    }

    private static string RecommendedToolFor(string name)
    {
        string normalized = name.ToLowerInvariant();
        if (normalized.Contains("weed") || normalized.Contains("grass"))
            return "Scythe";
        if (normalized.Contains("stone") || normalized.Contains("boulder"))
            return "Pickaxe";
        if (normalized.Contains("twig") || normalized.Contains("branch") || normalized.Contains("stump") || normalized.Contains("log"))
            return "Axe";
        return "Interact";
    }

    private bool IsOperationBusy()
    {
        return this.heldButtons.Length > 0
            || this.cursorPending
            || this.stateDelayTicks > 0
            || this.waitCondition is not null
            || this.pendingClickButton != SButton.None
            || this.physicalMouseButton != SButton.None
            || this.dragPending
            || this.dragActive
            || this.scrollTicksRemaining > 0
            || this.idleTicksRemaining > 0
            || this.navigationPath is not null
            || this.focusPending;
    }

    private void LogOperationBusy()
    {
        this.Monitor.Log(
            $"control_busy buttons={string.Join('+', this.heldButtons)} ticks_remaining={this.ticksRemaining} "
                + $"cursor_pending={this.cursorPending} snapshot_ticks={this.stateDelayTicks} "
                + $"wait_condition={this.waitDescription ?? "none"} wait_ticks_remaining={this.waitTicksRemaining} "
                + $"drag_pending={this.dragPending || this.dragActive} scroll_ticks_remaining={this.scrollTicksRemaining}",
            LogLevel.Warn
        );
    }

    private void ClearWait()
    {
        this.waitCondition = null;
        this.waitDescription = null;
        this.waitTimeoutTicks = 0;
        this.waitTicksRemaining = 0;
    }

    private void ScheduleState(string reason)
    {
        this.stateDelayTicks = 2;
        this.delayedStateReason = reason;
        this.delayedStatus = null;
    }

    private void ScheduleCompletion(string status, string reason)
    {
        this.stateDelayTicks = 2;
        this.delayedStateReason = reason;
        this.delayedStatus = status;
    }

    private static void ActivateGameWindow()
    {
        IntPtr handle = Game1.game1.Window.Handle;
        IntPtr foregroundHandle = GetForegroundWindow();
        uint foregroundThread = GetWindowThreadProcessId(foregroundHandle, out _);
        uint gameWindowThread = GetWindowThreadProcessId(handle, out _);
        bool attached = foregroundThread != 0
            && gameWindowThread != 0
            && foregroundThread != gameWindowThread
            && AttachThreadInput(gameWindowThread, foregroundThread, true);

        ShowWindowAsync(handle, 9);
        BringWindowToTop(handle);
        SetForegroundWindow(handle);
        SetFocus(handle);

        if (attached)
            AttachThreadInput(gameWindowThread, foregroundThread, false);
    }

    private void DisablePauseWhenOutOfFocus()
    {
        this.originalPauseWhenOutOfFocus ??= Game1.options.pauseWhenOutOfFocus;
        Game1.options.pauseWhenOutOfFocus = false;
    }

    private void RestorePauseWhenOutOfFocus()
    {
        if (this.originalPauseWhenOutOfFocus is not bool original)
            return;
        Game1.options.pauseWhenOutOfFocus = original;
        this.originalPauseWhenOutOfFocus = null;
    }

    private void PauseSimulation()
    {
        if (!Context.IsWorldReady)
            return;
        this.originalSimulationPaused ??= Game1.paused;
        Game1.paused = true;
        this.simulationOwned = true;
    }

    private void ResumeSimulation()
    {
        if (this.simulationOwned)
            Game1.paused = false;
    }

    private void RestoreSimulationPause()
    {
        if (this.originalSimulationPaused is bool original)
            Game1.paused = original;
        this.originalSimulationPaused = null;
        this.simulationOwned = false;
    }

    private static void SetPhysicalCursorPosition(int clientX, int clientY)
    {
        var point = new NativePoint { X = clientX, Y = clientY };
        ClientToScreen(Game1.game1.Window.Handle, ref point);
        SetCursorPos(point.X, point.Y);
    }

    private static void PressPhysicalMouseButton(SButton button)
    {
        MouseEvent(button == SButton.MouseLeft ? 0x0002u : 0x0008u, 0, 0, 0, UIntPtr.Zero);
    }

    private static void ReleasePhysicalMouseButton(SButton button)
    {
        MouseEvent(button == SButton.MouseLeft ? 0x0004u : 0x0010u, 0, 0, 0, UIntPtr.Zero);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr windowHandle, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint attachThread, uint attachToThread, bool attach);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr windowHandle);

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr windowHandle, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr windowHandle);

    [DllImport("user32.dll")]
    private static extern IntPtr SetFocus(IntPtr windowHandle);

    [DllImport("user32.dll")]
    private static extern bool ClientToScreen(IntPtr windowHandle, ref NativePoint point);

    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll", EntryPoint = "mouse_event")]
    private static extern void MouseEvent(uint flags, uint dx, uint dy, int data, UIntPtr extraInfo);

    private void LogBinding(string action, IEnumerable<InputButton> inputs)
    {
        string buttons = string.Join('+', inputs.Select(input => input.ToSButton()));
        this.Monitor.Log($"binding action={action} buttons={buttons}", LogLevel.Info);
    }

    private static bool TryParseButtons(string raw, out SButton[] buttons)
    {
        buttons = raw
            .Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(value => Enum.TryParse(value, ignoreCase: true, out SButton button) ? button : SButton.None)
            .Distinct()
            .ToArray();

        return buttons.Length > 0 && !buttons.Contains(SButton.None);
    }

    private static bool TryParseAgentButtons(IEnumerable<string> rawButtons, out SButton[] buttons)
    {
        buttons = rawButtons
            .Select(value => Enum.TryParse(value, ignoreCase: true, out SButton button) ? button : SButton.None)
            .Distinct()
            .ToArray();

        return buttons.Length > 0 && buttons.All(AgentButtons.Contains);
    }

    private static bool TryParsePointerButton(string raw, out SButton button)
    {
        button = raw.ToLowerInvariant() switch
        {
            "left" => SButton.MouseLeft,
            "right" => SButton.MouseRight,
            _ => SButton.None
        };
        return button != SButton.None;
    }

    private static bool TryCreateWaitCondition(string raw, out Func<bool> condition, out string description)
    {
        string[] parts = raw.Split('=', 2, StringSplitOptions.TrimEntries);
        string field = parts[0].ToLowerInvariant();
        string expected = parts.Length == 2 ? parts[1] : string.Empty;
        description = $"{field}={expected}";

        if (bool.TryParse(expected, out bool expectedBoolean))
        {
            condition = field switch
            {
                "world_ready" => () => Context.IsWorldReady == expectedBoolean,
                "game_active" => () => Game1.game1.IsActive == expectedBoolean,
                "player_free" => () => IsPlayerFreeStrict() == expectedBoolean,
                "can_move" => () => (Context.CanPlayerMove && !Game1.freezeControls && Game1.farmEvent is null
                    && Game1.activeClickableMenu is not SaveGameMenu) == expectedBoolean,
                "using_tool" => () => Context.IsWorldReady && Game1.player.UsingTool == expectedBoolean,
                "event_up" => () => Game1.eventUp == expectedBoolean,
                _ => null!
            };

            return condition is not null;
        }

        condition = field switch
        {
            "menu" when expected.Length > 0 => () => string.Equals(GetMenuState(), expected, StringComparison.OrdinalIgnoreCase),
            "location" when expected.Length > 0 => () => Context.IsWorldReady
                && string.Equals(Game1.currentLocation.NameOrUniqueName, expected, StringComparison.OrdinalIgnoreCase),
            "minigame" when expected.Length > 0 => () => string.Equals(
                Game1.currentMinigame?.GetType().Name ?? "none",
                expected,
                StringComparison.OrdinalIgnoreCase
            ),
            _ => null!
        };

        return condition is not null;
    }
}
