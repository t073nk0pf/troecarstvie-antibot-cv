"""Causal aggregation of active-catalogue pages into turn-in authority.

This module is deliberately observation-only.  A catalogue page contributes
to authority only when it is paired with the exact navigation that produced
it and with an accepted :class:`ActiveCatalogSnapshotEvidence` envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from .quest_active_catalog import (
    ActiveQuestCatalogAccumulator,
    ActiveQuestCatalogError,
    ActiveQuestEntry,
)
from .quest_active_catalog_navigation import (
    ActiveCatalogSnapshotEvidence,
    PendingActiveCatalogNavigation,
    settle_active_catalog_snapshot,
)
from .quest_catalog_navigation import CatalogSettleStatus, snapshot_epoch_seconds
from .quest_turnin_admission import ActiveCatalogAuthority


class ActiveCatalogAuthorityError(ValueError):
    """Raised when a page cannot safely contribute to catalogue authority."""


@dataclass(frozen=True, slots=True)
class _AcceptedPage:
    snapshot_id: str
    generated_at: float
    document_revision: str


@dataclass(frozen=True, slots=True)
class ActiveCatalogObservation:
    """Complete causal catalogue before semantic plan/lease binding."""

    entries: tuple[ActiveQuestEntry, ...]
    snapshot_id: str
    generated_at: float
    client_id: str
    profile_id: str
    tab_id: str
    causal_baseline: str
    causal_ancestors: tuple[str, ...]


class ActiveCatalogAuthorityAccumulator:
    """Build one immutable authority from a causally linked page sequence."""

    def __init__(
        self,
        *,
        causal_baseline: str,
        causal_ancestors: tuple[str, ...] = (),
        max_age_seconds: float,
        max_pages: int = 20,
        max_items: int = 500,
    ) -> None:
        if (
            not _bounded(causal_baseline)
            or isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(float(max_age_seconds))
            or max_age_seconds <= 0
        ):
            raise ValueError("invalid active catalogue authority context")
        ancestors = tuple(dict.fromkeys(causal_ancestors))
        if any(not _bounded(item) for item in ancestors):
            raise ValueError("invalid active catalogue causal ancestors")
        self._causal_baseline = causal_baseline
        self._causal_ancestors = ancestors
        self._max_age_seconds = float(max_age_seconds)
        self._catalog = ActiveQuestCatalogAccumulator(
            max_pages=max_pages, max_items=max_items,
        )
        self._identity: tuple[str, str, int] | None = None
        self._pages: list[_AcceptedPage] = []
        self._observation: ActiveCatalogObservation | None = None
        self._authority: ActiveCatalogAuthority | None = None

    @property
    def complete(self) -> bool:
        return self._observation is not None

    @property
    def next_page(self) -> int | None:
        return None if self.complete else self._catalog.next_page

    @property
    def authority(self) -> ActiveCatalogAuthority:
        if self._authority is None:
            raise ActiveCatalogAuthorityError("active catalogue authority is not sealed")
        return self._authority

    @property
    def observation(self) -> ActiveCatalogObservation:
        if self._observation is None:
            raise ActiveCatalogAuthorityError("active catalogue observation is incomplete")
        return self._observation

    def ingest(
        self,
        pending: PendingActiveCatalogNavigation,
        evidence: ActiveCatalogSnapshotEvidence,
        snapshot_page: object,
        *,
        now: float,
    ) -> ActiveCatalogObservation | None:
        """Accept one page, returning an unbound observation on completion."""

        if self.complete:
            raise ActiveCatalogAuthorityError("active catalogue authority is already complete")
        now_value = _finite_number(now, "now")
        if pending.page != self._catalog.next_page:
            raise ActiveCatalogAuthorityError("active catalogue pages must be sequential")
        identity = (pending.client_id, pending.profile_id, pending.tab_id)
        if self._identity is not None and identity != self._identity:
            raise ActiveCatalogAuthorityError("active catalogue identity changed")
        decision = settle_active_catalog_snapshot(pending, evidence, now=now_value)
        if decision.status is not CatalogSettleStatus.ACCEPT:
            raise ActiveCatalogAuthorityError(
                f"active catalogue evidence is not accepted: {decision.reason}"
            )
        page = _validated_pair(snapshot_page, pending, evidence)
        if now_value - page.generated_at > self._max_age_seconds:
            raise ActiveCatalogAuthorityError("active catalogue page is stale")
        if self._pages:
            previous = self._pages[-1]
            if pending.baseline_snapshot_id != previous.snapshot_id:
                raise ActiveCatalogAuthorityError("active catalogue navigation chain is broken")
            if page.generated_at <= previous.generated_at:
                raise ActiveCatalogAuthorityError("active catalogue generation did not advance")
            if page.document_revision == previous.document_revision:
                raise ActiveCatalogAuthorityError("active catalogue document generation did not advance")

        try:
            result = self._catalog.ingest(snapshot_page)
        except ActiveQuestCatalogError as exc:
            raise ActiveCatalogAuthorityError(str(exc)) from exc
        self._identity = identity
        self._pages.append(page)
        if result is None:
            return None
        if now_value - self._pages[0].generated_at > self._max_age_seconds:
            raise ActiveCatalogAuthorityError("active catalogue page set is stale")
        self._observation = ActiveCatalogObservation(
            entries=result,
            snapshot_id=page.snapshot_id,
            generated_at=page.generated_at,
            client_id=identity[0],
            profile_id=identity[1],
            tab_id=str(identity[2]),
            causal_baseline=self._causal_baseline,
            causal_ancestors=self._causal_ancestors,
        )
        return self._observation

    def seal(
        self,
        *,
        plan_fingerprint: str,
        lease_fingerprint: str,
        catalog_revision: int,
    ) -> ActiveCatalogAuthority:
        """Bind a complete observation to the compiled plan exactly once."""

        if self._observation is None:
            raise ActiveCatalogAuthorityError("active catalogue observation is incomplete")
        if self._authority is not None:
            raise ActiveCatalogAuthorityError("active catalogue authority is already sealed")
        if (
            not _bounded(plan_fingerprint)
            or not _bounded(lease_fingerprint)
            or isinstance(catalog_revision, bool)
            or not isinstance(catalog_revision, int)
            or catalog_revision < 0
        ):
            raise ActiveCatalogAuthorityError("invalid active catalogue seal")
        observation = self._observation
        self._authority = ActiveCatalogAuthority(
            entries=observation.entries,
            revision=max(catalog_revision, int(observation.generated_at * 1000)),
            snapshot_id=observation.snapshot_id,
            generated_at=observation.generated_at,
            max_age_seconds=self._max_age_seconds,
            client_id=observation.client_id,
            profile_id=observation.profile_id,
            tab_id=observation.tab_id,
            causal_baseline=observation.causal_baseline,
            causal_ancestors=observation.causal_ancestors,
            plan_fingerprint=plan_fingerprint,
            lease_fingerprint=lease_fingerprint,
            complete=True,
        )
        return self._authority


def _validated_pair(
    raw: object,
    pending: PendingActiveCatalogNavigation,
    evidence: ActiveCatalogSnapshotEvidence,
) -> _AcceptedPage:
    if not isinstance(raw, Mapping):
        raise ActiveCatalogAuthorityError("active catalogue page must be an object")
    generated = snapshot_epoch_seconds(raw.get("generatedAt"))
    revision = raw.get("documentRevision")
    if revision is None:
        revision = raw.get("navigationRevision")
    exact = (
        raw.get("snapshotId") == evidence.snapshot_id
        and generated == evidence.generated_at
        and raw.get("loadStatus") == "loaded"
        and raw.get("truncated") is False
        and raw.get("mode") == "started"
        and raw.get("currentPage") == pending.page == evidence.page
        and raw.get("href") == pending.destination == evidence.href
        and isinstance(revision, str)
        and bool(revision.strip())
        and revision.strip() == evidence.revision
    )
    if not exact or generated is None:
        raise ActiveCatalogAuthorityError("active catalogue page/evidence pair mismatch")
    page_count = raw.get("pageCount")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count <= 0:
        raise ActiveCatalogAuthorityError("active catalogue page count is invalid")
    return _AcceptedPage(evidence.snapshot_id, generated, revision.strip())


def _bounded(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 240


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveCatalogAuthorityError(f"{label} must be finite")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ActiveCatalogAuthorityError(f"{label} must be finite")
    return parsed


__all__ = [
    "ActiveCatalogAuthorityAccumulator", "ActiveCatalogAuthorityError",
    "ActiveCatalogObservation",
]
