"""Action-free typed spellbook planning from explicit battle evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class SkillRole(str, Enum):
    BUFF = "buff"
    DEFENSE = "defense"
    ATTACK = "attack"
    DAMAGE_OVER_TIME = "damage_over_time"


class TurnEffect(str, Enum):
    INSTANT = "instant"
    REQUIRES_TURN_CONTINUES = "requires_turn_continues"
    CONSUMES_TURN = "consumes_turn"


class CooldownBasis(str, Enum):
    SECONDS = "seconds"
    TURNS_FROM_BATTLE_START = "turns_from_battle_start"


class SpellbookIntent(str, Enum):
    PLAN = "PLAN"
    WAIT = "WAIT"
    STOP_UNSAFE = "STOP_UNSAFE"


@dataclass(frozen=True)
class SkillSemantics:
    slot: int
    name: str
    role: SkillRole
    turn_effect: TurnEffect
    prowess_cost: int
    cooldown: int
    cooldown_basis: CooldownBasis
    expected_damage: float = 0.0
    setup_priority: int = 0
    defensive: bool = False


@dataclass(frozen=True)
class SpellbookProfile:
    skills: tuple[SkillSemantics, ...]
    fallback_slot: int = 0


@dataclass(frozen=True)
class SkillReadiness:
    slot: int
    name: str
    ready: bool | None
    cooldown_remaining: int | float | None


@dataclass(frozen=True)
class SpellbookEvidence:
    my_turn: bool | None
    prowess: int | None
    skills: tuple[SkillReadiness, ...]
    worthwhile_setup_slots: frozenset[int] = field(default_factory=frozenset)
    anti_spam_blocked_slots: frozenset[int] = field(default_factory=frozenset)
    defensive_needed: bool | None = None
    low_prowess_fallback: bool = False


@dataclass(frozen=True)
class SpellbookDecision:
    intent: SpellbookIntent
    reason: str
    setup_actions: tuple[SkillSemantics, ...] = field(default_factory=tuple)
    turn_action: SkillSemantics | None = None
    fallback_slot: int | None = None


class SpellbookPolicy:
    """Plan setup actions and at most one turn-consuming action."""

    def __init__(self, profile: SpellbookProfile) -> None:
        self.profile = profile

    def decide(self, evidence: SpellbookEvidence) -> SpellbookDecision:
        error = self._validate(evidence)
        if error:
            return SpellbookDecision(SpellbookIntent.STOP_UNSAFE, error)
        observed = {skill.slot: skill for skill in evidence.skills}
        if evidence.low_prowess_fallback:
            if evidence.my_turn is not True:
                return SpellbookDecision(SpellbookIntent.WAIT, "fallback_waits_for_player_turn")
            return SpellbookDecision(
                SpellbookIntent.PLAN,
                "explicit_low_prowess_fallback",
                fallback_slot=self.profile.fallback_slot,
            )
        if any(
            observed[skill.slot].ready is None
            or observed[skill.slot].cooldown_remaining is None
            for skill in self.profile.skills
        ):
            return SpellbookDecision(SpellbookIntent.WAIT, "readiness_or_cooldown_unknown")

        prowess = evidence.prowess
        if prowess is None:
            return SpellbookDecision(SpellbookIntent.WAIT, "prowess_unknown")
        remaining = prowess
        setup: list[SkillSemantics] = []
        setup_candidates = sorted(
            (
                skill
                for skill in self.profile.skills
                if skill.turn_effect is not TurnEffect.CONSUMES_TURN
            ),
            key=lambda skill: (skill.setup_priority, skill.slot),
        )
        for skill in setup_candidates:
            if skill.slot in evidence.anti_spam_blocked_slots:
                continue
            if skill.defensive and evidence.defensive_needed is not True:
                continue
            if not skill.defensive and skill.slot not in evidence.worthwhile_setup_slots:
                continue
            if skill.turn_effect is TurnEffect.REQUIRES_TURN_CONTINUES and evidence.my_turn is not True:
                continue
            if self._ready(observed[skill.slot]) and remaining >= skill.prowess_cost:
                setup.append(skill)
                remaining -= skill.prowess_cost

        turn_action = None
        if evidence.my_turn is True:
            attacks = sorted(
                (
                    skill
                    for skill in self.profile.skills
                    if skill.turn_effect is TurnEffect.CONSUMES_TURN
                    and skill.slot not in evidence.anti_spam_blocked_slots
                    and self._ready(observed[skill.slot])
                    and remaining >= skill.prowess_cost
                ),
                key=lambda skill: (-skill.expected_damage, skill.slot),
            )
            if attacks:
                turn_action = attacks[0]
        if setup or turn_action is not None:
            return SpellbookDecision(
                SpellbookIntent.PLAN,
                "explicit_ready_spellbook_plan",
                tuple(setup),
                turn_action,
            )
        return SpellbookDecision(SpellbookIntent.WAIT, "no_explicit_ready_action")

    def _validate(self, evidence: SpellbookEvidence) -> str | None:
        if not isinstance(evidence, SpellbookEvidence):
            return "invalid_evidence"
        if not isinstance(evidence.low_prowess_fallback, bool):
            return "invalid_low_prowess_fallback"
        slots = [skill.slot for skill in self.profile.skills]
        if len(slots) != len(set(slots)) or any(slot <= 0 for slot in slots):
            return "invalid_profile"
        observed: Mapping[int, SkillReadiness] = {skill.slot: skill for skill in evidence.skills}
        if len(observed) != len(evidence.skills) or set(observed) != set(slots):
            return "spellbook_observation_incomplete"
        for skill in self.profile.skills:
            value = observed[skill.slot]
            if value.name != skill.name:
                return "spellbook_identity_mismatch"
            cooldown = value.cooldown_remaining
            if (
                value.ready is not None
                and not isinstance(value.ready, bool)
                or cooldown is not None
                and (
                    isinstance(cooldown, bool)
                    or not isinstance(cooldown, (int, float))
                    or cooldown < 0
                )
            ):
                return "invalid_readiness_evidence"
            if value.ready is True and cooldown not in {None, 0}:
                return "contradictory_readiness_evidence"
        if evidence.prowess is not None and (
            isinstance(evidence.prowess, bool)
            or not isinstance(evidence.prowess, int)
            or evidence.prowess < 0
        ):
            return "invalid_prowess_evidence"
        return None

    @staticmethod
    def _ready(evidence: SkillReadiness) -> bool:
        return evidence.ready is True and evidence.cooldown_remaining == 0


CURRENT_V3G45_SPELLBOOK = SpellbookProfile(
    skills=(
        SkillSemantics(1, "Элитная выучка I", SkillRole.BUFF, TurnEffect.INSTANT, 14, 2, CooldownBasis.SECONDS, setup_priority=10),
        SkillSemantics(2, "Смена позиции I", SkillRole.DEFENSE, TurnEffect.REQUIRES_TURN_CONTINUES, 21, 12, CooldownBasis.TURNS_FROM_BATTLE_START, setup_priority=30, defensive=True),
        SkillSemantics(3, "Прорубание II", SkillRole.ATTACK, TurnEffect.CONSUMES_TURN, 17, 3, CooldownBasis.TURNS_FROM_BATTLE_START, expected_damage=13.0),
        SkillSemantics(4, "Глубокий порез II", SkillRole.DAMAGE_OVER_TIME, TurnEffect.INSTANT, 15, 8, CooldownBasis.TURNS_FROM_BATTLE_START, expected_damage=42.0, setup_priority=20),
        SkillSemantics(5, "Танцующее лезвие III", SkillRole.ATTACK, TurnEffect.CONSUMES_TURN, 13, 2, CooldownBasis.SECONDS, expected_damage=8.5),
    ),
)
