using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Quests;
using System.Security.Cryptography;
using System.Text;

namespace Autoplay.GameBridge;

public sealed partial class ModEntry
{
    private int navigationSegmentTicks;
    private HashSet<string> navigationNotices = new();

    private HashSet<string> NearbyNoticeKeys()
    {
        var here = Game1.player.TilePoint;
        var keys = Game1.currentLocation.characters
            .Where(npc => Math.Abs(npc.TilePoint.X - here.X) + Math.Abs(npc.TilePoint.Y - here.Y) <= 6)
            .Select(npc => "npc:" + npc.Name).ToHashSet();
        // Signs, boards and other map interactions are distinct from routine debris.
        for (int x = here.X - 3; x <= here.X + 3; x++)
        for (int y = here.Y - 3; y <= here.Y + 3; y++)
        {
            string? action = Game1.currentLocation.doesTileHaveProperty(x, y, "Action", "Buildings");
            if (!string.IsNullOrEmpty(action) && !action.StartsWith("Warp"))
                keys.Add($"action:{x},{y}:{action}");
        }
        return keys;
    }

    private string QuestRevision()
    {
        string content = string.Join("|", Game1.player.questLog.Where(q => !q.IsHidden())
            .Select(q => $"{q.id.Value}:{q.ShouldDisplayAsComplete()}:{q.GetDaysLeft()}:{string.Join(';', q.GetObjectiveDescriptions())}"));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(content)))[..16];
    }

    private IReadOnlyList<BridgeQuest> CaptureJournal()
    {
        if (Game1.activeClickableMenu is not QuestLog log)
            return Array.Empty<BridgeQuest>();
        var shown = this.helper.Reflection.GetField<IQuest?>(log, "_shownQuest").GetValue();
        int page = this.helper.Reflection.GetField<int>(log, "currentPage").GetValue();
        var quests = shown is not null ? new[] { shown } : this.helper.Reflection.GetMethod(log, "GetAllQuests").Invoke<IList<IQuest>>().Skip(page * 6).Take(6);
        return quests.Select(q => new BridgeQuest {
            Id = q is Quest quest ? quest.id.Value : q.GetName(),
            Title = q.GetName(),
            Description = shown is not null ? q.GetDescription() : "Open this journal entry for details.",
            Objectives = q.GetObjectiveDescriptions(),
            DaysLeft = q.IsTimedQuest() ? q.GetDaysLeft() : null,
            Reward = q.GetMoneyReward(),
            Complete = q.ShouldDisplayAsComplete()
        }).ToArray();
    }

    private IReadOnlyList<BridgeMenuEntry> CaptureLifeMenu()
    {
        var entries = new List<BridgeMenuEntry>();
        void Add(string label, ClickableComponent? component, bool? canAccept = null)
        {
            if (component is not null && component.visible && component.bounds.Width > 0)
                entries.Add(new BridgeMenuEntry { Label = label, ScreenX = component.bounds.Center.X, ScreenY = component.bounds.Center.Y, CanAccept = canAccept });
        }
        void Slots(InventoryMenu inventory, string verb, bool receiving = false)
        {
            int capacity = inventory.playerInventory ? Math.Min(Game1.player.MaxItems, inventory.capacity) : inventory.capacity;
            for (int i = 0; i < Math.Min(capacity, inventory.inventory.Count); i++)
            {
                var item = inventory.actualInventory.ElementAtOrDefault(i);
                Add($"{verb} slot {i}: {item?.DisplayName ?? "empty"}{(item is null ? "" : $" x{item.Stack}")}", inventory.inventory[i],
                    receiving && item is not null ? Game1.player.couldInventoryAcceptThisItem(item) : null);
            }
        }
        if (Game1.activeClickableMenu is GameMenu game)
        {
            foreach (var tab in game.tabs) Add("Tab: " + tab.name, tab);
            var page = game.GetCurrentPage();
            if (page is InventoryPage inventory)
            {
                Slots(inventory.inventory, "Inventory");
                Add("Organize inventory", inventory.organizeButton);
            }
            if (page is MapPage map)
                foreach (var point in map.points.Values) Add("Map: " + point.name, point);
            if (page is SkillsPage skills)
                foreach (var skill in skills.skillAreas) Add("Skill: " + skill.name, skill);
            if (page is CraftingPage crafting)
            {
                foreach (var recipe in crafting.pagesOfCraftingRecipes[crafting.currentCraftingPage])
                    Add("Craft: " + recipe.Value.DisplayName, recipe.Key);
                Add("Previous recipes", crafting.upButton); Add("Next recipes", crafting.downButton);
            }
            Add("Close menu", game.upperRightCloseButton);
        }
        if (Game1.activeClickableMenu is ItemGrabMenu grab)
        {
            if (!grab.shippingBin) Slots(grab.ItemsToGrabMenu, "Take", receiving: true);
            Slots(grab.inventory, grab.shippingBin ? "Ship" : "Store");
            Add("Fill existing stacks", grab.fillStacksButton);
            Add("Close items", grab.okButton);
        }
        if (Game1.activeClickableMenu is QuestLog log)
        {
            var shown = this.helper.Reflection.GetField<IQuest?>(log, "_shownQuest").GetValue();
            if (shown is null)
            {
                var quests = this.CaptureJournal();
                for (int i = 0; i < Math.Min(log.questLogButtons.Count, quests.Count); i++)
                    Add(quests[i].Title, log.questLogButtons[i]);
            }
            else if (shown.ShouldDisplayAsComplete() && shown.HasMoneyReward()) Add("Collect quest reward", log.rewardBox);
            int page = this.helper.Reflection.GetField<int>(log, "currentPage").GetValue();
            if (shown is not null || page > 0) Add("Previous page / back", log.backButton);
            if (shown is null && this.helper.Reflection.GetMethod(log, "GetAllQuests").Invoke<IList<IQuest>>().Count > (page + 1) * 6)
                Add("Next page", log.forwardButton);
            Add("Close journal", log.upperRightCloseButton);
        }
        else if (Game1.activeClickableMenu is LetterViewerMenu letter)
        {
            if (letter.HasQuestOrSpecialOrder) Add("Accept request", letter.acceptQuestButton);
            foreach (var item in letter.itemsToGrab) if (item.item is not null) Add("Take " + item.item.DisplayName, item, Game1.player.couldInventoryAcceptThisItem(item.item));
            if (letter.page > 0) Add("Previous letter page", letter.backButton);
            if (letter.page + 1 < letter.mailMessage.Count) Add("Next letter page", letter.forwardButton);
            Add("Close letter", letter.upperRightCloseButton);
        }
        return entries;
    }
}
