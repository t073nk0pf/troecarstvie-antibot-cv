from src.antibot_cv.automation.combat_policy import AvailableSkill, CombatDecision, CombatIntent
from src.antibot_cv.automation.combat_skill_mutation import (
    bind_skill_mutation,
    refresh_skill_mutation_binding,
    skill_mutation_payload,
)


def snapshot(*, slot: int = 3, ready: object = True, cooldown: object = 0) -> dict[str, object]:
    return {
        "snapshotId": "battle-7",
        "battleIdentity": "fight.php|battle:7|opp:42",
        "observationToken": "page-token-1",
        "abilities": [
            {
                "id": -1003,
                "slot": slot,
                "name": "Прорубание II",
                "readinessEvidence": {
                    "authoritative": True,
                    "ready": ready,
                    "cooldownRemaining": cooldown,
                },
            }
        ],
    }


def decision() -> CombatDecision:
    return CombatDecision(
        CombatIntent.USE_SKILL,
        "test",
        skill=AvailableSkill("Прорубание II", 3, ready=True, cooldown=0),
    )


def test_exact_ready_skill_produces_mutation_binding() -> None:
    binding = bind_skill_mutation(snapshot(), decision())

    assert binding is not None
    assert binding.skill_id == -1003
    assert binding.slot == 3
    assert binding.snapshot_id == "battle-7"
    assert binding.observation_token == "page-token-1"


def test_changed_slot_produces_no_binding_but_cooldown_does_not_block_submission() -> None:
    assert bind_skill_mutation(snapshot(slot=4), decision()) is None
    assert bind_skill_mutation(snapshot(cooldown=1), decision()) is not None
    assert bind_skill_mutation(snapshot(ready=None, cooldown=None), decision()) is not None


def test_missing_token_or_malformed_battle_epoch_produces_no_binding() -> None:
    missing_token = snapshot()
    missing_token["observationToken"] = ""
    malformed_identity = snapshot()
    malformed_identity["battleIdentity"] = "fight.php|opp:42"
    malformed_snapshot = snapshot()
    malformed_snapshot["snapshotId"] = "battle 7"
    malformed_token = snapshot()
    malformed_token["observationToken"] = ["page-token-1"]

    assert bind_skill_mutation(missing_token, decision()) is None
    assert bind_skill_mutation(malformed_identity, decision()) is None
    assert bind_skill_mutation(malformed_snapshot, decision()) is None
    assert bind_skill_mutation(malformed_token, decision()) is None


def test_refresh_requires_new_token_snapshot_same_battle_and_authoritative_turn() -> None:
    previous = bind_skill_mutation(snapshot(), decision())
    assert previous is not None
    fresh = snapshot()
    fresh.update(
        {
            "snapshotId": "battle-8",
            "observationToken": "page-token-2",
            "myTurn": True,
            "turnEvidence": {"authoritative": True, "myTurn": True},
        }
    )

    refreshed = refresh_skill_mutation_binding(fresh, previous)

    assert refreshed is not None
    assert refreshed.snapshot_id == "battle-8"
    assert refreshed.observation_token == "page-token-2"

    for key, value in (
        ("snapshotId", previous.snapshot_id),
        ("observationToken", previous.observation_token),
        ("battleIdentity", "fight.php|battle:8|opp:42"),
        ("myTurn", False),
    ):
        unsafe = dict(fresh)
        unsafe[key] = value
        assert refresh_skill_mutation_binding(unsafe, previous) is None


def test_payload_rejects_non_string_unbounded_and_malformed_identity_fields() -> None:
    binding = bind_skill_mutation(snapshot(), decision())
    assert binding is not None
    metadata = binding.metadata()
    assert skill_mutation_payload(metadata, 3) is not None

    for key, value in (
        ("expected_skill_name", {"name": "Прорубание II"}),
        ("expected_battle_identity", ["fight.php", "battle:7", "opp:42"]),
        ("expected_battle_snapshot_id", 7),
        ("expected_battle_observation_token", 123),
        ("expected_skill_name", "x" * 201),
        ("expected_battle_snapshot_id", "bad id"),
        ("expected_battle_observation_token", "bad/token"),
        ("expected_battle_identity", "fight.php|battle:7|opp:42|extra"),
        ("expected_battle_identity", "fight.php|battle:7 8|opp:42"),
    ):
        unsafe = {**metadata, key: value}
        assert skill_mutation_payload(unsafe, 3) is None
