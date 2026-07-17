"""Typed causal contract for durable available-quest catalogue navigation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Mapping
from urllib.parse import parse_qsl, urljoin, urlsplit


QUEST_GAME_ORIGIN = "https://3kingdoms.ru"


class CatalogNavigationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    ACK_PENDING = "ACK_PENDING"
    NOT_ISSUED = "NOT_ISSUED"


class CatalogSettleStatus(str, Enum):
    ACCEPT = "accept"
    WAIT = "wait"
    STOP_IDENTITY = "stop_identity"
    STOP_EXPIRED = "stop_expired"


@dataclass(frozen=True)
class CatalogNavigationOutcome:
    status: CatalogNavigationStatus
    mutation_issued: bool
    destination: str
    client_id: str
    before_href: str = ""
    after_href: str = ""
    shell_loaded: bool = False
    reason: str = ""

    @property
    def dispatched(self) -> bool:
        return self.status is not CatalogNavigationStatus.NOT_ISSUED


@dataclass(frozen=True)
class PendingCatalogNavigation:
    client_id: str
    profile_id: str
    tab_id: int
    mode: str
    page: int
    destination: str
    baseline_snapshot_id: str
    baseline_generated_at: float
    issued_at: float
    deadline: float

    def __post_init__(self) -> None:
        if (
            not _bounded(self.client_id, 240)
            or not _bounded(self.profile_id, 240)
            or isinstance(self.tab_id, bool)
            or not isinstance(self.tab_id, int)
            or self.tab_id < 0
            or self.mode != "avail"
            or isinstance(self.page, bool)
            or not isinstance(self.page, int)
            or not 0 <= self.page <= 100
            or not _bounded(self.destination, 500)
            or not exact_quest_catalog_destination(self.destination, self.page)
            or not _bounded(self.baseline_snapshot_id, 240)
            or not all(math.isfinite(value) for value in (
                self.baseline_generated_at, self.issued_at, self.deadline
            ))
            or self.baseline_generated_at <= 0
            or not self.baseline_generated_at <= self.issued_at < self.deadline
            or self.deadline - self.issued_at > 120
        ):
            raise ValueError("invalid pending catalog navigation")


@dataclass(frozen=True)
class CatalogSnapshotEvidence:
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


@dataclass(frozen=True)
class CatalogSettleDecision:
    status: CatalogSettleStatus
    reason: str


def make_pending_catalog_navigation(
    *,
    client_id: str,
    profile_id: str,
    tab_id: int,
    page: int,
    current_href: str,
    baseline_snapshot_id: str,
    baseline_generated_at: object,
    issued_at: float,
    settle_timeout_s: float = 20.0,
) -> PendingCatalogNavigation:
    generated = snapshot_epoch_seconds(baseline_generated_at)
    timeout = float(settle_timeout_s)
    if generated is None or not math.isfinite(timeout) or timeout < 1 or timeout > 120:
        raise ValueError("catalog navigation causal baseline is incomplete")
    destination = urljoin(current_href, f"/user_quest.php?mode=avail&page={page}")
    return PendingCatalogNavigation(
        str(client_id).strip(),
        str(profile_id).strip(),
        tab_id,
        "avail",
        page,
        destination,
        str(baseline_snapshot_id).strip(),
        generated,
        float(issued_at),
        float(issued_at) + timeout,
    )


def serialize_pending_catalog_navigation(pending: PendingCatalogNavigation) -> dict[str, object]:
    return {
        "schema": 1,
        "client_id": pending.client_id,
        "profile_id": pending.profile_id,
        "tab_id": pending.tab_id,
        "mode": pending.mode,
        "page": pending.page,
        "destination": pending.destination,
        "baseline_snapshot_id": pending.baseline_snapshot_id,
        "baseline_generated_at": pending.baseline_generated_at,
        "issued_at": pending.issued_at,
        "deadline": pending.deadline,
    }


def restore_pending_catalog_navigation(raw: object) -> PendingCatalogNavigation | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != 1 or len(raw) != 11:
        raise ValueError("invalid pending catalog navigation checkpoint")
    try:
        return PendingCatalogNavigation(
            client_id=str(raw.get("client_id") or "").strip(),
            profile_id=str(raw.get("profile_id") or "").strip(),
            tab_id=_strict_int(raw.get("tab_id")),
            mode=str(raw.get("mode") or "").strip(),
            page=_strict_int(raw.get("page")),
            destination=str(raw.get("destination") or "").strip(),
            baseline_snapshot_id=str(raw.get("baseline_snapshot_id") or "").strip(),
            baseline_generated_at=_strict_float(raw.get("baseline_generated_at")),
            issued_at=_strict_float(raw.get("issued_at")),
            deadline=_strict_float(raw.get("deadline")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid pending catalog navigation checkpoint") from exc


def parse_catalog_navigation_outcome(
    *, result_ok: bool, message: object, client_id: object, destination: str
) -> CatalogNavigationOutcome:
    parsed: object = None
    if isinstance(message, str):
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, Mapping):
        raw_status = str(parsed.get("outcome") or "").strip().upper()
        try:
            status = CatalogNavigationStatus(raw_status)
        except ValueError:
            status = CatalogNavigationStatus.ACK_PENDING if bool(parsed.get("mutationIssued")) else CatalogNavigationStatus.NOT_ISSUED
        mutation_issued = parsed.get("mutationIssued") is True
        parsed_destination = str(parsed.get("destination") or "")
        destination_matches = parsed_destination == destination
        if status is CatalogNavigationStatus.NOT_ISSUED and mutation_issued:
            status = CatalogNavigationStatus.ACK_PENDING
        if status is CatalogNavigationStatus.ACK_PENDING and not mutation_issued:
            status = CatalogNavigationStatus.NOT_ISSUED
        if status is CatalogNavigationStatus.CONFIRMED:
            already_open = (
                not mutation_issued
                and str(parsed.get("message") or "") == "quest_catalog_already_open"
                and parsed.get("shellLoaded") is True
            )
            if not result_ok or not destination_matches or (not mutation_issued and not already_open):
                status = CatalogNavigationStatus.ACK_PENDING if mutation_issued else CatalogNavigationStatus.NOT_ISSUED
            elif mutation_issued and parsed.get("shellLoaded") is not True:
                status = CatalogNavigationStatus.ACK_PENDING
        elif not destination_matches and mutation_issued:
            status = CatalogNavigationStatus.ACK_PENDING
        before = parsed.get("before")
        after = parsed.get("after")
        return CatalogNavigationOutcome(
            status,
            mutation_issued,
            parsed_destination or destination,
            str(client_id or ""),
            str(before.get("href") or "") if isinstance(before, Mapping) else "",
            str(after.get("href") or "") if isinstance(after, Mapping) else "",
            parsed.get("shellLoaded") is True,
            str(parsed.get("message") or ""),
        )
    text = str(message or "")
    if text == "injector_delivery_timeout":
        status = CatalogNavigationStatus.NOT_ISSUED
        issued = False
    elif not result_ok:
        status = CatalogNavigationStatus.ACK_PENDING
        issued = True
    else:
        status = CatalogNavigationStatus.ACK_PENDING
        issued = True
    return CatalogNavigationOutcome(status, issued, destination, str(client_id or ""), reason=text)


def settle_catalog_snapshot(
    pending: PendingCatalogNavigation,
    evidence: CatalogSnapshotEvidence,
    *,
    now: float,
) -> CatalogSettleDecision:
    try:
        now_value = float(now)
    except (TypeError, ValueError, OverflowError):
        now_value = math.nan
    if not math.isfinite(now_value):
        return CatalogSettleDecision(CatalogSettleStatus.STOP_EXPIRED, "catalog_navigation_clock_invalid")
    if (
        evidence.client_id != pending.client_id
        or evidence.profile_id != pending.profile_id
        or evidence.tab_id != pending.tab_id
    ):
        return CatalogSettleDecision(CatalogSettleStatus.STOP_IDENTITY, "catalog_navigation_identity_mismatch")
    if now_value >= pending.deadline:
        return CatalogSettleDecision(CatalogSettleStatus.STOP_EXPIRED, "catalog_navigation_settle_expired")
    fresh = (
        bool(evidence.snapshot_id)
        and evidence.snapshot_id != pending.baseline_snapshot_id
        and evidence.generated_at is not None
        and math.isfinite(evidence.generated_at)
        and evidence.generated_at >= max(pending.baseline_generated_at, pending.issued_at)
        and evidence.generated_at <= min(now_value, pending.deadline)
    )
    exact = (
        evidence.load_status == "loaded"
        and evidence.truncated is False
        and evidence.page_kind == "quests"
        and evidence.mode == pending.mode
        and evidence.page == pending.page
        and evidence.href == pending.destination
    )
    if fresh and exact:
        return CatalogSettleDecision(CatalogSettleStatus.ACCEPT, "catalog_navigation_snapshot_confirmed")
    return CatalogSettleDecision(CatalogSettleStatus.WAIT, "catalog_navigation_snapshot_pending")


def snapshot_epoch_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        return parsed if math.isfinite(parsed) and parsed > 0 else None
    except (OSError, OverflowError, ValueError):
        return None


def exact_quest_catalog_destination(value: str, page: int) -> bool:
    parsed = urlsplit(value)
    return (
        exact_game_origin_href(value)
        and f"{parsed.scheme}://{parsed.netloc}" == QUEST_GAME_ORIGIN
        and parsed.path == "/user_quest.php"
        and parse_qsl(parsed.query, keep_blank_values=True) == [("mode", "avail"), ("page", str(page))]
        and not parsed.fragment
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


def exact_game_origin_href(value: object) -> bool:
    parsed = urlsplit(str(value or ""))
    return parsed.scheme == "https" and parsed.netloc == "3kingdoms.ru"
