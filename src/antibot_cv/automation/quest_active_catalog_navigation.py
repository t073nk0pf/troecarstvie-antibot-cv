"""Durable causal contract for active-quest catalogue navigation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping
from urllib.parse import parse_qsl, urljoin, urlsplit

from src.antibot_cv.automation.quest_catalog_navigation import (
    CatalogSettleDecision,
    CatalogSettleStatus,
    exact_game_origin_href,
    snapshot_epoch_seconds,
)


@dataclass(frozen=True)
class PendingActiveCatalogNavigation:
    client_id: str
    profile_id: str
    tab_id: int
    mode: str
    page: int
    destination: str
    baseline_href: str
    baseline_snapshot_id: str
    baseline_generated_at: float
    baseline_revision: str
    issued_at: float
    deadline: float

    def __post_init__(self) -> None:
        if (
            not _bounded(self.client_id, 240)
            or not _bounded(self.profile_id, 240)
            or isinstance(self.tab_id, bool)
            or not isinstance(self.tab_id, int)
            or self.tab_id < 0
            or self.mode != "started"
            or isinstance(self.page, bool)
            or not isinstance(self.page, int)
            or not 0 <= self.page <= 100
            or not exact_active_catalog_destination(self.destination, self.page)
            or not exact_game_origin_href(self.baseline_href)
            or not _bounded(self.baseline_snapshot_id, 240)
            or len(self.baseline_revision) > 240
            or not all(math.isfinite(value) for value in (
                self.baseline_generated_at, self.issued_at, self.deadline
            ))
            or self.baseline_generated_at <= 0
            or not self.baseline_generated_at <= self.issued_at < self.deadline
            or self.deadline - self.issued_at > 120
        ):
            raise ValueError("invalid pending active catalog navigation")

    @property
    def already_open(self) -> bool:
        return self.baseline_href == self.destination


@dataclass(frozen=True)
class ActiveCatalogSnapshotEvidence:
    client_id: str
    profile_id: str
    tab_id: int | None
    snapshot_id: str
    generated_at: float | None
    load_status: str
    truncated: bool | None
    page_kind: str
    mode: str
    page: int | None
    href: str
    revision: str = ""


def make_pending_active_catalog_navigation(
    *, client_id: str, profile_id: str, tab_id: int, page: int,
    current_href: str, baseline_snapshot_id: str, baseline_generated_at: object,
    baseline_revision: object = "", issued_at: float, settle_timeout_s: float = 20.0,
) -> PendingActiveCatalogNavigation:
    generated = snapshot_epoch_seconds(baseline_generated_at)
    timeout = float(settle_timeout_s)
    if generated is None or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError("active catalog navigation causal baseline is incomplete")
    destination = urljoin(current_href, f"/user_quest.php?mode=started&page={page}")
    return PendingActiveCatalogNavigation(
        str(client_id).strip(), str(profile_id).strip(), tab_id, "started", page,
        destination, str(current_href).strip(), str(baseline_snapshot_id).strip(),
        generated, str(baseline_revision or "").strip(), float(issued_at),
        float(issued_at) + timeout,
    )


def serialize_pending_active_catalog_navigation(
    pending: PendingActiveCatalogNavigation,
) -> dict[str, object]:
    return {
        "schema": 1, "client_id": pending.client_id, "profile_id": pending.profile_id,
        "tab_id": pending.tab_id, "mode": pending.mode, "page": pending.page,
        "destination": pending.destination, "baseline_href": pending.baseline_href,
        "baseline_snapshot_id": pending.baseline_snapshot_id,
        "baseline_generated_at": pending.baseline_generated_at,
        "baseline_revision": pending.baseline_revision, "issued_at": pending.issued_at,
        "deadline": pending.deadline,
    }


def restore_pending_active_catalog_navigation(raw: object) -> PendingActiveCatalogNavigation | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != 1 or len(raw) != 13:
        raise ValueError("invalid pending active catalog navigation checkpoint")
    try:
        return PendingActiveCatalogNavigation(
            str(raw.get("client_id") or "").strip(), str(raw.get("profile_id") or "").strip(),
            _strict_int(raw.get("tab_id")), str(raw.get("mode") or "").strip(),
            _strict_int(raw.get("page")), str(raw.get("destination") or "").strip(),
            str(raw.get("baseline_href") or "").strip(),
            str(raw.get("baseline_snapshot_id") or "").strip(),
            _strict_float(raw.get("baseline_generated_at")),
            str(raw.get("baseline_revision") or "").strip(),
            _strict_float(raw.get("issued_at")), _strict_float(raw.get("deadline")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid pending active catalog navigation checkpoint") from exc


def settle_active_catalog_snapshot(
    pending: PendingActiveCatalogNavigation,
    evidence: ActiveCatalogSnapshotEvidence,
    *, now: float,
) -> CatalogSettleDecision:
    try:
        now_value = float(now)
    except (TypeError, ValueError, OverflowError):
        now_value = math.nan
    if not math.isfinite(now_value):
        return CatalogSettleDecision(CatalogSettleStatus.STOP_EXPIRED, "active_catalog_navigation_clock_invalid")
    if (evidence.client_id, evidence.profile_id, evidence.tab_id) != (
        pending.client_id, pending.profile_id, pending.tab_id
    ):
        return CatalogSettleDecision(CatalogSettleStatus.STOP_IDENTITY, "active_catalog_navigation_identity_mismatch")
    if now_value >= pending.deadline:
        return CatalogSettleDecision(CatalogSettleStatus.STOP_EXPIRED, "active_catalog_navigation_settle_expired")
    fresh = (
        bool(evidence.snapshot_id) and evidence.snapshot_id != pending.baseline_snapshot_id
        and evidence.generated_at is not None and math.isfinite(evidence.generated_at)
        and pending.issued_at <= evidence.generated_at <= min(now_value, pending.deadline)
    )
    exact = (
        evidence.load_status == "loaded" and evidence.truncated is False
        and evidence.page_kind == "quests" and evidence.mode == "started"
        and evidence.page == pending.page and evidence.href == pending.destination
    )
    # A newly wrapped snapshot can still contain the old DOM.  A URL transition
    # is causal proof for normal navigation; already-open pages need a document
    # revision explicitly supplied by the observer.
    revision_proven = not pending.already_open or (
        bool(evidence.revision) and evidence.revision != pending.baseline_revision
    )
    if fresh and exact and revision_proven:
        return CatalogSettleDecision(CatalogSettleStatus.ACCEPT, "active_catalog_navigation_snapshot_confirmed")
    return CatalogSettleDecision(CatalogSettleStatus.WAIT, "active_catalog_navigation_snapshot_pending")


def exact_active_catalog_destination(value: str, page: int) -> bool:
    parsed = urlsplit(value)
    return (
        exact_game_origin_href(value) and parsed.path == "/user_quest.php"
        and parse_qsl(parsed.query, keep_blank_values=True) == [
            ("mode", "started"), ("page", str(page))
        ] and not parsed.fragment
    )


def _bounded(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("integer required")
    return value


def _strict_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("number required")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("finite number required")
    return parsed
