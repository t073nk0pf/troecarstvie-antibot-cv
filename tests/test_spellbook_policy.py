from src.antibot_cv.automation.spellbook_policy import (
    CURRENT_V3G45_SPELLBOOK,
    CooldownBasis,
    SkillReadiness,
    SpellbookEvidence,
    SpellbookIntent,
    SpellbookPolicy,
    TurnEffect,
)


def evidence(*, ready=None, **updates):
    ready = ready or {slot: (True, 0) for slot in range(1, 6)}
    values = dict(
        my_turn=True,
        prowess=100,
        skills=tuple(
            SkillReadiness(skill.slot, skill.name, *ready[skill.slot])
            for skill in CURRENT_V3G45_SPELLBOOK.skills
        ),
        worthwhile_setup_slots=frozenset({1, 4}),
        defensive_needed=True,
    )
    values.update(updates)
    return SpellbookEvidence(**values)


def test_current_profile_encodes_observed_turn_and_cooldown_semantics() -> None:
    skills = {skill.slot: skill for skill in CURRENT_V3G45_SPELLBOOK.skills}

    assert skills[1].turn_effect is TurnEffect.INSTANT
    assert skills[1].cooldown_basis is CooldownBasis.SECONDS
    assert skills[2].turn_effect is TurnEffect.REQUIRES_TURN_CONTINUES
    assert skills[2].cooldown == 12
    assert skills[3].turn_effect is TurnEffect.CONSUMES_TURN
    assert skills[4].turn_effect is TurnEffect.INSTANT
    assert skills[5].expected_damage == 8.5


def test_ready_plan_orders_instants_defense_then_strongest_attack() -> None:
    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(evidence())

    assert decision.intent is SpellbookIntent.PLAN
    assert [skill.slot for skill in decision.setup_actions] == [1, 4, 2]
    assert decision.turn_action is not None and decision.turn_action.slot == 3


def test_slot_five_falls_back_only_when_stronger_slot_three_is_explicitly_cooling() -> None:
    ready = {slot: (False, 1) for slot in range(1, 6)}
    ready[3] = (False, 2)
    ready[5] = (True, 0)

    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(
        evidence(ready=ready, worthwhile_setup_slots=frozenset(), defensive_needed=False)
    )

    assert decision.intent is SpellbookIntent.PLAN
    assert decision.turn_action is not None and decision.turn_action.slot == 5


def test_unknown_readiness_waits_without_actions() -> None:
    ready = {slot: (False, 1) for slot in range(1, 6)}
    ready[2] = (None, None)

    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(evidence(ready=ready))

    assert decision.intent is SpellbookIntent.WAIT
    assert decision.setup_actions == ()
    assert decision.turn_action is None


def test_defensive_slot_requires_explicit_condition_and_player_turn() -> None:
    ready = {slot: (False, 1) for slot in range(1, 6)}
    ready[2] = (True, 0)
    policy = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK)

    assert policy.decide(evidence(ready=ready, defensive_needed=False)).setup_actions == ()
    assert policy.decide(evidence(ready=ready, my_turn=False)).setup_actions == ()


def test_slot_zero_requires_explicit_low_prowess_fallback() -> None:
    ready = {slot: (None, None) for slot in range(1, 6)}
    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(
        evidence(ready=ready, prowess=0, low_prowess_fallback=True)
    )

    assert decision.intent is SpellbookIntent.PLAN
    assert decision.fallback_slot == 0
    assert decision.setup_actions == ()
    assert decision.turn_action is None


def test_identity_mismatch_stops_unsafe() -> None:
    skills = list(evidence().skills)
    skills[1] = SkillReadiness(2, "Другая способность", True, 0)

    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(
        evidence(skills=tuple(skills))
    )

    assert decision.intent is SpellbookIntent.STOP_UNSAFE
    assert decision.reason == "spellbook_identity_mismatch"


def test_non_boolean_low_prowess_fallback_stops_unsafe() -> None:
    for invalid in ("false", 1):
        decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(
            evidence(low_prowess_fallback=invalid)  # type: ignore[arg-type]
        )

        assert decision.intent is SpellbookIntent.STOP_UNSAFE
        assert decision.reason == "invalid_low_prowess_fallback"
