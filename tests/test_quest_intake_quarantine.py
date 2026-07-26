from __future__ import annotations

import json

import pytest

from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_director_policy import QuestDirectorIntent, QuestRef
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime
from src.antibot_cv.automation.quest_intake_quarantine import (
    INTAKE_CAPABILITY_VERSION,
    intake_ref_fingerprint,
)
from src.antibot_cv.automation.quest_intake_runtime import QuestIntakeRuntime
from src.antibot_cv.automation.quest_npc_open_navigation import make_pending_npc_open


def _ref(
    quest_id: str = "263",
    *,
    title: str = "Милость Громовержца",
    location: str = "Пригород Арсы",
    giver: str = "Волхв Вяземайт",
    accept_ref: str | None = None,
    catalog_page: int | None = 0,
) -> QuestRef:
    return QuestRef(
        quest_id,
        title,
        accept_ref,
        location,
        (giver,),
        catalog_page,
    )


def _available(quest_ref: QuestRef, *, page: int = 0) -> dict[str, object]:
    return {
        "id": quest_ref.id,
        "title": quest_ref.title,
        "status": "available",
        "description": "Work",
        "reward": "XP",
        "locationText": quest_ref.location,
        "giverNames": list(quest_ref.giver_names),
        "navigation": [{"text": quest_ref.location}],
        "catalogPage": page,
        "cardIndex": int(quest_ref.id),
    }


def _catalog(*items: dict[str, object]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "avail",
        "currentPage": 0,
        "pageCount": 1,
        "hasNextPage": False,
        "items": list(items),
        "truncated": False,
    }


def _active_catalog(*items: dict[str, object]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "started",
        "currentPage": 0,
        "pageCount": 1,
        "hasNextPage": False,
        "items": list(items),
        "truncated": False,
    }


def _active(quest_ref: QuestRef) -> dict[str, object]:
    return {
        "id": quest_ref.id,
        "title": quest_ref.title,
        "status": "active",
        "objective": "Убивая Кабанов-секачей, получите трофей.",
        "navigation": [
            {"text": "Кабанов-секачей", "target": "Кабан-секач [5]"},
            {"text": quest_ref.location, "target": quest_ref.location},
        ],
        "progress": None,
    }


def _pending_director(
    quest_ref: QuestRef,
    *,
    state_path=None,
) -> tuple[QuestDirectorRuntime, QuestRef]:
    other = _ref("264", title="Другое поручение")
    director = QuestDirectorRuntime(chain_state_path=state_path)
    director.available_snapshot_fresh = True
    director.active_snapshot_fresh = True
    director.discovery_initialized = True
    director.available_quests = (quest_ref, other)
    director.intake_queue = (quest_ref, other)
    director.pending_accept = quest_ref
    director.chain.stage_accepted_ref(quest_ref)
    return director, other


def test_intake_fingerprint_is_exact_but_excludes_catalog_page() -> None:
    original = _ref(catalog_page=0)
    assert intake_ref_fingerprint(original) == intake_ref_fingerprint(
        _ref(catalog_page=9)
    )
    changed = (
        _ref(title="Другое название"),
        _ref(location="Чёрное капище"),
        _ref(giver="Другой волхв"),
        _ref(accept_ref="3808"),
    )
    assert all(
        intake_ref_fingerprint(candidate) != intake_ref_fingerprint(original)
        for candidate in changed
    )


def test_intake_quarantine_persists_restart_absence_and_time(tmp_path) -> None:
    state_path = tmp_path / "chain.json"
    quest_ref = _ref()
    chain = QuestChainRuntime(state_path=state_path)
    chain.stage_accepted_ref(quest_ref)
    evidence = chain.quarantine_intake_ref(
        quest_ref,
        reason="dialogue_choice_ambiguous",
        capability_version=INTAKE_CAPABILITY_VERSION,
        recorded_at=1.0,
    )

    restored = QuestChainRuntime(state_path=state_path)

    assert restored.intake_quarantines == (evidence,)
    assert restored.pending_accepted_ref is None
    assert restored.is_intake_quarantined(
        _ref(catalog_page=7),
        capability_version=INTAKE_CAPABILITY_VERSION,
    )
    assert restored.intake_quarantines[0].recorded_at == 1.0


def test_intake_quarantine_expires_only_on_changed_ref_or_capability() -> None:
    quest_ref = _ref()
    changed = _ref(location="Чёрное капище")
    chain = QuestChainRuntime()
    chain.stage_accepted_ref(quest_ref)
    chain.quarantine_intake_ref(
        quest_ref,
        reason="dialogue_choice_ambiguous",
        capability_version=INTAKE_CAPABILITY_VERSION,
        recorded_at=1.0,
    )

    assert not chain.is_intake_quarantined(
        changed,
        capability_version=INTAKE_CAPABILITY_VERSION,
    )
    assert chain.intake_quarantines == ()

    chain.stage_accepted_ref(quest_ref)
    chain.quarantine_intake_ref(
        quest_ref,
        reason="dialogue_choice_ambiguous",
        capability_version=INTAKE_CAPABILITY_VERSION,
        recorded_at=2.0,
    )
    assert not chain.is_intake_quarantined(
        quest_ref,
        capability_version="quest_intake_v2",
    )
    assert chain.intake_quarantines == ()


def test_intake_quarantine_history_is_bounded_and_restore_is_strict() -> None:
    chain = QuestChainRuntime(max_intake_quarantines=2)
    for quest_id in ("1", "2", "3"):
        quest_ref = _ref(quest_id, title=f"Quest {quest_id}")
        chain.stage_accepted_ref(quest_ref)
        chain.quarantine_intake_ref(
            quest_ref,
            reason="dialogue_choice_ambiguous",
            capability_version=INTAKE_CAPABILITY_VERSION,
            recorded_at=float(quest_id),
        )
    assert [item.quest_id for item in chain.intake_quarantines] == ["2", "3"]
    checkpoint = chain.checkpoint()
    assert checkpoint is not None
    raw = checkpoint["intake_quarantines"]
    assert isinstance(raw, list)

    with pytest.raises(ValueError, match="history"):
        QuestChainRuntime(max_intake_quarantines=1).restore(checkpoint)
    duplicate = {**checkpoint, "intake_quarantines": [raw[0], raw[0]]}
    with pytest.raises(ValueError, match="evidence"):
        QuestChainRuntime().restore(duplicate)
    tampered = json.loads(json.dumps(checkpoint))
    tampered["intake_quarantines"][0]["fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="evidence"):
        QuestChainRuntime().restore(tampered)


def test_director_quarantine_is_atomic_exact_and_action_free(tmp_path) -> None:
    quest_ref = _ref()
    director, other = _pending_director(
        quest_ref,
        state_path=tmp_path / "chain.json",
    )

    director.quarantine_pending_accept(quest_ref, "dialogue_choice_ambiguous")

    assert director.pending_accept is None
    assert director.chain.pending_accepted_ref is None
    assert director.intake_queue == (other,)
    assert director.available_quests == (other,)
    decision = director.decision()
    assert decision.intent is QuestDirectorIntent.WAIT
    assert decision.reason == "quest_intake_quarantine_recorded"


def test_director_quarantine_atomically_clears_settled_dialog_stage(tmp_path) -> None:
    quest_ref = _ref()
    director, other = _pending_director(
        quest_ref,
        state_path=tmp_path / "chain.json",
    )
    stage = make_pending_npc_open(
        client_id="client-a", profile_id="profile-a", tab_id=42,
        quest_id=quest_ref.id, quest_title=quest_ref.title,
        quest_accept_ref=quest_ref.accept_ref, quest_catalog_page=0,
        giver_name=quest_ref.giver_names[0], npc_id="4", route_ref="100",
        npc_name=quest_ref.giver_names[0], location_id="127",
        location_name=quest_ref.location or "", area_snapshot_id="area-npcs-1",
        area_generated_at=10.0, issued_at=11.0,
    )
    director.chain.stage_npc_open(stage)
    director.chain.settle_npc_open(stage)

    director.quarantine_pending_accept(
        quest_ref,
        "quest_accept_dialog_action_ambiguous",
    )

    assert director.chain.pending_accepted_ref is None
    assert director.chain.pending_npc_dialog is None
    assert director.chain.pending_npc_action is None
    assert director.intake_queue == (other,)
    restored = QuestChainRuntime(state_path=tmp_path / "chain.json")
    assert restored.pending_npc_dialog is None
    assert restored.intake_quarantines[-1].quest_id == quest_ref.id


@pytest.mark.parametrize("mismatch", ("pending", "staged", "queue", "eligible"))
def test_director_quarantine_mismatch_never_clears_state(mismatch: str) -> None:
    quest_ref = _ref()
    director, other = _pending_director(quest_ref)
    if mismatch == "pending":
        director.pending_accept = other
    elif mismatch == "staged":
        director.chain.pending_accepted_ref = other
    elif mismatch == "queue":
        director.intake_queue = (other, quest_ref)
    else:
        director.available_quests = (other,)
    before = (
        director.pending_accept,
        director.chain.pending_accepted_ref,
        director.intake_queue,
        director.available_quests,
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        director.quarantine_pending_accept(quest_ref, "dialogue_choice_ambiguous")

    assert (
        director.pending_accept,
        director.chain.pending_accepted_ref,
        director.intake_queue,
        director.available_quests,
    ) == before
    assert director.chain.intake_quarantines == ()


@pytest.mark.parametrize("confirmation", ("active", "accepted"))
def test_director_rejects_quarantine_after_confirmation(confirmation: str) -> None:
    quest_ref = _ref()
    director, _ = _pending_director(quest_ref)
    if confirmation == "active":
        director.active_quests = (QuestRef(quest_ref.id, quest_ref.title),)
    else:
        director.accepted_refs[quest_ref.id] = quest_ref

    with pytest.raises(RuntimeError, match="confirmed"):
        director.quarantine_pending_accept(quest_ref, "dialogue_choice_ambiguous")

    assert director.pending_accept == quest_ref
    assert director.chain.pending_accepted_ref == quest_ref


def test_restart_filters_same_ref_and_yields_wait_before_next_intake(tmp_path) -> None:
    state_path = tmp_path / "chain.json"
    quest_ref = _ref()
    original, other = _pending_director(quest_ref, state_path=state_path)
    original.quarantine_pending_accept(quest_ref, "dialogue_choice_ambiguous")
    restored = QuestDirectorRuntime(chain_state_path=state_path)

    assert restored.decision().reason == "quest_intake_quarantine_recorded"
    restored.begin_catalog_refresh()
    restored.ingest_catalog_page(_catalog(_available(quest_ref), _available(other)))
    wait = restored.decision()
    assert wait.intent is QuestDirectorIntent.WAIT
    assert wait.reason == "quest_intake_quarantine_recorded"
    assert restored.available_quests == (other,)
    restored.observe_active([])
    next_decision = restored.decision()
    assert next_decision.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert next_decision.quest == other


def test_intake_abandon_requires_and_clears_only_exact_ref() -> None:
    quest_ref = _ref()
    intake = QuestIntakeRuntime()
    intake.begin(quest_ref, already_at_location=False)

    with pytest.raises(RuntimeError, match="does not match"):
        intake.abandon(_ref(location="Чёрное капище"))
    assert intake.pending is not None

    intake.abandon(quest_ref)
    assert intake.pending is None


def test_cold_start_staged_active_binds_without_reaccept(tmp_path) -> None:
    quest_ref = _ref()
    state_path = tmp_path / "active-chain.json"
    QuestChainRuntime(state_path=state_path).stage_accepted_ref(quest_ref)
    director = QuestDirectorRuntime(chain_state_path=state_path)
    director.begin_catalog_refresh()
    director.ingest_catalog_page(_catalog())
    director.begin_active_refresh()
    director.ingest_active_page(_active_catalog(_active(quest_ref)))

    assert director.chain.pending_accepted_ref is None
    assert director.chain.lease is not None
    assert director.chain.lease.accepted_ref == quest_ref
    assert director.pending_accept is None
    assert director.accepted_refs[quest_ref.id] == quest_ref
    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.pending_accepted_ref is None
    assert restored.chain.lease is not None
    assert restored.chain.lease.accepted_ref == quest_ref


def test_atomic_staged_active_recovery_rolls_back_on_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    quest_ref = _ref()
    state_path = tmp_path / "atomic-failure-chain.json"
    chain = QuestChainRuntime(state_path=state_path)
    chain.stage_accepted_ref(quest_ref)
    catalogue = QuestDirectorRuntime()
    catalogue.begin_active_refresh()
    catalogue.ingest_active_page(_active_catalog(_active(quest_ref)))
    entry = catalogue.active_catalog.result[0]

    def fail_persist() -> None:
        raise OSError("injected persistence failure")

    monkeypatch.setattr(chain, "_persist", fail_persist)
    with pytest.raises(OSError, match="injected persistence failure"):
        chain.recover_staged_active_ref(
            entry,
            revision=catalogue.active_catalog.revision,
            expected_ref=quest_ref,
        )

    assert chain.lease is None
    assert chain.pending_accepted_ref == quest_ref
    restored = QuestChainRuntime(state_path=state_path)
    assert restored.lease is None
    assert restored.pending_accepted_ref == quest_ref


def test_cold_start_staged_available_preempts_queue_and_reconstructs_acceptance(
    tmp_path,
) -> None:
    quest_ref = _ref(catalog_page=7)
    other = _ref("264", title="Другое поручение")
    state_path = tmp_path / "available-chain.json"
    QuestChainRuntime(state_path=state_path).stage_accepted_ref(quest_ref)
    director = QuestDirectorRuntime(chain_state_path=state_path)
    director.begin_catalog_refresh()
    director.ingest_catalog_page(
        _catalog(_available(other), _available(_ref(catalog_page=0)))
    )
    director.begin_active_refresh()
    director.ingest_active_page(_active_catalog())

    decision = director.decision()

    assert decision.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert decision.reason == "staged_intake_available_reconstructed"
    assert decision.quest == quest_ref
    assert director.intake_queue[0] == quest_ref
    assert director.begin_accept(quest_ref.id) == quest_ref
    assert director.chain.pending_accepted_ref == quest_ref


def test_cold_start_orphan_staged_gets_one_refresh_then_durable_quarantine(
    tmp_path,
) -> None:
    quest_ref = _ref()
    state_path = tmp_path / "orphan-chain.json"
    QuestChainRuntime(state_path=state_path).stage_accepted_ref(quest_ref)
    director = QuestDirectorRuntime(chain_state_path=state_path)
    director.begin_catalog_refresh()
    director.ingest_catalog_page(_catalog())
    director.begin_active_refresh()
    director.ingest_active_page(_active_catalog())

    refresh = director.decision()
    assert refresh.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert refresh.reason == "staged_intake_reconciliation_refresh_required"
    assert director.pending_accept is None
    assert director.chain.pending_accepted_ref == quest_ref

    director.begin_catalog_refresh()
    director.ingest_catalog_page(_catalog())
    director.begin_active_refresh()
    director.ingest_active_page(_active_catalog())
    barrier = director.decision()

    assert barrier.intent is QuestDirectorIntent.WAIT
    assert barrier.reason == "orphan_staged_intake_quarantined"
    assert director.pending_accept is None
    assert director.chain.pending_accepted_ref is None
    assert [item.quest_id for item in director.chain.intake_quarantines] == [quest_ref.id]
    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.pending_accepted_ref is None
    assert restored.chain.intake_quarantines == director.chain.intake_quarantines
