"""Deterministic, side-effect-free legacy/semantic quest decision comparison."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable, Mapping

from .quest_active_catalog import ActiveQuestEntry
from .quest_compiler import QuestCompileStatus, compile_quest_plan
from .quest_compiler_adapters import LEGACY_COMPILER_ADAPTERS
from .quest_evidence import EvidenceEnvelope
from .quest_plan_evaluator import (
    EvaluationContext,
    EvaluationStatus,
    evaluate_quest_plan,
)
from .quest_plan_model import Acquire, InteractNpc, Kill, QuestLeaf, TurnIn, Visit
from .quest_shadow_legacy_adapter import (
    LegacyDecisionEnvelope,
    normalize_legacy_decision,
)


class ShadowComparisonStatus(str, Enum):
    MATCH = "match"
    DIVERGED = "diverged"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True, slots=True)
class SemanticDecisionEnvelope:
    status: EvaluationStatus
    atom_kind: str | None
    target: str | None
    requirement_id: str | None
    plan_fingerprint: str

    def to_canonical_dict(self) -> dict[str, str | None]:
        return {
            "atom_kind": self.atom_kind,
            "plan_fingerprint": self.plan_fingerprint,
            "requirement_id": self.requirement_id,
            "status": self.status.value,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    status: ShadowComparisonStatus
    code: str
    quest_id: str
    legacy: LegacyDecisionEnvelope | None
    semantic: SemanticDecisionEnvelope | None
    diagnostics: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "code": self.code,
            "diagnostics": self.diagnostics,
            "legacy": None if self.legacy is None else self.legacy.to_canonical_dict(),
            "quest_id": self.quest_id,
            "semantic": None if self.semantic is None else self.semantic.to_canonical_dict(),
            "status": self.status.value,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_shadow_decision(
    entry: ActiveQuestEntry,
    legacy_decision: Mapping[str, object] | LegacyDecisionEnvelope,
    *,
    evidence: Iterable[EvidenceEnvelope] = (),
    capabilities: Iterable[str] = (),
    context: EvaluationContext | None = None,
) -> ShadowComparison:
    """Compare one already-observed legacy decision with the semantic core."""

    try:
        legacy = (
            legacy_decision
            if isinstance(legacy_decision, LegacyDecisionEnvelope)
            else normalize_legacy_decision(legacy_decision)
        )
    except (TypeError, ValueError) as exc:
        return ShadowComparison(
            ShadowComparisonStatus.INCOMPARABLE,
            "legacy_envelope_invalid",
            getattr(entry, "id", ""),
            None,
            None,
            (type(exc).__name__,),
        )
    if not isinstance(entry, ActiveQuestEntry) or legacy.quest_id != entry.id:
        return ShadowComparison(
            ShadowComparisonStatus.INCOMPARABLE,
            "quest_identity_mismatch",
            getattr(entry, "id", ""),
            legacy,
            None,
            (),
        )

    compiled = compile_quest_plan(entry, adapters=LEGACY_COMPILER_ADAPTERS)
    if compiled.status is not QuestCompileStatus.READY or compiled.plan is None:
        return ShadowComparison(
            ShadowComparisonStatus.INCOMPARABLE,
            f"semantic_compile_{compiled.status.value}",
            entry.id,
            legacy,
            None,
            compiled.diagnostics,
        )
    evaluated = evaluate_quest_plan(
        compiled.plan,
        evidence,
        capabilities=capabilities,
        context=context,
    )
    semantic = _semantic_envelope(compiled.plan.fingerprint, evaluated.status, evaluated.next_atom)
    matched = (
        legacy.status.value == semantic.status.value
        and legacy.atom_kind == semantic.atom_kind
        and legacy.target == semantic.target
    )
    return ShadowComparison(
        ShadowComparisonStatus.MATCH if matched else ShadowComparisonStatus.DIVERGED,
        "decision_match" if matched else _divergence_code(legacy, semantic),
        entry.id,
        legacy,
        semantic,
        (),
    )


def _semantic_envelope(
    plan_fingerprint: str,
    status: EvaluationStatus,
    atom: QuestLeaf | None,
) -> SemanticDecisionEnvelope:
    if atom is None:
        return SemanticDecisionEnvelope(status, None, None, None, plan_fingerprint)
    kind, target = _atom_identity(atom)
    return SemanticDecisionEnvelope(
        status, kind, target, atom.requirement_id, plan_fingerprint
    )


def _atom_identity(atom: QuestLeaf) -> tuple[str, str]:
    if isinstance(atom, Acquire):
        return "acquire", atom.item
    if isinstance(atom, Kill):
        return "kill", atom.target
    if isinstance(atom, Visit):
        return "visit", atom.location
    if isinstance(atom, InteractNpc):
        return "interact_npc", atom.npc
    if isinstance(atom, TurnIn):
        return "turn_in", atom.npc
    raise TypeError("unsupported semantic atom")


def _divergence_code(
    legacy: LegacyDecisionEnvelope, semantic: SemanticDecisionEnvelope
) -> str:
    if legacy.status.value != semantic.status.value:
        return "status_diverged"
    if legacy.atom_kind != semantic.atom_kind:
        return "atom_kind_diverged"
    return "target_diverged"


__all__ = [
    "SemanticDecisionEnvelope",
    "ShadowComparison",
    "ShadowComparisonStatus",
    "compare_shadow_decision",
]
