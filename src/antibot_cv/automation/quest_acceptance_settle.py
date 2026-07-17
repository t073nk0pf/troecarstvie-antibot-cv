"""Fail-closed settle policy for post-navigation quest-giver observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.antibot_cv.automation.quest_giver import (
    QuestGiverAmbiguousError,
    QuestGiverMissingError,
    resolve_unique_giver,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name, snapshot_epoch_seconds


class AcceptanceSettleIntent(str, Enum):
    READY = "READY"
    WAIT = "WAIT"
    STOP_UNSAFE = "STOP_UNSAFE"


@dataclass(frozen=True)
class AcceptanceSettleDecision:
    intent: AcceptanceSettleIntent
    reason: str
    snapshot_id: str | None = None


def acceptance_client_matches(expected: object, observed: object, *, dry_run: bool) -> bool:
    """Require exact live provenance; allow only an explicit null/null dry-run contract."""

    if dry_run and expected is None and observed is None:
        return True
    return (
        isinstance(expected, str)
        and bool(expected.strip())
        and isinstance(observed, str)
        and bool(observed.strip())
        and observed == expected
    )


def assess_acceptance_area_snapshot(
    snapshot: object,
    *,
    expected_location: str,
    expected_giver: str,
    previous_snapshot_id: str | None,
    area_opened_epoch: float,
) -> AcceptanceSettleDecision:
    """Classify incomplete evidence separately from contradictions and ambiguity."""

    if not _bounded_text(expected_location, 220) or not _bounded_text(expected_giver, 180):
        return _stop("quest_accept_expected_identity_invalid")
    if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
        return _wait("quest_accept_npc_snapshot_unavailable")
    if snapshot.get("truncated") is not False:
        return _wait("quest_accept_npc_snapshot_truncated")
    snapshot_id = _bounded_text(snapshot.get("snapshotId"), 120)
    if not snapshot_id.startswith("area-npcs-"):
        return _wait("quest_accept_npc_snapshot_identity_pending")
    if previous_snapshot_id and snapshot_id == previous_snapshot_id:
        return _wait("quest_accept_npc_snapshot_not_fresh", snapshot_id)
    generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    if generated_at is None:
        return _wait("quest_accept_npc_snapshot_time_pending", snapshot_id)
    if generated_at < area_opened_epoch:
        return _wait("quest_accept_npc_snapshot_before_area_open", snapshot_id)
    location = snapshot.get("location")
    if not isinstance(location, dict):
        return _wait("quest_accept_location_identity_pending", snapshot_id)
    location_name = _bounded_text(location.get("name"), 220)
    if location_name and not same_location_name(location_name, expected_location):
        return _stop("quest_accept_location_mismatch", snapshot_id)
    if not location_name:
        return _wait("quest_accept_location_identity_pending", snapshot_id)
    location_id = _bounded_text(location.get("id"), 80)
    if not location_id:
        return _wait("quest_accept_location_identity_pending", snapshot_id)
    if not location_id.isdecimal() or int(location_id) <= 0:
        return _stop("quest_accept_location_identity_invalid", snapshot_id)
    items = snapshot.get("items")
    if not isinstance(items, list):
        return _wait("quest_accept_npc_items_pending", snapshot_id)
    try:
        actor = resolve_unique_giver(expected_giver, items)
    except QuestGiverMissingError:
        return _wait("quest_accept_giver_not_observed", snapshot_id)
    except QuestGiverAmbiguousError:
        return _stop("quest_accept_giver_ambiguous", snapshot_id)
    except ValueError:
        return _stop("quest_accept_giver_identity_invalid", snapshot_id)
    actor_id = _bounded_text(actor.get("dataId"), 80)
    actor_name = _bounded_text(actor.get("name"), 180)
    if not actor_id.isdecimal() or int(actor_id) <= 0 or not actor_name:
        return _stop("quest_accept_giver_identity_invalid", snapshot_id)
    return AcceptanceSettleDecision(AcceptanceSettleIntent.READY, "quest_accept_giver_ready", snapshot_id)


def _bounded_text(value: object, max_length: int) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > max_length:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


def _wait(reason: str, snapshot_id: str | None = None) -> AcceptanceSettleDecision:
    return AcceptanceSettleDecision(AcceptanceSettleIntent.WAIT, reason, snapshot_id)


def _stop(reason: str, snapshot_id: str | None = None) -> AcceptanceSettleDecision:
    return AcceptanceSettleDecision(AcceptanceSettleIntent.STOP_UNSAFE, reason, snapshot_id)
