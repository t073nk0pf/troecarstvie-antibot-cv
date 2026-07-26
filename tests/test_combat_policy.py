from src.antibot_cv.automation.combat_policy import (
    AvailableSkill,
    BattleItem,
    BattleItemKind,
    BattleResources,
    BattleSnapshot,
    CombatIntent,
    CombatPolicy,
)


def snap(**overrides):
    values = dict(
        active=True,
        finished=False,
        turn=1,
        resources=BattleResources(100, 100, 100, 100),
        skills=(AvailableSkill("heavy", 2, 20, True, 0), AvailableSkill("light", 1, 5, True, 0)),
        items=(),
    )
    values.update(overrides)
    return BattleSnapshot(**values)


def policy():
    return CombatPolicy(
        skill_name_allowlist=("heavy", "light"),
        item_name_allowlist={BattleItemKind.HEALTH: ("health",), BattleItemKind.PROWESS: ("prowess",), BattleItemKind.DAMAGE_BOOST: ("boost",)},
    )


def test_strongest_ready_allowlisted_skill_is_one_next_action():
    result = policy().decide(snap())
    assert (result.intent, result.skill.name) == (CombatIntent.USE_SKILL, "heavy")


def test_recovery_item_precedes_a_strike_even_when_the_strike_is_not_ready():
    result = policy().decide(snap(skills=(AvailableSkill("heavy", 2, 20, True, 2),), resources=BattleResources(10, 100, 100, 100), items=(BattleItem("health", 4, "health", 2, True, 0),)))
    assert (result.intent, result.item.name) == (CombatIntent.USE_ITEM, "health")


def test_nonready_allowlisted_strike_is_submitted_without_waiting_for_bridge_confirmation():
    result = policy().decide(
        snap(skills=(AvailableSkill("heavy", 2, 20, ready=False, cooldown=2),))
    )
    assert (result.intent, result.skill.name) == (CombatIntent.USE_SKILL, "heavy")


def test_threshold_item_precedes_ready_skill_and_only_one_item_is_selected():
    result = policy().decide(
        snap(
            resources=BattleResources(10, 100, 10, 100),
            items=(
                BattleItem("health", 4, "health", 2, True, 0),
                BattleItem("prowess", 5, "prowess", 2, True, 0),
            ),
        )
    )
    assert result.intent is CombatIntent.USE_ITEM
    assert result.item.name == "health"


def test_unknown_item_never_used_and_burdjuk_is_not_out_of_battle_recovery():
    result = policy().decide(snap(skills=(), items=(BattleItem("unknown", 4, "health", 9, True, 0),)))
    assert result.intent is CombatIntent.WAIT


def test_zero_prowess_skill_slot_zero_requires_separate_flag():
    skill = AvailableSkill("zero", 0, 0, True, 0)
    low = snap(skills=(skill,), resources=BattleResources(100, 100, 0, 100))
    assert policy().decide(low).intent is CombatIntent.WAIT
    enabled = CombatPolicy(skill_slot_allowlist=(0,), allow_zero_prowess_slot=True)
    assert enabled.decide(low).intent is CombatIntent.USE_SKILL


def test_low_hp_does_not_stop_started_battle_and_finished_exits():
    assert policy().decide(snap(resources=BattleResources(1, 100, 100, 100))).intent is CombatIntent.USE_SKILL
    assert policy().decide(snap(finished=True)).intent is CombatIntent.EXIT


def test_repeat_limit_waits_and_unknown_state_stops_unsafe():
    assert policy().decide(snap(repeated_action_count=3)).intent is CombatIntent.WAIT
    assert policy().decide(snap(active=None)).intent is CombatIntent.STOP_UNSAFE


def test_malformed_typed_observation_fails_closed():
    assert policy().decide(snap(resources=BattleResources(True, 100, 100, 100))).intent is CombatIntent.STOP_UNSAFE


def test_empty_allowlists_never_authorize_unknown_skill_or_item() -> None:
    open_policy = CombatPolicy()
    observed = snap(
        skills=(AvailableSkill("unknown", 2, 100, True, 0),),
        resources=BattleResources(1, 100, 100, 100),
        items=(BattleItem("unknown potion", 4, "health", 2, True, 0),),
    )
    assert open_policy.decide(observed).intent is CombatIntent.WAIT


def test_explicit_damage_boost_is_used_once_before_strike() -> None:
    boost = BattleItem("boost", 6, BattleItemKind.DAMAGE_BOOST, 1, True, 0)
    enabled = CombatPolicy(
        skill_name_allowlist=("heavy",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
    )
    first = enabled.decide(snap(items=(boost,), damage_boost_active=False))
    assert first.intent is CombatIntent.USE_ITEM
    assert first.item.name == "boost"
    assert enabled.decide(snap(items=(boost,), damage_boost_active=True)).intent is CombatIntent.USE_SKILL


def test_damage_boost_prefixes_a_nonzero_damage_skill_without_ready_confirmation() -> None:
    boost = BattleItem("boost", 6, BattleItemKind.DAMAGE_BOOST, 1, True, 0)
    policy = CombatPolicy(
        skill_name_allowlist=("setup",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
    )
    snapshot = snap(
        skills=(AvailableSkill("setup", 1, damage=0, ready=True, cooldown=0),),
        items=(boost,),
        damage_boost_active=False,
    )
    assert policy.decide(snapshot).intent is CombatIntent.USE_SKILL

    strike = snap(
        skills=(AvailableSkill("strike", 1, damage=10, ready=False, cooldown=2),),
        items=(boost,),
        damage_boost_active=False,
    )
    strike_policy = CombatPolicy(
        skill_name_allowlist=("strike",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
    )
    assert strike_policy.decide(strike).intent is CombatIntent.USE_ITEM


def test_damage_boost_chance_is_evaluated_once_for_the_selected_strike() -> None:
    boost = BattleItem("boost", 6, BattleItemKind.DAMAGE_BOOST, 1, True, 0)
    strike = AvailableSkill("strike", 1, damage=10, ready=False, cooldown=2)

    never = CombatPolicy(
        skill_name_allowlist=("strike",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
        damage_boost_use_chance_percent=0,
    )
    assert never.decide(snap(skills=(strike,), items=(boost,), damage_boost_active=False, damage_boost_roll=0)).intent is CombatIntent.USE_SKILL

    half = CombatPolicy(
        skill_name_allowlist=("strike",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
        damage_boost_use_chance_percent=50,
    )
    assert half.decide(snap(skills=(strike,), items=(boost,), damage_boost_active=False, damage_boost_roll=0.49)).intent is CombatIntent.USE_ITEM
    assert half.decide(snap(skills=(strike,), items=(boost,), damage_boost_active=False, damage_boost_roll=0.50)).intent is CombatIntent.USE_SKILL

    always = CombatPolicy(
        skill_name_allowlist=("strike",),
        item_name_allowlist={BattleItemKind.DAMAGE_BOOST: ("boost",)},
        damage_boost_enabled=True,
        damage_boost_use_chance_percent=100,
    )
    assert always.decide(snap(skills=(strike,), items=(boost,), damage_boost_active=False, damage_boost_roll=0.999)).intent is CombatIntent.USE_ITEM
