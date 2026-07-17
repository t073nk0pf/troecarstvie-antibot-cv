import time

from src.antibot_cv.automation.combat_policy import CombatIntent
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.spellbook_policy import CURRENT_V3G45_SPELLBOOK
from src.antibot_cv.detection.resources import ResourceBarStatus, ResourceStatus
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def status() -> ResourceStatus:
    return ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )


def snapshot(*, readiness=None, snapshot_id="battle-snapshot-1", token="page-token-1"):
    readiness = readiness or {slot: (True, 0) for slot in range(1, 6)}
    return {
        "snapshotId": snapshot_id,
        "battleIdentity": "fight.php|battle:7|opp:42",
        "observationToken": token,
        "hasFight": True,
        "finished": False,
        "myTurn": True,
        "turnEvidence": {"authoritative": True, "myTurn": True},
        "abilities": [
            {
                "id": -1000 - skill.slot,
                "slot": skill.slot,
                "name": skill.name,
                "ready": readiness[skill.slot][0],
                "cooldown": readiness[skill.slot][1],
                "readinessEvidence": {
                    "authoritative": readiness[skill.slot][0] is not None,
                    "ready": readiness[skill.slot][0],
                    "cooldownRemaining": readiness[skill.slot][1],
                },
            }
            for skill in CURRENT_V3G45_SPELLBOOK.skills
        ],
        "items": [],
    }


def controller(test_config: AutomationConfig) -> AutomationController:
    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {
        **data["combat"],
        "slot_sequence": [1, 2, 3, 4, 5],
        "require_ready_confirmation": False,
    }
    return AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )


def test_recognized_spellbook_does_not_use_permissive_round_robin_on_unknown_evidence(
    test_config: AutomationConfig,
) -> None:
    readiness = {slot: (True, 0) for slot in range(1, 6)}
    readiness[1] = (None, None)

    decision = controller(test_config)._combat_policy_decision(status(), snapshot(readiness=readiness))

    assert decision.intent is CombatIntent.WAIT
    assert decision.reason == "readiness_or_cooldown_unknown"


def test_bound_v3g45_cold_start_never_falls_through_to_generic_slot_policy(
    test_config: AutomationConfig,
) -> None:
    runtime = controller(test_config)
    runtime._bound_character_name = "v3g45"
    unknown = {
        "snapshotId": "battle-cold-1",
        "battleIdentity": "fight.php|opp:42",
        "hasFight": True,
        "finished": False,
        "myTurn": True,
        "useSkillAvailable": True,
        "turnEvidence": {"authoritative": True, "myTurn": True},
        "abilities": [],
        "items": [],
    }

    for _ in range(runtime._spellbook_battle_state.max_unknown_observations):
        decision = runtime._combat_policy_decision(status(), unknown)
        assert decision.intent is CombatIntent.WAIT
        assert decision.reason == "bound_character_spellbook_identity_unknown"
        assert runtime._pending_skill_mutation_binding is None

    stopped = runtime._combat_policy_decision(status(), unknown)

    assert stopped.intent is CombatIntent.STOP_UNSAFE
    assert stopped.reason == "spellbook_evidence_timeout:bound_character_spellbook_identity_unknown"
    assert runtime._pending_skill_mutation_binding is None


def test_recognized_spellbook_chooses_strongest_ready_turn_attack(
    test_config: AutomationConfig,
) -> None:
    readiness = {slot: (False, 1) for slot in range(1, 6)}
    readiness[3] = (True, 0)
    readiness[5] = (True, 0)

    decision = controller(test_config)._combat_policy_decision(status(), snapshot(readiness=readiness))

    assert decision.intent is CombatIntent.USE_SKILL
    assert decision.skill is not None
    assert decision.skill.slot == 3
    assert decision.skill.name == "Прорубание II"


def test_runtime_advances_past_confirmed_setup_and_resets_on_new_battle(
    test_config: AutomationConfig,
) -> None:
    runtime = controller(test_config)
    first_battle = runtime.session.new_battle()

    first = runtime._combat_policy_decision(status(), snapshot())
    assert first.skill is not None and first.skill.slot == 1
    runtime._spellbook_battle_state.confirm_pending(1, battle_id=first_battle)

    second = runtime._combat_policy_decision(status(), snapshot())
    assert second.skill is not None and second.skill.slot == 4

    runtime.session.new_battle()
    reset = runtime._combat_policy_decision(status(), snapshot())
    assert reset.skill is not None and reset.skill.slot == 1


def test_runtime_bounds_repeated_unconfirmed_slot_two_and_advances_to_attack(
    test_config: AutomationConfig,
) -> None:
    runtime = controller(test_config)
    runtime.session.new_battle()
    runtime.action_executor.execute = lambda request: False  # type: ignore[method-assign]
    readiness = {slot: (False, 1) for slot in range(1, 6)}
    readiness[2] = (True, 0)
    readiness[3] = (True, 0)
    value = snapshot(readiness=readiness)

    for _ in range(2):
        decision = runtime._combat_policy_decision(status(), value)
        assert decision.skill is not None and decision.skill.slot == 2
        assert runtime._use_skill_slot_via_injector("click_combat_slot", 2, "test_intended") is False

    progressed = runtime._combat_policy_decision(status(), value)

    assert runtime._spellbook_battle_state.rejection_counts == {2: 2}
    assert runtime._spellbook_battle_state.blocked_setup_slots == {2}
    assert progressed.intent is CombatIntent.USE_SKILL
    assert progressed.skill is not None and progressed.skill.slot == 3

    runtime.session.new_battle()
    reset = runtime._combat_policy_decision(status(), value)
    assert runtime._spellbook_battle_state.rejection_counts == {}
    assert runtime._spellbook_battle_state.blocked_setup_slots == set()
    assert reset.skill is not None and reset.skill.slot == 2


def test_mutation_refreshes_cached_binding_and_executes_only_with_new_token(
    test_config: AutomationConfig,
) -> None:
    runtime = controller(test_config)
    runtime.session.new_battle()
    planned = snapshot(snapshot_id="battle-old", token="page-token-old")
    runtime._last_battle_snapshot_cache = planned
    runtime._last_battle_snapshot_success_monotonic = time.monotonic() - 10
    decision = runtime._combat_policy_decision(status(), planned)
    assert decision.skill is not None
    fresh = snapshot(snapshot_id="battle-new", token="page-token-new")
    calls: list[bool] = []
    requests = []
    runtime._battle_snapshot_via_injector = lambda force=False: calls.append(force) or fresh
    runtime.action_executor.execute = lambda request: requests.append(request) or True  # type: ignore[method-assign]

    assert runtime._use_skill_slot_via_injector("click_combat_slot", decision.skill.slot, "test") is True
    assert calls == [True]
    assert len(requests) == 1
    assert requests[0].metadata["expected_battle_snapshot_id"] == "battle-new"
    assert requests[0].metadata["expected_battle_observation_token"] == "page-token-new"


def test_mutation_refresh_failure_or_reused_cache_causes_zero_action(
    test_config: AutomationConfig,
) -> None:
    for refreshed in (None, snapshot()):
        runtime = controller(test_config)
        runtime.session.new_battle()
        planned = snapshot()
        decision = runtime._combat_policy_decision(status(), planned)
        assert decision.skill is not None
        calls: list[bool] = []
        requests = []
        runtime._battle_snapshot_via_injector = lambda force=False, value=refreshed: calls.append(force) or value
        runtime.action_executor.execute = lambda request: requests.append(request) or True  # type: ignore[method-assign]

        assert runtime._use_skill_slot_via_injector("click_combat_slot", decision.skill.slot, "test") is False
        assert calls == [True]
        assert requests == []
        assert runtime._pending_skill_mutation_binding is None
