from src.antibot_cv.automation.combat_policy import CombatIntent
from src.antibot_cv.automation.spellbook_combat_adapter import (
    SpellbookBattleState,
    decide_current_spellbook_action,
)
from src.antibot_cv.automation.spellbook_policy import CURRENT_V3G45_SPELLBOOK


def snapshot(*, my_turn=True, authoritative=True, readiness=None):
    readiness = readiness or {slot: (True, 0) for slot in range(1, 6)}
    return {
        "turnEvidence": {"authoritative": authoritative, "myTurn": my_turn},
        "abilities": [
            {
                "id": -1000 - skill.slot,
                "slot": skill.slot,
                "name": skill.name,
                "readinessEvidence": {
                    "authoritative": readiness[skill.slot][0] is not None,
                    "ready": readiness[skill.slot][0],
                    "cooldownRemaining": readiness[skill.slot][1],
                },
            }
            for skill in CURRENT_V3G45_SPELLBOOK.skills
        ],
    }


def test_current_spellbook_waits_when_bridge_readiness_is_unknown() -> None:
    readiness = {slot: (True, 0) for slot in range(1, 6)}
    readiness[2] = (None, None)

    adapted = decide_current_spellbook_action(snapshot(readiness=readiness), prowess=100)

    assert adapted.matched is True
    assert adapted.decision is not None
    assert adapted.decision.intent is CombatIntent.WAIT
    assert adapted.decision.reason == "readiness_or_cooldown_unknown"


def test_current_spellbook_waits_without_authoritative_turn_signal() -> None:
    adapted = decide_current_spellbook_action(snapshot(authoritative=False), prowess=100)

    assert adapted.matched is True
    assert adapted.decision is not None
    assert adapted.decision.intent is CombatIntent.WAIT
    assert adapted.decision.reason == "turn_evidence_unknown"


def test_current_spellbook_uses_setup_then_strongest_ready_attack() -> None:
    setup = decide_current_spellbook_action(snapshot(), prowess=100)
    assert setup.decision is not None and setup.decision.skill is not None
    assert setup.decision.skill.slot == 1

    readiness = {slot: (False, 1) for slot in range(1, 6)}
    readiness[3] = (True, 0)
    readiness[5] = (True, 0)
    attack = decide_current_spellbook_action(snapshot(readiness=readiness), prowess=100)
    assert attack.decision is not None and attack.decision.skill is not None
    assert attack.decision.skill.slot == 3


def test_partial_current_spellbook_does_not_fall_through_to_generic_policy() -> None:
    value = snapshot()
    value["abilities"] = value["abilities"][:1]

    adapted = decide_current_spellbook_action(value, prowess=100)

    assert adapted.matched is True
    assert adapted.decision is not None
    assert adapted.decision.intent is CombatIntent.WAIT
    assert adapted.decision.reason == "spellbook_observation_incomplete"


def test_partial_current_spellbook_wait_is_bounded_then_stops_unsafe() -> None:
    value = snapshot()
    value["abilities"] = value["abilities"][:1]
    state = SpellbookBattleState(max_unknown_observations=2)
    state.begin(41)

    first = state.bound(decide_current_spellbook_action(value, prowess=100))
    second = state.bound(decide_current_spellbook_action(value, prowess=100))
    stopped = state.bound(decide_current_spellbook_action(value, prowess=100))

    assert first.decision is not None and first.decision.intent is CombatIntent.WAIT
    assert second.decision is not None and second.decision.intent is CombatIntent.WAIT
    assert stopped.decision is not None and stopped.decision.intent is CombatIntent.STOP_UNSAFE
    assert stopped.decision.reason == "spellbook_evidence_timeout:spellbook_observation_incomplete"


def test_confirmed_setup_is_blocked_until_battle_boundary() -> None:
    state = SpellbookBattleState()
    state.begin(10)
    first = state.bound(decide_current_spellbook_action(snapshot(), prowess=100))
    assert first.decision is not None and first.decision.skill is not None
    assert first.decision.skill.slot == 1
    state.confirm_pending(1, battle_id=10)

    second = state.bound(
        decide_current_spellbook_action(
            snapshot(),
            prowess=100,
            anti_spam_blocked_slots=frozenset(state.confirmed_setup_slots),
        )
    )
    assert second.decision is not None and second.decision.skill is not None
    assert second.decision.skill.slot == 4

    state.begin(11)
    reset = state.bound(decide_current_spellbook_action(snapshot(), prowess=100))
    assert state.confirmed_setup_slots == set()
    assert reset.decision is not None and reset.decision.skill is not None
    assert reset.decision.skill.slot == 1


def test_latched_current_profile_cannot_fall_through_when_observation_disappears() -> None:
    state = SpellbookBattleState(max_unknown_observations=1)
    state.begin(12)
    state.bound(decide_current_spellbook_action(snapshot(), prowess=100))
    missing = decide_current_spellbook_action({"abilities": []}, prowess=100)
    assert missing.matched is False

    waiting = state.bound(missing)
    stopped = state.bound(missing)

    assert waiting.matched is True
    assert waiting.decision is not None and waiting.decision.intent is CombatIntent.WAIT
    assert waiting.decision.reason == "spellbook_observation_lost"
    assert stopped.decision is not None and stopped.decision.intent is CombatIntent.STOP_UNSAFE
    assert stopped.decision.reason == "spellbook_evidence_timeout:spellbook_observation_lost"


def test_repeated_unconfirmed_slot_two_is_blocked_without_starving_turn_attack() -> None:
    readiness = {slot: (False, 1) for slot in range(1, 6)}
    readiness[2] = (True, 0)
    readiness[3] = (True, 0)
    value = snapshot(readiness=readiness)
    state = SpellbookBattleState(max_rejections_per_slot=2)
    state.begin(20)

    for _ in range(2):
        decision = state.bound(
            decide_current_spellbook_action(
                value,
                prowess=100,
                anti_spam_blocked_slots=frozenset(state.blocked_setup_slots),
            )
        )
        assert decision.decision is not None and decision.decision.skill is not None
        assert decision.decision.skill.slot == 2
        state.reject_pending(2)

    progressed = state.bound(
        decide_current_spellbook_action(
            value,
            prowess=100,
            anti_spam_blocked_slots=frozenset(state.blocked_setup_slots),
        )
    )
    assert state.rejection_counts == {2: 2}
    assert state.blocked_setup_slots == {2}
    assert progressed.decision is not None and progressed.decision.skill is not None
    assert progressed.decision.skill.slot == 3


def test_rejection_limits_reset_for_new_battle() -> None:
    state = SpellbookBattleState(max_rejections_per_slot=1)
    state.begin(30)
    adapted = state.bound(decide_current_spellbook_action(snapshot(), prowess=100))
    assert adapted.decision is not None and adapted.decision.skill is not None
    state.reject_pending(1)
    assert state.blocked_setup_slots == {1}

    state.begin(31)

    assert state.rejection_counts == {}
    assert state.blocked_setup_slots == set()
    assert state.rejection_stop_reason is None


def test_repeated_turn_attack_rejection_stops_unsafe() -> None:
    readiness = {slot: (False, 1) for slot in range(1, 6)}
    readiness[3] = (True, 0)
    value = snapshot(readiness=readiness)
    state = SpellbookBattleState(max_rejections_per_slot=2)
    state.begin(40)

    for _ in range(2):
        adapted = state.bound(decide_current_spellbook_action(value, prowess=100))
        assert adapted.decision is not None and adapted.decision.skill is not None
        assert adapted.decision.skill.slot == 3
        state.reject_pending(3)

    stopped = state.bound(decide_current_spellbook_action(value, prowess=100))

    assert stopped.decision is not None
    assert stopped.decision.intent is CombatIntent.STOP_UNSAFE
    assert stopped.decision.reason == "spellbook_attack_rejection_limit:slot_3"


def test_unrelated_spellbook_is_not_claimed() -> None:
    adapted = decide_current_spellbook_action(
        {"abilities": [{"slot": 2, "name": "Возмездие I"}]},
        prowess=100,
    )

    assert adapted.matched is False
    assert adapted.decision is None


def test_known_spellbook_name_with_malformed_identity_stops_unsafe() -> None:
    adapted = decide_current_spellbook_action(
        {"abilities": [{"id": -1001, "slot": "1junk", "name": "Элитная выучка I"}]},
        prowess=100,
    )

    assert adapted.matched is True
    assert adapted.decision is not None
    assert adapted.decision.intent is CombatIntent.STOP_UNSAFE
    assert adapted.decision.reason == "spellbook_identity_invalid"


def test_positive_item_and_unrelated_negative_control_do_not_pollute_current_book() -> None:
    value = snapshot()
    value["abilities"].extend(
        [
            {"id": 438, "slot": 1, "name": "Малый бурдюк жизни"},
            {"id": -9999, "slot": 0, "name": "Обычная атака"},
        ]
    )

    adapted = decide_current_spellbook_action(value, prowess=100)

    assert adapted.matched is True
    assert adapted.decision is not None
    assert adapted.decision.intent is CombatIntent.USE_SKILL
    assert adapted.decision.skill is not None and adapted.decision.skill.slot == 1
