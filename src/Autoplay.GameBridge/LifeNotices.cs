using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Quests;

namespace Autoplay.GameBridge;

public sealed partial class ModEntry
{
    private readonly List<BridgeNotice> lifeNotices = new();
    private HashSet<string> visibleLifeText = new();
    private HashSet<string> ownedTools = new();
    private string noticeSession = Guid.NewGuid().ToString("N");
    private int noticeSequence;

    private void ResetLifeNotices()
    {
        this.lifeNotices.Clear();
        this.visibleLifeText.Clear();
        this.ownedTools = Game1.player.Items.OfType<Tool>().Select(item => item.QualifiedItemId).ToHashSet();
        this.noticeSession = Guid.NewGuid().ToString("N");
        this.noticeSequence = 0;
    }

    private void AddLifeNotice(string kind, string text)
    {
        this.lifeNotices.Add(new BridgeNotice {
            Id = $"{this.noticeSession}:{++this.noticeSequence}", Kind = kind, Text = text,
            Location = Game1.currentLocation.NameOrUniqueName, Day = Game1.dayOfMonth,
            Season = Game1.currentSeason, Year = Game1.year, Time = Game1.timeOfDay
        });
        if (this.lifeNotices.Count > 128) this.lifeNotices.RemoveAt(0);
    }

    // Sample while the game updates, including while Python is waiting on a model.
    // Only text already presented to the player is retained; never future event commands.
    private void CaptureLifeNotices()
    {
        if (!Context.IsWorldReady) return;
        var visible = new HashSet<string>();
        void Text(string kind, string? text)
        {
            if (string.IsNullOrWhiteSpace(text)) return;
            string key = kind + ":" + text;
            visible.Add(key);
            if (!this.visibleLifeText.Contains(key)) this.AddLifeNotice(kind, text);
        }
        foreach (var message in Game1.hudMessages) Text("hud", message.message);
        if (Game1.activeClickableMenu is DialogueBox dialogue)
            Text("dialogue", dialogue.getCurrentString());
        if (Game1.activeClickableMenu is LetterViewerMenu letter)
            Text("letter", letter.mailMessage.ElementAtOrDefault(letter.page));
        if (Game1.activeClickableMenu is LevelUpMenu)
            foreach (string text in this.CaptureMenuText()) Text("level_up", text);
        foreach (Tool tool in Game1.player.Items.OfType<Tool>())
            if (this.ownedTools.Add(tool.QualifiedItemId)) this.AddLifeNotice("new_tool", tool.DisplayName);
        this.visibleLifeText = visible;
    }

    private IReadOnlyList<BridgeQuest> CaptureQuestSummaries() => Game1.player.questLog
        .Where(q => !q.IsHidden()).Select(q => new BridgeQuest {
            Id = q.id.Value, Title = q.GetName(), Description = q.GetDescription(),
            Objectives = q.GetObjectiveDescriptions(), DaysLeft = q.IsTimedQuest() ? q.GetDaysLeft() : null,
            Reward = q.GetMoneyReward(), Complete = q.ShouldDisplayAsComplete()
        }).ToArray();

    private IReadOnlyList<string> CaptureMenuText()
    {
        var text = new List<string>();
        void Add(string? value) { if (!string.IsNullOrWhiteSpace(value)) text.Add(value); }
        var menu = Game1.activeClickableMenu;
        if (menu is GameMenu game)
        {
            Add("Tabs: " + string.Join(", ", game.tabs.Select(tab => tab.name)));
            Add(game.hoverText);
            menu = game.GetCurrentPage();
        }
        if (menu is InventoryPage inventory)
        {
            Add($"Health {Game1.player.health}/{Game1.player.maxHealth}; energy {(int)Game1.player.Stamina}/{Game1.player.MaxStamina}; money {Game1.player.Money}g.");
            Add(inventory.hoverTitle); Add(inventory.hoverText);
            if (inventory.hoveredItem is not null) Add(inventory.hoveredItem.getDescription());
        }
        if (menu is SkillsPage)
        {
            foreach (var skill in new[] { ("Farming", 0), ("Fishing", 1), ("Foraging", 2), ("Mining", 3), ("Combat", 4) })
                Add($"{skill.Item1}: level {Game1.player.getEffectiveSkillLevel(skill.Item2)}");
            Add(this.helper.Reflection.GetField<string>(menu, "hoverTitle").GetValue());
            Add(this.helper.Reflection.GetField<string>(menu, "hoverText").GetValue());
        }
        if (menu is MapPage map)
        {
            Add(map.scrollText); Add(map.hoverText);
            foreach (var point in map.points.Values.Where(p => p.visible)) Add(point.name);
        }
        if (menu is SocialPage social)
            foreach (var entry in social.SocialEntries.Skip(social.slotPosition).Take(5))
                Add(entry.IsMet ? $"{entry.DisplayName}: {entry.HeartLevel} hearts; talked today: {entry.Friendship?.TalkedToToday}" : "??? (not yet met)");
        if (menu is CraftingPage crafting)
        {
            Add(crafting.hoverText);
            if (crafting.hoverItem is not null) Add(crafting.hoverItem.getDescription());
        }
        if (menu is MenuWithInventory withInventory && withInventory.heldItem is not null)
            Add($"Holding on cursor: {withInventory.heldItem.DisplayName} x{withInventory.heldItem.Stack}; put it in an appropriate slot before closing.");
        if (menu is ItemGrabMenu grab) Add(grab.message);
        if (menu is LetterViewerMenu letter)
        {
            Add(letter.mailMessage.ElementAtOrDefault(letter.page));
            if (letter.moneyIncluded > 0) Add($"Money enclosed: {letter.moneyIncluded}g");
            if (!string.IsNullOrEmpty(letter.learnedRecipe)) Add("Recipe learned: " + letter.learnedRecipe);
        }
        if (menu is LevelUpMenu level)
        {
            Add(this.helper.Reflection.GetField<string>(level, "title").GetValue());
            foreach (string field in new[] { "extraInfoForLevel", "leftProfessionDescription", "rightProfessionDescription" })
                foreach (string line in this.helper.Reflection.GetField<List<string>>(level, field).GetValue() ?? new List<string>()) Add(line);
        }
        return text.Distinct().ToArray();
    }
}
