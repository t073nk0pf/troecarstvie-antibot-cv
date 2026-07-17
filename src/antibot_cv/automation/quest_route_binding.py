"""Pure quest-route identity validation shared by route coordinators."""

from __future__ import annotations

from dataclasses import dataclass

from src.antibot_cv.automation.runtime_helpers import (
    is_semantic_location_name,
    same_location_name,
)


QUEST_ROUTE_KINDS = frozenset(
    {"quest_accept", "quest_dialogue", "quest_location", "quest_turn_in", "quest_ordered_handoff"}
)


@dataclass(frozen=True)
class RouteIdentity:
    quest_id: str | None = None
    title: str | None = None
    location: str | None = None
    giver_names: tuple[str, ...] = ()
    fingerprint: str | None = None


@dataclass(frozen=True)
class QuestRouteBindingEvidence:
    kind: str
    target: str
    director_present: bool
    phase: str | None = None
    pending: RouteIdentity | None = None
    reference: RouteIdentity | None = None
    lease: RouteIdentity | None = None
    accepted: RouteIdentity | None = None
    plan_quest_id: str | None = None
    plan_kind: str | None = None
    active_quest_id: str | None = None
    route_link_label: str | None = None
    navigator_label: str | None = None
    target_routes: tuple[str, ...] = ()
    route_locations: tuple[str, ...] = ()


def validate_quest_route_binding(evidence: QuestRouteBindingEvidence) -> str | None:
    """Return a stable mismatch code, or ``None`` for one exact binding."""

    if evidence.kind not in QUEST_ROUTE_KINDS:
        return None
    if not is_semantic_location_name(evidence.target):
        return "target_invalid"
    if not evidence.director_present:
        return "director_missing"
    if evidence.kind == "quest_accept":
        if evidence.phase != "ROUTE" or evidence.pending is None:
            return "accept_phase_mismatch"
        if evidence.reference is None:
            return "accept_reference_mismatch"
        if (
            len(evidence.reference.giver_names) != 1
            or not evidence.reference.giver_names[0].strip()
        ):
            return "accept_giver_ambiguous"
        if (
            evidence.pending.quest_id != evidence.reference.quest_id
            or evidence.pending.title != evidence.reference.title
            or evidence.pending.location != evidence.reference.location
            or evidence.pending.giver_names != evidence.reference.giver_names
            or not same_location_name(evidence.target, evidence.pending.location)
        ):
            return "accept_identity_mismatch"
        return None
    if evidence.lease is None:
        return "lease_missing"
    if evidence.kind == "quest_ordered_handoff":
        if evidence.pending is None:
            return "ordered_handoff_cursor_missing"
        if (
            evidence.lease.quest_id != evidence.pending.quest_id
            or evidence.lease.title != evidence.pending.title
            or evidence.lease.fingerprint != evidence.pending.fingerprint
            or not same_location_name(evidence.target, evidence.pending.location)
        ):
            return "ordered_handoff_identity_mismatch"
        return None
    if evidence.kind == "quest_dialogue":
        if evidence.phase != "ROUTE" or evidence.pending is None:
            return "dialogue_phase_mismatch"
        if (
            evidence.lease.quest_id != evidence.pending.quest_id
            or evidence.lease.title != evidence.pending.title
            or evidence.lease.fingerprint != evidence.pending.fingerprint
            or evidence.plan_quest_id != evidence.pending.quest_id
            or evidence.plan_kind != "npc_dialogue_or_handoff"
            or not same_location_name(evidence.target, evidence.pending.location)
        ):
            return "dialogue_identity_mismatch"
        return None
    if evidence.kind == "quest_turn_in":
        if evidence.phase != "ROUTE" or evidence.pending is None:
            return "turn_in_phase_mismatch"
        if (
            evidence.lease.quest_id != evidence.pending.quest_id
            or evidence.lease.title != evidence.pending.title
            or evidence.lease.fingerprint != evidence.pending.fingerprint
            or evidence.accepted is None
            or evidence.accepted.quest_id != evidence.pending.quest_id
            or evidence.accepted.title != evidence.pending.title
            or evidence.accepted.location != evidence.pending.location
            or evidence.accepted.giver_names != evidence.pending.giver_names
            or not same_location_name(evidence.target, evidence.pending.location)
        ):
            return "turn_in_identity_mismatch"
        return None
    if evidence.pending is None:
        return "quest_location_objective_missing"
    if (
        evidence.lease.quest_id != evidence.pending.quest_id
        or evidence.lease.title != evidence.pending.title
        or evidence.lease.fingerprint != evidence.pending.fingerprint
        or evidence.active_quest_id != evidence.pending.quest_id
        or evidence.route_link_label != evidence.navigator_label
        or evidence.target_routes != (evidence.target,)
        or evidence.target not in evidence.route_locations
    ):
        return "quest_location_identity_mismatch"
    return None


def route_binding_evidence(
    kind: object,
    target: object,
    *,
    director: object,
    intake_pending: object,
    dialogue_pending: object,
    turn_in_pending: object,
    active_quest_id: object,
    route_link_label: object,
    target_routes_by_monster: object,
    route_locations: object,
    ordered_handoff_cursor: object = None,
) -> QuestRouteBindingEvidence:
    """Adapt coordinator-owned runtime records into immutable validation evidence."""

    normalized_kind = str(kind or "").strip()
    normalized_target = str(target or "").strip()
    base = {
        "kind": normalized_kind,
        "target": normalized_target,
        "director_present": director is not None,
    }
    if director is None or normalized_kind not in QUEST_ROUTE_KINDS:
        return QuestRouteBindingEvidence(**base)
    if normalized_kind == "quest_accept":
        reference = getattr(director, "pending_accept", None)
        return QuestRouteBindingEvidence(
            **base,
            phase=_phase(intake_pending),
            pending=_identity(
                intake_pending,
                id_attr="quest_id",
                giver_names=(str(getattr(intake_pending, "giver_name", "") or ""),),
            ),
            reference=_identity(reference, id_attr="id", title_attr="title"),
        )
    chain = getattr(director, "chain", None)
    lease_record = getattr(chain, "lease", None)
    if ordered_handoff_cursor is None:
        ordered_handoff_cursor = getattr(chain, "ordered_handoff_cursor", None)
    lease = _identity(
        lease_record,
        id_attr="quest_id",
        title_attr="quest_title",
        fingerprint_attr="current_fingerprint",
    )
    if normalized_kind == "quest_ordered_handoff":
        return QuestRouteBindingEvidence(
            **base,
            pending=(
                None if ordered_handoff_cursor is None else RouteIdentity(
                    quest_id=_text(getattr(ordered_handoff_cursor, "quest_id", None)),
                    title=_text(getattr(ordered_handoff_cursor, "quest_title", None)),
                    location=_text(getattr(ordered_handoff_cursor, "target_location", None)),
                    fingerprint=_text(getattr(ordered_handoff_cursor, "fingerprint", None)),
                )
            ),
            lease=lease,
        )
    if normalized_kind == "quest_dialogue":
        objective = getattr(dialogue_pending, "objective", None)
        plan = getattr(director, "active_route_plan", None)
        return QuestRouteBindingEvidence(
            **base,
            phase=_phase(dialogue_pending),
            pending=_identity(
                objective,
                id_attr="quest_id",
                title_attr="quest_title",
                fingerprint_attr="fingerprint",
            ),
            lease=lease,
            plan_quest_id=_text(getattr(plan, "quest_id", None)),
            plan_kind=_text(getattr(getattr(plan, "kind", None), "value", None)),
        )
    if normalized_kind == "quest_turn_in":
        objective = getattr(turn_in_pending, "objective", None)
        accepted_record = getattr(lease_record, "accepted_ref", None)
        return QuestRouteBindingEvidence(
            **base,
            phase=_phase(turn_in_pending),
            pending=_identity(
                objective,
                id_attr="quest_id",
                title_attr="quest_title",
                fingerprint_attr="completed_fingerprint",
                giver_names=(str(getattr(objective, "giver_name", "") or ""),),
            ),
            lease=lease,
            accepted=_identity(accepted_record, id_attr="id", title_attr="title"),
        )
    objective = getattr(director, "active_objective", None)
    monster_name = str(getattr(getattr(objective, "monster", None), "name", "") or "")
    routes = target_routes_by_monster if isinstance(target_routes_by_monster, dict) else {}
    return QuestRouteBindingEvidence(
        **base,
        pending=_identity(
            objective,
            id_attr="quest_id",
            title_attr="quest_title",
            fingerprint_attr="fingerprint",
        ),
        lease=lease,
        active_quest_id=_text(active_quest_id),
        route_link_label=_text(route_link_label),
        navigator_label=_text(getattr(objective, "navigator_label", None)),
        target_routes=tuple(routes.get(monster_name, ())),
        route_locations=tuple(route_locations) if isinstance(route_locations, (list, tuple)) else (),
    )


def _identity(
    value: object,
    *,
    id_attr: str,
    title_attr: str = "title",
    fingerprint_attr: str | None = None,
    giver_names: tuple[str, ...] | None = None,
) -> RouteIdentity | None:
    if value is None:
        return None
    raw_givers = giver_names if giver_names is not None else getattr(value, "giver_names", ())
    return RouteIdentity(
        quest_id=_text(getattr(value, id_attr, None)),
        title=_text(getattr(value, title_attr, None)),
        location=_text(getattr(value, "location", None)),
        giver_names=tuple(str(item or "") for item in raw_givers),
        fingerprint=(
            _text(getattr(value, fingerprint_attr, None)) if fingerprint_attr is not None else None
        ),
    )


def _phase(value: object) -> str | None:
    phase = getattr(value, "phase", None)
    return _text(getattr(phase, "value", phase))


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
