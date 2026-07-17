"""Immutable mutation binding for one explicitly observed combat skill."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from src.antibot_cv.automation.combat_policy import CombatDecision, CombatIntent


@dataclass(frozen=True)
class SkillMutationBinding:
    skill_id: int
    skill_name: str
    slot: int
    battle_identity: str
    snapshot_id: str
    observation_token: str

    def metadata(self) -> dict[str, object]:
        return {
            "expected_skill_id": self.skill_id,
            "expected_skill_name": self.skill_name,
            "expected_skill_slot": self.slot,
            "expected_battle_identity": self.battle_identity,
            "expected_battle_snapshot_id": self.snapshot_id,
            "expected_battle_observation_token": self.observation_token,
        }


def bind_skill_mutation(
    snapshot: Mapping[str, object],
    decision: CombatDecision,
) -> SkillMutationBinding | None:
    """Bind a planned skill to exact, authoritative pre-mutation evidence."""

    if decision.intent is not CombatIntent.USE_SKILL or decision.skill is None:
        return None
    snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=128)
    battle_identity = _bounded_text(snapshot.get("battleIdentity"), max_length=320)
    observation_token = _bounded_text(snapshot.get("observationToken"), max_length=192)
    if (
        not _valid_opaque_id(snapshot_id)
        or not _valid_battle_identity(battle_identity)
        or not _valid_opaque_id(observation_token)
    ):
        return None
    abilities = snapshot.get("abilities")
    if not isinstance(abilities, list):
        return None
    matches = [
        value
        for value in abilities
        if isinstance(value, Mapping)
        and _strict_int(value.get("slot")) == decision.skill.slot
        and _bounded_text(value.get("name"), max_length=200) == decision.skill.name
    ]
    if len(matches) != 1:
        return None
    ability = matches[0]
    skill_id = _strict_int(ability.get("id"))
    if (
        skill_id is None
        or skill_id >= 0
    ):
        return None
    return SkillMutationBinding(
        skill_id,
        decision.skill.name,
        decision.skill.slot,
        battle_identity,
        snapshot_id,
        observation_token,
    )


def refresh_skill_mutation_binding(
    snapshot: Mapping[str, object],
    previous: SkillMutationBinding,
) -> SkillMutationBinding | None:
    """Rebind the same skill to a newly minted, authoritative battle observation."""

    snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=128)
    battle_identity = _bounded_text(snapshot.get("battleIdentity"), max_length=320)
    observation_token = _bounded_text(snapshot.get("observationToken"), max_length=192)
    turn_evidence = snapshot.get("turnEvidence")
    if (
        not _valid_opaque_id(snapshot_id)
        or not _valid_battle_identity(battle_identity)
        or not _valid_opaque_id(observation_token)
        or snapshot_id == previous.snapshot_id
        or observation_token == previous.observation_token
        or battle_identity != previous.battle_identity
        or not isinstance(turn_evidence, Mapping)
        or turn_evidence.get("authoritative") is not True
        or turn_evidence.get("myTurn") is not True
        or snapshot.get("myTurn") is not True
    ):
        return None
    abilities = snapshot.get("abilities")
    if not isinstance(abilities, list):
        return None
    matches = [
        value
        for value in abilities
        if isinstance(value, Mapping)
        and _strict_int(value.get("id")) == previous.skill_id
        and _strict_int(value.get("slot")) == previous.slot
        and _bounded_text(value.get("name"), max_length=200) == previous.skill_name
    ]
    if len(matches) != 1:
        return None
    return SkillMutationBinding(
        previous.skill_id,
        previous.skill_name,
        previous.slot,
        battle_identity,
        snapshot_id,
        observation_token,
    )


def skill_mutation_payload(metadata: Mapping[str, object], slot: int) -> dict[str, object] | None:
    """Translate an action request without weakening its exact mutation binding."""

    skill_id = _strict_int(metadata.get("expected_skill_id"))
    expected_slot = _strict_int(metadata.get("expected_skill_slot"))
    skill_name = _bounded_text(metadata.get("expected_skill_name"), max_length=200)
    battle_identity = _bounded_text(metadata.get("expected_battle_identity"), max_length=320)
    snapshot_id = _bounded_text(metadata.get("expected_battle_snapshot_id"), max_length=128)
    observation_token = _bounded_text(metadata.get("expected_battle_observation_token"), max_length=192)
    if (
        skill_id is None
        or skill_id >= 0
        or expected_slot != slot
        or not skill_name
        or not _valid_battle_identity(battle_identity)
        or not _valid_opaque_id(snapshot_id)
        or not _valid_opaque_id(observation_token)
    ):
        return None
    return {
        "slot": slot,
        "preClickDelayMs": metadata.get("pre_click_delay_ms", 0),
        "clickHoldMs": metadata.get("click_hold_ms", 0),
        "verifyTimeoutMs": 900,
        "commandTimeoutMs": 4000,
        "expectedSkillId": skill_id,
        "expectedSkillName": skill_name,
        "expectedSkillSlot": expected_slot,
        "expectedBattleIdentity": battle_identity,
        "expectedSnapshotId": snapshot_id,
        "expectedObservationToken": observation_token,
    }


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bounded_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str) or value != value.strip():
        return ""
    if not value or len(value) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")


def _valid_opaque_id(value: str) -> bool:
    return bool(value and _OPAQUE_ID_PATTERN.fullmatch(value))


def _valid_battle_identity(value: str) -> bool:
    parts = value.split("|")
    return (
        len(parts) == 3
        and 0 < len(parts[0]) <= 120
        and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in parts[0])
        and parts[1].startswith("battle:")
        and 0 < len(parts[1][7:]) <= 100
        and _valid_opaque_id(parts[1][7:])
        and parts[2].startswith("opp:")
        and 0 < len(parts[2][4:]) <= 80
        and _valid_opaque_id(parts[2][4:])
    )
