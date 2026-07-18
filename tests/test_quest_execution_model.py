from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.antibot_cv.automation.quest_execution_model import (
    QUEST_ROUTE_KINDS,
    MutationIntent,
    RouteKind,
    RouteLease,
    canonical_json,
)


def _lease(**overrides: object) -> RouteLease:
    values: dict[str, object] = {
        "kind": RouteKind.QUEST_AREA_OBJECT,
        "actor_id": "actor-1",
        "client_id": "client-1",
        "profile_id": "profile-1",
        "tab_id": "tab-1",
        "quest_id": "q304",
        "step_fingerprint": "step-304-blood",
        "revision": "snapshot-8",
        "generated_at": 100.0,
        "issued_at": 101.0,
        "expires_at": 120.0,
    }
    values.update(overrides)
    return RouteLease(**values)  # type: ignore[arg-type]


def _coherence(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "now": 105.0,
        "actor_id": "actor-1",
        "client_id": "client-1",
        "profile_id": "profile-1",
        "tab_id": "tab-1",
        "quest_id": "q304",
        "step_fingerprint": "step-304-blood",
        "revision": "snapshot-8",
        "max_snapshot_age_s": 10.0,
    }
    values.update(overrides)
    return values


def test_route_kinds_cover_legacy_quest_routes_and_area_object() -> None:
    assert QUEST_ROUTE_KINDS == {
        "quest_accept", "quest_dialogue", "quest_location", "quest_turn_in",
        "quest_ordered_handoff", "quest_area_object",
    }


def test_route_lease_is_immutable_deterministic_and_exactly_coherent() -> None:
    lease = _lease()
    assert lease.coherence_reason(**_coherence()) is None
    assert lease.is_coherent(**_coherence())
    assert lease.fingerprint == _lease().fingerprint
    with pytest.raises(FrozenInstanceError):
        lease.quest_id = "foreign"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"actor_id": "actor-2"}, "lease_actor_identity_mismatch"),
        ({"client_id": "client-2"}, "lease_actor_identity_mismatch"),
        ({"profile_id": "profile-2"}, "lease_actor_identity_mismatch"),
        ({"tab_id": "tab-2"}, "lease_actor_identity_mismatch"),
        ({"quest_id": "q280"}, "lease_step_identity_mismatch"),
        ({"step_fingerprint": "other"}, "lease_step_identity_mismatch"),
        ({"revision": "snapshot-9"}, "lease_revision_mismatch"),
        ({"now": 121.0}, "lease_expired_or_not_yet_valid"),
        ({"now": 99.0}, "lease_expired_or_not_yet_valid"),
        ({"max_snapshot_age_s": 4.0}, "lease_snapshot_stale"),
    ],
)
def test_route_lease_fails_closed_for_foreign_or_stale_evidence(
    change: dict[str, object], reason: str
) -> None:
    lease = _lease()
    assert lease.coherence_reason(**_coherence(**change)) == reason
    assert not lease.is_coherent(**_coherence(**change))


def test_route_lease_rejects_partial_identity_and_invalid_time_bounds() -> None:
    with pytest.raises(ValueError, match="client_id"):
        _lease(client_id="")
    with pytest.raises(ValueError, match="timestamps"):
        _lease(expires_at=101.0)
    assert _lease().coherence_reason(**_coherence(tab_id="")) == "lease_expected_identity_invalid"


def test_mutation_intent_is_pure_canonical_and_copies_nested_metadata() -> None:
    metadata = {"target": "кровь кабана", "candidate": {"id": "42"}}
    intent = MutationIntent(
        action_type="click_area_object",
        requirement_id="req-304-blood",
        step_fingerprint="step-304-blood",
        idempotency_key="run-1:stage-1",
        route_lease=_lease(),
        metadata=metadata,
    )
    metadata["candidate"]["id"] = "foreign"  # type: ignore[index]
    assert intent.metadata["candidate"] == {"id": "42"}
    with pytest.raises(TypeError):
        intent.metadata["target"] = "foreign"  # type: ignore[index]
    with pytest.raises(TypeError):
        intent.metadata["candidate"]["id"] = "foreign"  # type: ignore[index]
    assert intent.fingerprint == MutationIntent(
        action_type="click_area_object",
        requirement_id="req-304-blood",
        step_fingerprint="step-304-blood",
        idempotency_key="run-1:stage-1",
        route_lease=_lease(),
        metadata={"candidate": {"id": "42"}, "target": "кровь кабана"},
    ).fingerprint
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_mutation_intent_rejects_unsafe_or_noncanonical_fields() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        MutationIntent("click", "req", "step", "")
    with pytest.raises(TypeError, match="unsupported canonical value"):
        MutationIntent("click", "req", "step", "key", metadata={"unsafe": object()})
