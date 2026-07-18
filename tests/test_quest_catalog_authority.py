from __future__ import annotations

from dataclasses import replace

import pytest

from src.antibot_cv.automation.quest_active_catalog_navigation import (
    ActiveCatalogSnapshotEvidence,
    make_pending_active_catalog_navigation,
)
from src.antibot_cv.automation.quest_catalog_authority import (
    ActiveCatalogAuthorityAccumulator,
    ActiveCatalogAuthorityError,
)


def _pending(page: int, *, baseline: str, issued_at: float, identity=("client", "profile", 7)):
    return make_pending_active_catalog_navigation(
        client_id=identity[0], profile_id=identity[1], tab_id=identity[2], page=page,
        current_href="https://3kingdoms.ru/main.php",
        baseline_snapshot_id=baseline, baseline_generated_at=issued_at - 0.1,
        baseline_revision=f"before-{page}", issued_at=issued_at,
    )


def _page(page: int, *, snapshot_id: str, generated_at: float, page_count=2, revision=None):
    href = f"https://3kingdoms.ru/user_quest.php?mode=started&page={page}"
    raw = {
        "snapshotId": snapshot_id, "generatedAt": generated_at,
        "documentRevision": revision or f"doc-{page}",
        "loadStatus": "loaded", "truncated": False, "pageKind": "quests",
        "mode": "started", "currentPage": page, "pageCount": page_count,
        "hasNextPage": page + 1 < page_count, "href": href,
        "items": [{"id": str(280 + page), "title": f"Quest {page}", "status": "active"}],
    }
    evidence = ActiveCatalogSnapshotEvidence(
        "client", "profile", 7, snapshot_id, generated_at, "loaded", False,
        "quests", "started", page, href, raw["documentRevision"],
    )
    return raw, evidence


def _accumulator(**changes):
    values = dict(
        causal_baseline="evaluation-9", max_age_seconds=5.0,
    )
    values.update(changes)
    return ActiveCatalogAuthorityAccumulator(**values)


def test_terminal_complete_sequence_emits_observation_then_seals_authority() -> None:
    accumulator = _accumulator(causal_ancestors=("prior-evaluation",))
    page0, evidence0 = _page(0, snapshot_id="snap-0", generated_at=100.1)
    page1, evidence1 = _page(1, snapshot_id="snap-1", generated_at=101.1)

    assert accumulator.ingest(_pending(0, baseline="root", issued_at=100.0), evidence0, page0, now=100.2) is None
    observation = accumulator.ingest(
        _pending(1, baseline="snap-0", issued_at=101.0), evidence1, page1, now=101.2,
    )
    assert observation is accumulator.observation
    assert observation.snapshot_id == "snap-1"
    assert tuple(entry.id for entry in observation.entries) == ("280", "281")

    authority = accumulator.seal(
        plan_fingerprint="plan", lease_fingerprint="lease", catalog_revision=12,
    )

    assert authority is accumulator.authority
    assert authority.complete is True
    assert authority.snapshot_id == "snap-1"
    assert authority.revision == 101100
    assert authority.causal_baseline == "evaluation-9"
    assert authority.causal_ancestors == ("prior-evaluation",)
    assert authority.client_id == "client"
    assert authority.profile_id == "profile"
    assert authority.tab_id == "7"
    assert tuple(entry.id for entry in authority.entries) == ("280", "281")


def test_partial_set_never_exposes_authority() -> None:
    accumulator = _accumulator()
    page, evidence = _page(0, snapshot_id="snap-0", generated_at=100.1)

    assert accumulator.ingest(_pending(0, baseline="root", issued_at=100.0), evidence, page, now=100.2) is None
    assert accumulator.complete is False
    with pytest.raises(ActiveCatalogAuthorityError, match="incomplete"):
        _ = accumulator.observation
    with pytest.raises(ActiveCatalogAuthorityError, match="incomplete"):
        accumulator.seal(plan_fingerprint="plan", lease_fingerprint="lease", catalog_revision=1)


def test_seal_is_exact_and_once_only() -> None:
    accumulator = _accumulator()
    page, evidence = _page(0, snapshot_id="snap-0", generated_at=100.1, page_count=1)
    accumulator.ingest(_pending(0, baseline="root", issued_at=100.0), evidence, page, now=100.2)

    with pytest.raises(ActiveCatalogAuthorityError, match="invalid.*seal"):
        accumulator.seal(plan_fingerprint="", lease_fingerprint="lease", catalog_revision=1)
    with pytest.raises(ActiveCatalogAuthorityError, match="invalid.*seal"):
        accumulator.seal(plan_fingerprint="plan", lease_fingerprint="lease", catalog_revision=True)
    authority = accumulator.seal(
        plan_fingerprint="plan", lease_fingerprint="lease", catalog_revision=1,
    )
    assert authority.plan_fingerprint == "plan"
    with pytest.raises(ActiveCatalogAuthorityError, match="already sealed"):
        accumulator.seal(plan_fingerprint="plan", lease_fingerprint="lease", catalog_revision=1)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda raw, ev: (raw, replace(ev, client_id="foreign")), "not accepted"),
        (lambda raw, ev: ({**raw, "snapshotId": "other"}, ev), "pair mismatch"),
        (lambda raw, ev: ({**raw, "hasNextPage": False}, ev), "next-page signal"),
        (lambda raw, ev: ({**raw, "documentRevision": "other"}, ev), "pair mismatch"),
    ],
)
def test_foreign_or_mismatched_page_evidence_fails_closed(mutation, reason) -> None:
    accumulator = _accumulator()
    raw, evidence = _page(0, snapshot_id="snap-0", generated_at=100.1)
    raw, evidence = mutation(raw, evidence)

    with pytest.raises(ActiveCatalogAuthorityError, match=reason):
        accumulator.ingest(_pending(0, baseline="root", issued_at=100.0), evidence, raw, now=100.2)


@pytest.mark.parametrize("change", [
    {"baseline": "not-snap-0"},
    {"generated_at": 100.1},
    {"revision": "doc-0"},
])
def test_second_page_requires_causal_snapshot_and_document_advance(change) -> None:
    accumulator = _accumulator()
    page0, evidence0 = _page(0, snapshot_id="snap-0", generated_at=100.1)
    accumulator.ingest(_pending(0, baseline="root", issued_at=100.0), evidence0, page0, now=100.2)
    generated_at = change.get("generated_at", 101.1)
    page1, evidence1 = _page(
        1, snapshot_id="snap-1", generated_at=generated_at,
        revision=change.get("revision"),
    )
    pending = _pending(1, baseline=change.get("baseline", "snap-0"), issued_at=101.0)

    with pytest.raises(ActiveCatalogAuthorityError):
        accumulator.ingest(pending, evidence1, page1, now=101.2)


def test_stale_page_and_nonsequential_page_never_emit_authority() -> None:
    stale = _accumulator(max_age_seconds=1.0)
    raw, evidence = _page(0, snapshot_id="snap-0", generated_at=100.1)
    with pytest.raises(ActiveCatalogAuthorityError, match="stale"):
        stale.ingest(_pending(0, baseline="root", issued_at=100.0), evidence, raw, now=102.0)

    skipped = _accumulator()
    raw1, evidence1 = _page(1, snapshot_id="snap-1", generated_at=101.1)
    with pytest.raises(ActiveCatalogAuthorityError, match="sequential"):
        skipped.ingest(_pending(1, baseline="root", issued_at=101.0), evidence1, raw1, now=101.2)


def test_boolean_freshness_cannot_construct_or_complete_authority() -> None:
    with pytest.raises(TypeError):
        ActiveCatalogAuthorityAccumulator(active_snapshot_fresh=True)  # type: ignore[call-arg]

    accumulator = _accumulator()
    assert accumulator.complete is False
