"""Typed contracts for exact, observation-only NPC census inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CensusInspectContract:
    snapshot_id: str
    location_id: str
    endpoint_id: str
    route_ref: str
    endpoint_name: str
    observation_epoch: str
    observation_revision: int
    generated_at: str

    def __post_init__(self) -> None:
        _text(self.snapshot_id, 160)
        _identity(self.location_id, positive=True)
        _identity(self.endpoint_id, positive=False)
        _identity(self.route_ref, positive=True)
        _text(self.endpoint_name, 180)
        _base36(self.observation_epoch)
        _revision(self.observation_revision)
        _timestamp(self.generated_at)
        if self.snapshot_id != (
            f"area-npcs-{self.observation_epoch}-{_to_base36(self.observation_revision)}"
        ):
            raise ValueError("census area snapshot identity mismatches baseline")

    @classmethod
    def from_payload(cls, payload: object) -> "CensusInspectContract":
        if not isinstance(payload, Mapping):
            raise ValueError("census inspect payload is invalid")
        return cls(
            snapshot_id=_text(payload.get("expectedSnapshotId"), 160),
            location_id=_identity(payload.get("expectedLocationId"), positive=True),
            endpoint_id=_identity(payload.get("npcId"), positive=False),
            route_ref=_identity(payload.get("expectedRouteRef"), positive=True),
            endpoint_name=_text(payload.get("expectedName"), 180),
            observation_epoch=_base36(payload.get("expectedObservationEpoch")),
            observation_revision=_revision(payload.get("expectedObservationRevision")),
            generated_at=_text(payload.get("expectedGeneratedAt"), 64),
        )

    def action_metadata(self) -> dict[str, object]:
        return {
            "expected_snapshot_id": self.snapshot_id,
            "expected_location_id": self.location_id,
            "npc_id": self.endpoint_id,
            "expected_route_ref": self.route_ref,
            "expected_name": self.endpoint_name,
        }


def inspection_snapshot_matches(
    contract: CensusInspectContract, snapshot: object,
) -> bool:
    if (
        not isinstance(contract, CensusInspectContract)
        or not isinstance(snapshot, Mapping)
        or snapshot.get("ok") is not True
        or snapshot.get("message") != "npc_dialog_snapshot"
        or snapshot.get("pageKind") != "npc"
        or snapshot.get("truncated") is not False
        or snapshot.get("identityMatches") is not True
    ):
        return False
    try:
        endpoint_id = _identity(snapshot.get("npcId"), positive=False)
        _text(snapshot.get("resultingName"), 180)
        epoch = _base36(snapshot.get("observationEpoch"))
        revision = _revision(snapshot.get("observationRevision"))
        generated_at = _text(snapshot.get("generatedAt"), 64)
        snapshot_id = _text(snapshot.get("snapshotId"), 160)
        raw_instance = snapshot.get("npcInstanceId")
        if raw_instance not in (None, ""):
            _identity(raw_instance, positive=True)
    except ValueError:
        return False
    try:
        after_time = _timestamp(generated_at)
        before_time = _timestamp(contract.generated_at)
    except ValueError:
        return False
    return (
        endpoint_id == contract.endpoint_id
        and snapshot_id == f"npc-dialog-{epoch}-{_to_base36(revision)}"
        and epoch == contract.observation_epoch
        and revision > contract.observation_revision
        and after_time.tzinfo is not None
        and before_time.tzinfo is not None
        and after_time > before_time
    )


def newer_same_area_authority(
    contract: CensusInspectContract, snapshot: object,
) -> bool:
    """Prove that an unresolved inspection returned to its bound area."""

    if (
        not isinstance(contract, CensusInspectContract)
        or not isinstance(snapshot, Mapping)
        or snapshot.get("ok") is not True
        or snapshot.get("message") != "area_npc_snapshot"
        or snapshot.get("pageKind") != "area"
        or snapshot.get("truncated") is not False
    ):
        return False
    location = snapshot.get("location")
    if not isinstance(location, Mapping):
        return False
    try:
        location_id = _identity(location.get("id"), positive=True)
        epoch = _base36(snapshot.get("observationEpoch"))
        revision = _revision(snapshot.get("observationRevision"))
        generated_at = _text(snapshot.get("generatedAt"), 64)
        snapshot_id = _text(snapshot.get("snapshotId"), 160)
        after_time = _timestamp(generated_at)
        before_time = _timestamp(contract.generated_at)
    except ValueError:
        return False
    return (
        location_id == contract.location_id
        and snapshot_id == f"area-npcs-{epoch}-{_to_base36(revision)}"
        and (epoch != contract.observation_epoch or revision > contract.observation_revision)
        and after_time > before_time
    )


def _identity(value: object, *, positive: bool) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 16
        or int(value) > 9_007_199_254_740_991
        or str(int(value)) != value
        or (int(value) <= 0 if positive else int(value) < 0)
    ):
        raise ValueError("census inspect numeric identity is invalid")
    return value


def _text(value: object, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("census inspect text is invalid")
    return value


def _base36(value: object) -> str:
    if (
        not isinstance(value, str) or not value or value != value.lower()
        or any(char not in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)
        or len(value) > 16 or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError("census observation epoch is invalid")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 9_007_199_254_740_991:
        raise ValueError("census observation revision is invalid")
    return value


def _timestamp(value: str):
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("census observation timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("census observation timestamp is invalid")
    return parsed


def _to_base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result or "0"
