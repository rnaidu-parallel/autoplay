using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Autoplay.GameBridge;

internal sealed class BridgeRequest
{
    public string Id { get; set; } = string.Empty;
    public string Type { get; set; } = string.Empty;
    public string[] Buttons { get; set; } = Array.Empty<string>();
    public int Ticks { get; set; }
    public int X { get; set; }
    public int Y { get; set; }
    public int StartX { get; set; }
    public int StartY { get; set; }
    public int EndX { get; set; }
    public int EndY { get; set; }
    public string Button { get; set; } = string.Empty;
    public string Direction { get; set; } = string.Empty;
    public int Steps { get; set; }
    public int Index { get; set; }
    public string Field { get; set; } = string.Empty;
    public string Value { get; set; } = string.Empty;
    public string Mode { get; set; } = string.Empty;
    public string Location { get; set; } = string.Empty;
}

internal sealed class BridgeResponse
{
    public string Id { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public string? Error { get; init; }
    public string? Reason { get; init; }
    public GameStateSnapshot? State { get; init; }
}

internal sealed class GameStateSnapshot
{
    public bool WorldReady { get; init; }
    public bool GameActive { get; init; }
    public bool SimulationPaused { get; init; }
    public bool PlayerFree { get; init; }
    public bool CanMove { get; init; }
    public bool GraphicsFullScreen { get; init; }
    public bool WindowedBorderless { get; init; }
    public int ViewportWidth { get; init; }
    public int ViewportHeight { get; init; }
    public string Menu { get; init; } = string.Empty;
    public string? Location { get; init; }
    public int? TileX { get; init; }
    public int? TileY { get; init; }
    public int? PixelX { get; init; }
    public int? PixelY { get; init; }
    public int? Facing { get; init; }
    public int? ToolbarIndex { get; init; }
    public string? Tool { get; init; }
    public bool? UsingTool { get; init; }
    public int? Health { get; init; }
    public int? MaxHealth { get; init; }
    public int? Stamina { get; init; }
    public int? MaxStamina { get; init; }
    public int? Money { get; init; }
    public int? Time { get; init; }
    public int? Day { get; init; }
    public string? Season { get; init; }
    public int? Year { get; init; }
    public string? Weather { get; init; }
    public int? QuestCount { get; init; }
    public int? MailCount { get; init; }
    public int CursorScreenX { get; init; }
    public int CursorScreenY { get; init; }
    public int CursorWorldX { get; init; }
    public int CursorWorldY { get; init; }
    public int CursorTileX { get; init; }
    public int CursorTileY { get; init; }
    public int CursorGrabX { get; init; }
    public int CursorGrabY { get; init; }
    public bool EventUp { get; init; }
    public string Minigame { get; init; } = string.Empty;
    public string? CursorItem { get; init; }
    public string? DialogueText { get; init; }
    public int? TilledTiles { get; init; }
    public int? PlantedCrops { get; init; }
    public int? WateredCrops { get; init; }
    public int? HarvestableCrops { get; init; }
    public int? WateringCanWater { get; init; }
    public int? WateringCanMax { get; init; }
    public IReadOnlyList<BridgeInventoryItem> Inventory { get; init; } = Array.Empty<BridgeInventoryItem>();
    public IReadOnlyDictionary<string, int> InventoryCounts { get; init; } = new Dictionary<string, int>();
    public IReadOnlyList<BridgeWarp> Warps { get; init; } = Array.Empty<BridgeWarp>();
    public IReadOnlyList<BridgeWorldObject> NearbyObjects { get; init; } = Array.Empty<BridgeWorldObject>();
    public IReadOnlyList<BridgeCrop> CropsNearby { get; init; } = Array.Empty<BridgeCrop>();
    public IReadOnlyList<BridgeTile> TillableNearby { get; init; } = Array.Empty<BridgeTile>();
    public BridgeTile? BedTile { get; init; }
    public IReadOnlyList<BridgeNpc> NpcsNearby { get; init; } = Array.Empty<BridgeNpc>();
    public IReadOnlyList<BridgeShopItem> ShopItems { get; init; } = Array.Empty<BridgeShopItem>();
    public IReadOnlyList<BridgeMapAction> NearbyActions { get; init; } = Array.Empty<BridgeMapAction>();
    public int? NavigationOriginX { get; init; }
    public int? NavigationOriginY { get; init; }
    public IReadOnlyList<string> NavigationRows { get; init; } = Array.Empty<string>();
    public IReadOnlyList<BridgeDialogueResponse> DialogueResponses { get; init; } = Array.Empty<BridgeDialogueResponse>();
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public BridgeDiagnostics? Diagnostics { get; init; }
}

internal sealed class BridgeDiagnostics
{
    public bool IsInBed { get; init; }
    public int FreezePause { get; init; }
    public bool CanMoveRaw { get; init; }
    public bool UsingTool { get; init; }
    public bool HasController { get; init; }
    public IReadOnlyList<int> MovementDirections { get; init; } = Array.Empty<int>();
    public bool IsMoving { get; init; }
    public bool PassedOut { get; init; }
    public bool GamePaused { get; init; }
    public bool FreezeControls { get; init; }
    public bool FadeToBlack { get; init; }
    public bool GlobalFade { get; init; }
    public bool DialogueUp { get; init; }
    public bool EventUp { get; init; }
    public bool FarmEventActive { get; init; }
    public bool LocationEventActive { get; init; }
    public bool IsActive { get; init; }
    public bool ForegroundIsGame { get; init; }
    public string? ActiveMenu { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? NewDaySyncActive { get; init; }
    public IReadOnlyList<string> PressedKeys { get; init; } = Array.Empty<string>();
}

internal sealed class BridgeCrop
{
    public int X { get; init; }
    public int Y { get; init; }
    public int ScreenX { get; init; }
    public int ScreenY { get; init; }
    public string? Crop { get; init; }
    public bool Watered { get; init; }
    public bool ReadyToHarvest { get; init; }
    public bool Dead { get; init; }
}

internal sealed class BridgeTile
{
    public int X { get; init; }
    public int Y { get; init; }
    public int ScreenX { get; init; }
    public int ScreenY { get; init; }
}

internal sealed class BridgeNpc
{
    public string Name { get; init; } = string.Empty;
    public string Kind { get; init; } = string.Empty;
    public int X { get; init; }
    public int Y { get; init; }
}

internal sealed class BridgeShopItem
{
    public int Index { get; init; }
    public string Name { get; init; } = string.Empty;
    public int Price { get; init; }
    public int Stock { get; init; }
    public int ScreenX { get; init; }
    public int ScreenY { get; init; }
}

internal sealed class BridgeInventoryItem
{
    public bool IsSeed { get; init; }
    public int Slot { get; init; }
    public string QualifiedId { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
    public int Stack { get; init; }
    public int Quality { get; init; }
}

internal sealed class BridgeWarp
{
    public int X { get; init; }
    public int Y { get; init; }
    public string TargetName { get; init; } = string.Empty;
    public int TargetX { get; init; }
    public int TargetY { get; init; }
}

internal sealed class BridgeWorldObject
{
    public int X { get; init; }
    public int Y { get; init; }
    public string Name { get; init; } = string.Empty;
    public string RecommendedTool { get; init; } = string.Empty;
    public int ScreenX { get; init; }
    public int ScreenY { get; init; }
}

internal sealed class BridgeMapAction
{
    public int X { get; init; }
    public int Y { get; init; }
    public string Kind { get; init; } = string.Empty;
    public string Value { get; init; } = string.Empty;
}

internal sealed class BridgeDialogueResponse
{
    public int Index { get; init; }
    public string Key { get; init; } = string.Empty;
    public string Text { get; init; } = string.Empty;
    public bool Selected { get; init; }
}

internal sealed class BridgeRequestEnvelope
{
    public BridgeRequest Request { get; init; } = null!;
    public TaskCompletionSource<BridgeResponse> Completion { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
}

internal sealed class BridgePipeServer
{
    public const string PipeName = "autoplay-game-bridge";

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true
    };

    private readonly ConcurrentQueue<BridgeRequestEnvelope> requests = new();
    private readonly CancellationTokenSource cancellation = new();

    public void Start()
    {
        _ = Task.Run(this.RunAsync);
    }

    public bool TryDequeue(out BridgeRequestEnvelope? request)
    {
        return this.requests.TryDequeue(out request);
    }

    private async Task RunAsync()
    {
        while (!this.cancellation.IsCancellationRequested)
        {
            try
            {
                await using var pipe = new NamedPipeServerStream(
                    PipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous
                );
                await pipe.WaitForConnectionAsync(this.cancellation.Token);
                using var reader = new StreamReader(pipe, leaveOpen: true);
                using var writer = new StreamWriter(pipe, leaveOpen: true) { AutoFlush = true };

                while (pipe.IsConnected && !this.cancellation.IsCancellationRequested)
                {
                    string? line = await reader.ReadLineAsync();
                    if (line is null)
                        break;

                    BridgeRequest? request = JsonSerializer.Deserialize<BridgeRequest>(line, JsonOptions);
                    if (request is null || string.IsNullOrWhiteSpace(request.Id) || string.IsNullOrWhiteSpace(request.Type))
                    {
                        await writer.WriteLineAsync(JsonSerializer.Serialize(new BridgeResponse
                        {
                            Id = request?.Id ?? string.Empty,
                            Status = "error",
                            Error = "invalid_request"
                        }, JsonOptions));
                        continue;
                    }

                    var envelope = new BridgeRequestEnvelope { Request = request };
                    this.requests.Enqueue(envelope);
                    BridgeResponse response = await envelope.Completion.Task.WaitAsync(this.cancellation.Token);
                    await writer.WriteLineAsync(JsonSerializer.Serialize(response, JsonOptions));
                }
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (IOException)
            {
                // The client disconnected. Create a fresh pipe and wait for the harness to reconnect.
            }
            catch (JsonException)
            {
                // Invalid JSON ends this connection so the next client starts from a clean boundary.
            }
        }
    }
}
