from src.antibot_cv.automation.leveling_policy import (
    Death,
    Inventory,
    LevelingIntent,
    LevelingPolicy,
    Location,
    ObservedPlayer,
    Quests,
)


def snapshot(**overrides):
    values = {
        "player": ObservedPlayer("hero", 2, 100, 100, 100, 100, True),
        "location": Location("hunt", True, True, True),
        "death": Death(0, False),
        "quests": Quests(False),
        "inventory": Inventory({"red_potion": 1, "blue_potion": 1}),
    }
    values.update(overrides)
    return values


def policy():
    return LevelingPolicy("hero", 10, 3, hp_item_allowlist=("red_potion",), prowess_item_allowlist=("blue_potion",))


def test_goal_character_and_target_are_fail_closed():
    assert policy().decide(**snapshot(player=ObservedPlayer("other", 2, 100, 100, 100, 100, True))).intent is LevelingIntent.STOP_UNSAFE
    assert policy().decide(**snapshot(player=ObservedPlayer("hero", 10, 100, 100, 100, 100, True))).intent is LevelingIntent.STOP_GOAL


def test_death_allows_only_free_revive():
    dead = ObservedPlayer("hero", 2, 0, 100, 100, 100, False)
    assert policy().decide(**snapshot(player=dead, death=Death(0, True))).intent is LevelingIntent.REVIVE_FREE
    assert policy().decide(**snapshot(player=dead, death=Death(0, False))).intent is LevelingIntent.STOP_UNSAFE
    missing_resources = ObservedPlayer("hero", 2, None, None, None, None, False)
    assert policy().decide(**snapshot(player=missing_resources, death=Death(0, True))).intent is LevelingIntent.REVIVE_FREE


def test_resource_thresholds_use_allowlisted_items_then_rest():
    low_hp = ObservedPlayer("hero", 2, 20, 100, 100, 100, True)
    result = policy().decide(**snapshot(player=low_hp))
    assert (result.intent, result.item) == (LevelingIntent.RECOVER_HP_ITEM, "red_potion")
    result = policy().decide(**snapshot(player=low_hp, inventory=Inventory({"unknown_potion": 99})))
    assert result.intent is LevelingIntent.REST


def test_hunt_progression_is_deterministic():
    assert policy().decide(**snapshot(location=Location("town", True, False, False))).intent is LevelingIntent.OPEN_HUNT
    assert policy().decide(**snapshot(location=Location("hunt", True, True, True, False))).intent is LevelingIntent.WAIT
    assert policy().decide(**snapshot()).intent is LevelingIntent.FARM


def test_stale_unknown_and_death_limit_stop_without_live_action():
    assert policy().decide(**snapshot(location=Location("hunt", True, True, True, stale=True))).intent is LevelingIntent.STOP_UNSAFE
    assert policy().decide(**snapshot(player=ObservedPlayer("hero", None, 100, 100, 100, 100, True))).intent is LevelingIntent.STOP_UNSAFE
    assert policy().decide(**snapshot(death=Death(3, True))).intent is LevelingIntent.STOP_UNSAFE
    assert policy().decide(**snapshot(location=Location("danger", False, True, True))).intent is LevelingIntent.STOP_UNSAFE
