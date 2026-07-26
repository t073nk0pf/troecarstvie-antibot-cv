from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from src.antibot_cv.automation.quest_active_catalog_navigation import (
    ActiveCatalogSnapshotEvidence,
    make_pending_active_catalog_navigation,
)
from src.antibot_cv.automation.quest_catalog_authority import (
    ActiveCatalogAuthorityAccumulator,
)
from src.antibot_cv.automation.quest_compiler import compile_quest_plan
from src.antibot_cv.automation.quest_catalog_authority_runtime import (
    QuestCatalogAuthorityRuntimeMixin,
)
from src.antibot_cv.automation.quest_runtime import QuestRuntimeMixin


Q280_OBJECTIVE = "Вернитесь к разбойнику Аскорду в Земли Пращуров."


def _complete_q280_accumulator() -> ActiveCatalogAuthorityAccumulator:
    now = time.time()
    pending = make_pending_active_catalog_navigation(
        client_id="client",
        profile_id="profile",
        tab_id=7,
        page=0,
        current_href="https://3kingdoms.ru/main.php",
        baseline_snapshot_id="evaluation-baseline",
        baseline_generated_at=now - 0.2,
        baseline_revision="before",
        issued_at=now - 0.1,
    )
    raw = {
        "snapshotId": "started-1",
        "generatedAt": now - 0.05,
        "documentRevision": "document-1",
        "loadStatus": "loaded",
        "truncated": False,
        "pageKind": "quests",
        "mode": "started",
        "currentPage": 0,
        "pageCount": 1,
        "hasNextPage": False,
        "href": pending.destination,
        "items": [{
            "id": "280",
            "title": "Фамильная ступка",
            "status": "active",
            "objective": Q280_OBJECTIVE,
            "objectiveKind": "dialogue",
            "navigation": [{
                "text": "Земли Пращуров",
                "target": "Земли Пращуров",
            }],
            "progress": None,
        }],
    }
    evidence = ActiveCatalogSnapshotEvidence(
        "client",
        "profile",
        7,
        "started-1",
        now - 0.05,
        "loaded",
        False,
        "quests",
        "started",
        0,
        pending.destination,
        "document-1",
    )
    accumulator = ActiveCatalogAuthorityAccumulator(
        causal_baseline="evaluation-baseline",
        max_age_seconds=5.0,
    )
    assert accumulator.ingest(pending, evidence, raw, now=now) is not None
    return accumulator


def _runtime(*, quest_id: str = "280", quest_title: str = "Фамильная ступка"):
    accumulator = _complete_q280_accumulator()
    entry = accumulator.observation.entries[0]
    plan = compile_quest_plan(entry).plan
    assert plan is not None
    runtime = object.__new__(QuestRuntimeMixin)
    runtime.config = SimpleNamespace(
        leveling=SimpleNamespace(quest_engine_mode="q280_q304")
    )
    runtime._quest_active_catalog_authority_accumulator = accumulator
    runtime._quest_active_catalog_authority = None
    runtime._quest_director = SimpleNamespace(
        chain=SimpleNamespace(
            lease=SimpleNamespace(
                quest_id=quest_id,
                quest_title=quest_title,
                current_fingerprint="lease-step-fingerprint",
            )
        ),
        active_catalog=SimpleNamespace(revision=12),
    )
    return runtime, plan


def test_complete_causal_catalog_is_sealed_to_exact_q280_lease() -> None:
    runtime, plan = _runtime()

    runtime._seal_quest_active_catalog_authority()

    authority = runtime._quest_active_catalog_authority
    assert authority is not None
    assert authority.plan_fingerprint == plan.fingerprint
    assert authority.lease_fingerprint == "lease-step-fingerprint"
    assert authority.revision == int(authority.generated_at * 1000)
    assert authority.causal_baseline == "evaluation-baseline"


def test_changed_q280_identity_stops_sealing_unsafe() -> None:
    runtime, _ = _runtime(quest_title="Подменённый заголовок")

    with pytest.raises(ValueError, match="identity mismatch"):
        runtime._seal_quest_active_catalog_authority()

    assert runtime._quest_active_catalog_authority is None


def test_non_vertical_slice_lease_never_receives_turnin_authority() -> None:
    runtime, _ = _runtime(quest_id="31", quest_title="Поиски Рокоша")

    runtime._seal_quest_active_catalog_authority()

    assert runtime._quest_active_catalog_authority is None


def test_legacy_mode_does_not_require_or_seal_semantic_authority() -> None:
    runtime = object.__new__(QuestRuntimeMixin)
    runtime.config = SimpleNamespace(leveling=SimpleNamespace(quest_engine_mode="legacy"))
    runtime._quest_active_catalog_authority = object()
    runtime._quest_active_catalog_authority_accumulator = None
    runtime._quest_director = None

    runtime._seal_quest_active_catalog_authority()

    assert runtime._quest_active_catalog_authority is None


def test_quest_runtime_uses_focused_catalog_authority_mixin() -> None:
    assert issubclass(QuestRuntimeMixin, QuestCatalogAuthorityRuntimeMixin)


def test_record_page_fails_closed_without_navigation() -> None:
    runtime, _ = _runtime()

    reason = runtime._record_quest_active_catalog_authority_page(
        page=0,
        pending_navigation=None,
        evidence=None,
        quest_data={},
    )

    assert reason == "quest_active_catalog_authority_navigation_missing"


def test_final_refresh_initializes_accumulator_with_prior_semantic_lineage(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAccumulator:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def ingest(self, *args: object, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_catalog_authority_runtime.ActiveCatalogAuthorityAccumulator",
        FakeAccumulator,
    )
    runtime, _ = _runtime()
    runtime.config.leveling.snapshot_stale_timeout_ms = 5_000
    runtime.config.leveling.quest_catalog_max_pages = 20
    runtime._quest_semantic_evaluation_context = SimpleNamespace(
        causal_baseline="prior-semantic-baseline",
    )
    pending = SimpleNamespace(baseline_snapshot_id="final-refresh-baseline")

    assert runtime._record_quest_active_catalog_authority_page(
        page=0,
        pending_navigation=pending,
        evidence=object(),
        quest_data={},
        now=100.0,
    ) is None
    assert captured["causal_baseline"] == "final-refresh-baseline"
    assert captured["causal_ancestors"] == ("prior-semantic-baseline",)
