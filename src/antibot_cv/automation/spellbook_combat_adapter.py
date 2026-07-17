"""Fail-closed adapter between an injector battle snapshot and spellbook policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from src.antibot_cv.automation.combat_policy import AvailableSkill, CombatDecision, CombatIntent
from src.antibot_cv.automation.spellbook_policy import (
    CURRENT_V3G45_SPELLBOOK,
    SkillReadiness,
    SpellbookEvidence,
    SpellbookIntent,
    SpellbookPolicy,
)


@dataclass(frozen=True)
class AdaptedSpellbookDecision:
    matched: bool
    decision: CombatDecision | None = None


@dataclass
class SpellbookBattleState:
    """Bounded per-battle evidence and confirmed setup memory."""

    max_unknown_observations: int = 8
    max_rejections_per_slot: int = 2
    battle_id: int | None = None
    unknown_observations: int = 0
    confirmed_setup_slots: set[int] = field(default_factory=set)
    blocked_setup_slots: set[int] = field(default_factory=set)
    rejection_counts: dict[int, int] = field(default_factory=dict)
    rejection_stop_reason: str | None = None
    pending_slot: int | None = None
    profile_latched: bool = False

    def begin(self, battle_id: int | None) -> None:
        if battle_id == self.battle_id:
            return
        self.battle_id = battle_id
        self.unknown_observations = 0
        self.confirmed_setup_slots.clear()
        self.blocked_setup_slots.clear()
        self.rejection_counts.clear()
        self.rejection_stop_reason = None
        self.pending_slot = None
        self.profile_latched = False

    def bound(self, adapted: AdaptedSpellbookDecision) -> AdaptedSpellbookDecision:
        if adapted.matched:
            self.profile_latched = True
        elif self.profile_latched:
            adapted = _wait("spellbook_observation_lost")
        if self.rejection_stop_reason is not None:
            self.pending_slot = None
            return _unsafe(self.rejection_stop_reason)
        decision = adapted.decision
        if not adapted.matched or decision is None:
            self.unknown_observations = 0
            self.pending_slot = None
            return adapted
        if decision.intent is CombatIntent.WAIT and decision.reason in {
            "spellbook_observation_incomplete",
            "spellbook_observation_lost",
            "turn_evidence_unknown",
            "readiness_or_cooldown_unknown",
            "prowess_unknown",
            "bound_character_spellbook_identity_unknown",
        }:
            self.unknown_observations += 1
            self.pending_slot = None
            if self.unknown_observations > max(0, self.max_unknown_observations):
                return _unsafe(f"spellbook_evidence_timeout:{decision.reason}")
            return adapted
        self.unknown_observations = 0
        self.pending_slot = decision.skill.slot if decision.intent is CombatIntent.USE_SKILL and decision.skill else None
        return adapted

    def confirm_pending(self, slot: int, *, battle_id: int | None) -> None:
        if battle_id != self.battle_id or slot != self.pending_slot:
            return
        if slot in {1, 2, 4}:
            self.confirmed_setup_slots.add(slot)
        self.rejection_counts.pop(slot, None)
        self.pending_slot = None

    def reject_pending(self, slot: int) -> None:
        if slot != self.pending_slot:
            return
        count = self.rejection_counts.get(slot, 0) + 1
        self.rejection_counts[slot] = count
        if count >= max(1, self.max_rejections_per_slot):
            if slot in {1, 2, 4}:
                self.blocked_setup_slots.add(slot)
            else:
                self.rejection_stop_reason = f"spellbook_attack_rejection_limit:slot_{slot}"
        self.pending_slot = None


def decide_current_spellbook_action(
    battle_snapshot: Mapping[str, object],
    *,
    prowess: int | None,
    anti_spam_blocked_slots: frozenset[int] = frozenset(),
    character_name: str | None = None,
) -> AdaptedSpellbookDecision:
    """Return one action only when the current book has complete explicit evidence.

    A partial match is deliberately treated as the current profile. This keeps a
    transiently incomplete snapshot from falling through to the generic rotating
    slot policy. A snapshot with no current-profile identity remains available to
    the generic policy for other characters and test fixtures.
    """

    raw_abilities = battle_snapshot.get("abilities")
    raw_mappings = (
        [value for value in raw_abilities if isinstance(value, Mapping)]
        if isinstance(raw_abilities, list)
        else []
    )
    abilities = [
        value
        for value in raw_mappings
        if (ability_id := _strict_int(value.get("id"))) is not None and ability_id < 0
    ]
    expected = {skill.slot: skill for skill in CURRENT_V3G45_SPELLBOOK.skills}
    known_names = {skill.name for skill in expected.values()}
    current_name_hint = any(str(value.get("name") or "").strip() in known_names for value in raw_mappings)
    observed_pairs = {
        (slot, str(value.get("name") or "").strip())
        for value in abilities
        if (slot := _strict_int(value.get("slot"))) is not None
    }
    expected_pairs = {(skill.slot, skill.name) for skill in expected.values()}
    if not (observed_pairs & expected_pairs):
        if current_name_hint:
            return _unsafe("spellbook_identity_invalid")
        if str(character_name or "").strip().casefold() == "v3g45":
            return _wait("bound_character_spellbook_identity_unknown")
        return AdaptedSpellbookDecision(False)

    by_slot: dict[int, Mapping[str, object]] = {}
    for value in abilities:
        slot = _strict_int(value.get("slot"))
        if slot not in expected:
            continue
        if slot in by_slot:
            return _unsafe("spellbook_observation_ambiguous")
        by_slot[slot] = value
    if set(by_slot) != set(expected):
        return _wait("spellbook_observation_incomplete")
    if any(str(by_slot[slot].get("name") or "").strip() != skill.name for slot, skill in expected.items()):
        return _unsafe("spellbook_identity_mismatch")

    turn_evidence = battle_snapshot.get("turnEvidence")
    if not isinstance(turn_evidence, Mapping) or turn_evidence.get("authoritative") is not True:
        return _wait("turn_evidence_unknown")
    my_turn = turn_evidence.get("myTurn")
    if not isinstance(my_turn, bool):
        return _unsafe("invalid_turn_evidence")

    readiness: list[SkillReadiness] = []
    for slot, skill in expected.items():
        raw = by_slot[slot]
        evidence = raw.get("readinessEvidence")
        if not isinstance(evidence, Mapping) or evidence.get("authoritative") is not True:
            readiness.append(SkillReadiness(slot, skill.name, None, None))
            continue
        ready = evidence.get("ready")
        cooldown = evidence.get("cooldownRemaining")
        if not isinstance(ready, bool) or not _valid_cooldown(cooldown):
            return _unsafe("invalid_readiness_evidence")
        readiness.append(SkillReadiness(slot, skill.name, ready, cooldown))

    decision = SpellbookPolicy(CURRENT_V3G45_SPELLBOOK).decide(
        SpellbookEvidence(
            my_turn=my_turn,
            prowess=prowess,
            skills=tuple(readiness),
            worthwhile_setup_slots=frozenset({1, 4}),
            anti_spam_blocked_slots=anti_spam_blocked_slots,
            defensive_needed=True,
        )
    )
    if decision.intent is SpellbookIntent.STOP_UNSAFE:
        return _unsafe(decision.reason)
    if decision.intent is SpellbookIntent.WAIT:
        return _wait(decision.reason)
    chosen = decision.setup_actions[0] if decision.setup_actions else decision.turn_action
    if chosen is None and decision.fallback_slot is not None:
        return AdaptedSpellbookDecision(
            True,
            CombatDecision(
                CombatIntent.USE_SKILL,
                decision.reason,
                skill=AvailableSkill("zero_resource_fallback", decision.fallback_slot, ready=True, cooldown=0),
            ),
        )
    if chosen is None:
        return _wait("spellbook_plan_without_action")
    return AdaptedSpellbookDecision(
        True,
        CombatDecision(
            CombatIntent.USE_SKILL,
            decision.reason,
            skill=AvailableSkill(
                chosen.name,
                chosen.slot,
                damage=chosen.expected_damage,
                ready=True,
                cooldown=0,
            ),
        ),
    )


def _wait(reason: str) -> AdaptedSpellbookDecision:
    return AdaptedSpellbookDecision(True, CombatDecision(CombatIntent.WAIT, reason))


def _unsafe(reason: str) -> AdaptedSpellbookDecision:
    return AdaptedSpellbookDecision(True, CombatDecision(CombatIntent.STOP_UNSAFE, reason))


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _valid_cooldown(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
