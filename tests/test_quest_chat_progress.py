from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from types import MappingProxyType, SimpleNamespace

import pytest

from src.antibot_cv.automation.quest_chat_progress import (
    AuthoritativeQuestStep,
    PendingQuestChatRefresh,
    QuestChatProgressEvidence,
    QuestChatProgressTracker,
    QuestChatReconcileState,
    collection_resource,
    collection_completes_step,
    reconcile_chat_refresh,
    resource_matches_objective,
)
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint
from src.antibot_cv.automation.quest_runtime import QuestRuntimeMixin


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def step(objective: str, *, fingerprint: str = "step-1") -> AuthoritativeQuestStep:
    return AuthoritativeQuestStep("31", "Жужжащая угроза", fingerprint, objective)


def section(
    text: str,
    resource: str,
    *,
    event_id: str = "chat-1",
    observed_at: datetime = NOW,
    is_new: object = True,
) -> dict[str, object]:
    return {
        "status": "available",
        "data": {
            "loadStatus": "loaded",
            "truncated": False,
            "observations": [
                {
                    "eventId": event_id,
                    "text": text,
                    "resource": resource,
                    "observedAt": observed_at.isoformat(),
                    "isNew": is_new,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("message", "resource", "objective"),
    [
        (
            "Вы набрали достаточное количество осиных крыльев.",
            "осиных крыльев",
            "Соберите 5 осиных крыльев и вернитесь к старосте.",
        ),
        (
            "Вы набрали достаточное количество волчьих шкур!",
            "волчьих шкур",
            "Набрать десять волчьих шкур.",
        ),
        (
            "Вы набрали достаточное количество древесины.",
            "древесины",
            "Добудьте древесину (0/7).",
        ),
        (
            "Вы набрали необходимое количество крови Непобедимых кабанов!",
            "крови Непобедимых кабанов",
            "Добудьте 5 единиц крови Непобедимых кабанов.",
        ),
    ],
)
def test_accepts_exact_collection_template_for_unique_authoritative_resource(
    message: str,
    resource: str,
    objective: str,
) -> None:
    tracker = QuestChatProgressTracker()

    evidence = tracker.observe(
        section(message, resource),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    )

    assert evidence is not None
    assert evidence.resource == resource
    assert evidence.quest_id == "31"
    assert evidence.fingerprint == "step-1"


def test_ignores_unrelated_resource_and_non_exact_keyword_message() -> None:
    tracker = QuestChatProgressTracker()
    objective = "Соберите 5 осиных крыльев."

    assert tracker.observe(
        section("Вы набрали достаточное количество волчьих шкур.", "волчьих шкур"),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    ) is None
    assert collection_resource("Получено: достаточное количество осиных крыльев") is None


def test_only_single_collection_followed_by_explicit_return_completes_step() -> None:
    objective = (
        "Убивая Кабанов-секачей, получите 10 Гурум-корней "
        "и возвращайтесь к служителю Фаремайту во Врата Древних."
    )
    assert collection_completes_step("Гурум-корней", objective)
    assert not collection_completes_step("крови Непобедимых кабанов", objective)
    assert not collection_completes_step(
        "Гурум-корней",
        "Получите 10 Гурум-корней и соберите 5 грибов, затем возвращайтесь к Фаремайту.",
    )


def test_ambiguous_objective_resource_occurrence_is_fail_closed() -> None:
    assert not resource_matches_objective(
        "осиных крыльев",
        "Сравните осиные крылья и затем принесите осиных крыльев.",
    )


def test_matches_resource_and_monster_tokens_across_separate_quest_clauses() -> None:
    objective = (
        "Для укрепления нужен яд Шершней-мстителей. "
        "Отправьтесь на охоту за Шершнями, получите 5 жал и "
        "возвращайтесь к воеводе Асенарду."
    )

    assert resource_matches_objective("жал Шершней-мстителей", objective)
    assert collection_completes_step("жал Шершней-мстителей", objective)


def test_separated_semantic_match_rejects_repeated_ambiguous_token() -> None:
    objective = (
        "Убивайте Шершней-мстителей, получите 5 жал; "
        "повреждённые жала не засчитываются."
    )

    assert not resource_matches_objective("жал Шершней-мстителей", objective)


def test_stale_snapshot_or_observation_and_baseline_line_are_ignored() -> None:
    tracker = QuestChatProgressTracker(max_age_s=5)
    objective = "Соберите 5 осиных крыльев."
    message = "Вы набрали достаточное количество осиных крыльев."

    assert tracker.observe(
        section(message, "осиных крыльев"),
        snapshot_generated_at=(NOW - timedelta(seconds=6)).isoformat(),
        step=step(objective),
        now=NOW,
    ) is None
    assert tracker.observe(
        section(
            message,
            "осиных крыльев",
            observed_at=NOW - timedelta(seconds=6),
        ),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    ) is None
    assert tracker.observe(
        section(message, "осиных крыльев", is_new=False),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    ) is None


def test_deduplicates_by_authoritative_fingerprint_and_resource() -> None:
    tracker = QuestChatProgressTracker()
    objective = "Соберите 5 осиных крыльев."
    message = "Вы набрали достаточное количество осиных крыльев."
    first = tracker.observe(
        section(message, "осиных крыльев"),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    )
    duplicate = tracker.observe(
        section(message, "осиных крыльев", event_id="chat-shifted"),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective),
        now=NOW,
    )
    advanced = tracker.observe(
        section(message, "осиных крыльев", event_id="chat-next-step"),
        snapshot_generated_at=NOW.isoformat(),
        step=step(objective, fingerprint="step-2"),
        now=NOW,
    )

    assert first is not None
    assert duplicate is None
    assert advanced is not None


def test_multiple_matching_new_lines_in_one_snapshot_are_ambiguous() -> None:
    payload = section(
        "Вы набрали достаточное количество осиных крыльев.",
        "осиных крыльев",
    )
    observations = payload["data"]["observations"]  # type: ignore[index]
    assert isinstance(observations, list)
    observations.append({**observations[0], "eventId": "chat-2"})

    assert QuestChatProgressTracker().observe(
        payload,
        snapshot_generated_at=NOW.isoformat(),
        step=step("Соберите 5 осиных крыльев."),
        now=NOW,
    ) is None


def test_runtime_stages_only_full_active_refresh_for_bound_current_fingerprint() -> None:
    observed_now = datetime.now(timezone.utc)
    entry = ActiveQuestEntry(
        "31",
        "Жужжащая угроза",
        MappingProxyType(
            {
                "id": "31",
                "title": "Жужжащая угроза",
                "status": "active",
                "objective": "Соберите 5 осиных крыльев.",
                "navigation": (
                    MappingProxyType({"text": "Осиное гнездо", "target": "Осиное гнездо"}),
                ),
                "progress": MappingProxyType(
                    {"current": 4, "required": 5, "complete": False}
                ),
            }
        ),
    )
    fingerprint, _ = quest_step_fingerprint(entry)
    assert fingerprint is not None
    events: list[tuple[str, dict[str, object]]] = []
    runtime = SimpleNamespace(
        _quest_director=SimpleNamespace(
            chain=SimpleNamespace(
                lease=SimpleNamespace(
                    quest_id="31",
                    quest_title="Жужжащая угроза",
                    current_fingerprint=fingerprint,
                )
            ),
                active_catalog=SimpleNamespace(complete=True, result=(entry,), revision=4),
            ),
            _quest_chat_progress=QuestChatProgressTracker(),
            _quest_chat_refresh_pending=None,
            config=SimpleNamespace(
                leveling=SimpleNamespace(quest_refresh_timeout_ms=3000)
            ),
        logger=SimpleNamespace(
            log_event=lambda event_type, **fields: events.append((event_type, fields))
        ),
        state_machine=SimpleNamespace(state=SimpleNamespace(value="BATTLE")),
        session=SimpleNamespace(cycle_id="cycle-1"),
    )
    runtime._bound_quest_chat_step = lambda: QuestRuntimeMixin._bound_quest_chat_step(runtime)

    QuestRuntimeMixin._observe_quest_chat_progress(
        runtime,
        {"snapshotId": "state-chat-1", "generatedAt": observed_now.isoformat()},
        section(
            "Вы набрали достаточное количество осиных крыльев.",
            "осиных крыльев",
            observed_at=observed_now,
        ),
    )

    assert runtime._quest_chat_refresh_pending is not None
    assert runtime._quest_chat_refresh_pending.trigger_revision == 4
    assert events == [
        (
            "quest_chat_progress_observed",
            {
                "state": "BATTLE",
                "cycle_id": "cycle-1",
                "quest_id": "31",
                "quest_fingerprint": fingerprint,
                "resource": "осиных крыльев",
                "chat_event_id": "chat-1",
                "outcome": "active_catalog_refresh_pending",
                "trigger_revision": 4,
                "trigger_snapshot_id": "state-chat-1",
            },
        )
    ]


def pending_refresh(
    fingerprint: str,
    *,
    trigger_revision: int = 4,
    attempts: int = 1,
    dedicated: bool = True,
) -> PendingQuestChatRefresh:
    return PendingQuestChatRefresh(
        QuestChatProgressEvidence(
            "chat-1",
            "осиных крыльев",
            "осиных крыльев",
            "31",
            "Жужжащая угроза",
            fingerprint,
        ),
        trigger_revision,
        "state-chat-1",
        10.0,
        30.0,
        attempts,
        dedicated,
    )


def collection_entry(*, objective: str, current: int) -> ActiveQuestEntry:
    return ActiveQuestEntry(
        "31",
        "Жужжащая угроза",
        MappingProxyType(
            {
                "id": "31",
                "title": "Жужжащая угроза",
                "status": "active",
                "objective": objective,
                "navigation": (
                    MappingProxyType({"text": "Осиное гнездо", "target": "Осиное гнездо"}),
                ),
                "progress": MappingProxyType(
                    {"current": current, "required": 5, "complete": current >= 5}
                ),
            }
        ),
    )


def test_reconcile_requires_strictly_newer_dedicated_post_trigger_refresh() -> None:
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None

    same_revision = reconcile_chat_refresh(
        pending_refresh(fingerprint), (original,), completed_revision=4
    )
    predating_inflight = reconcile_chat_refresh(
        pending_refresh(fingerprint, dedicated=False), (original,), completed_revision=5
    )

    assert same_revision.state is QuestChatReconcileState.NOT_NEWER
    assert predating_inflight.state is QuestChatReconcileState.NOT_NEWER
    assert predating_inflight.reason == "completed_refresh_predates_chat_trigger"


def test_reconcile_distinguishes_same_advanced_and_removed_without_terminal_claim() -> None:
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    advanced = collection_entry(objective="Вернитесь к старосте.", current=5)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None
    base = pending_refresh(fingerprint)
    now = time.monotonic()
    pending = PendingQuestChatRefresh(
        base.evidence,
        base.trigger_revision,
        base.trigger_snapshot_id,
        now,
        now + 60,
        base.attempts,
        base.dedicated_refresh_started,
    )

    same = reconcile_chat_refresh(pending, (original,), completed_revision=5)
    changed = reconcile_chat_refresh(pending, (advanced,), completed_revision=5)
    removed = reconcile_chat_refresh(pending, (), completed_revision=5)

    assert same.state is QuestChatReconcileState.SAME_STEP
    assert changed.state is QuestChatReconcileState.ADVANCED
    assert removed.state is QuestChatReconcileState.REMOVED
    assert "without_terminal_proof" in removed.reason


def test_inflight_refresh_does_not_consume_pending_evidence() -> None:
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None
    raw_pending = pending_refresh(fingerprint, dedicated=False)
    pending = PendingQuestChatRefresh(
        raw_pending.evidence,
        raw_pending.trigger_revision,
        raw_pending.trigger_snapshot_id,
        time.monotonic(),
        time.monotonic() + 60,
        dedicated_refresh_started=False,
    )
    runtime = SimpleNamespace(
        _quest_chat_refresh_pending=pending,
        _quest_active_snapshot_requested=True,
        _request_active_quest_snapshot=lambda reason: (_ for _ in ()).throw(
            AssertionError(reason)
        ),
    )

    assert QuestRuntimeMixin._handle_pending_quest_chat_refresh(runtime) is True
    assert runtime._quest_chat_refresh_pending is pending


def test_inflight_chat_refresh_yields_to_active_catalog_pagination() -> None:
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None
    base = pending_refresh(fingerprint)
    now = time.monotonic()
    pending = PendingQuestChatRefresh(
        base.evidence,
        base.trigger_revision,
        base.trigger_snapshot_id,
        now,
        now + 60,
        base.attempts,
        base.dedicated_refresh_started,
    )
    runtime = SimpleNamespace(
        _quest_chat_refresh_pending=pending,
        _quest_active_snapshot_requested=True,
        _request_active_quest_snapshot=lambda reason: (_ for _ in ()).throw(
            AssertionError(reason)
        ),
    )

    assert QuestRuntimeMixin._handle_pending_quest_chat_refresh(
        runtime, allow_active_progress=True
    ) is False
    assert runtime._quest_chat_refresh_pending is pending


def test_evidence_is_staged_from_cached_authoritative_step_during_inflight_refresh() -> None:
    observed_now = datetime.now(timezone.utc)
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None
    lease = SimpleNamespace(
        quest_id="31",
        quest_title="Жужжащая угроза",
        current_fingerprint=fingerprint,
    )
    runtime = SimpleNamespace(
        _quest_director=SimpleNamespace(
            chain=SimpleNamespace(lease=lease),
            active_catalog=SimpleNamespace(complete=False, revision=4),
        ),
        _quest_chat_authoritative_step=AuthoritativeQuestStep(
            "31", "Жужжащая угроза", fingerprint, "Соберите 5 осиных крыльев."
        ),
        _quest_chat_progress=QuestChatProgressTracker(),
        _quest_chat_refresh_pending=None,
        config=SimpleNamespace(leveling=SimpleNamespace(quest_refresh_timeout_ms=3000)),
        logger=SimpleNamespace(log_event=lambda *args, **kwargs: None),
        state_machine=SimpleNamespace(state=SimpleNamespace(value="BATTLE")),
        session=SimpleNamespace(cycle_id="cycle-1"),
    )
    runtime._bound_quest_chat_step = lambda: QuestRuntimeMixin._bound_quest_chat_step(runtime)

    QuestRuntimeMixin._observe_quest_chat_progress(
        runtime,
        {"snapshotId": "during-inflight", "generatedAt": observed_now.isoformat()},
        section(
            "Вы набрали достаточное количество осиных крыльев.",
            "осиных крыльев",
            observed_at=observed_now,
        ),
    )

    assert runtime._quest_chat_refresh_pending is not None
    assert runtime._quest_chat_refresh_pending.trigger_revision == 4
    assert runtime._quest_chat_refresh_pending.dedicated_refresh_started is False


def test_same_fingerprint_confirms_one_resource_requirement_only() -> None:
    original = collection_entry(objective="Соберите 5 осиных крыльев.", current=4)
    fingerprint, _ = quest_step_fingerprint(original)
    assert fingerprint is not None
    events: list[tuple[str, dict[str, object]]] = []
    stops: list[str] = []
    base = pending_refresh(fingerprint, attempts=1)
    base = PendingQuestChatRefresh(
        base.evidence,
        base.trigger_revision,
        base.trigger_snapshot_id,
        time.monotonic(),
        time.monotonic() + 60,
        1,
        True,
    )
    runtime = SimpleNamespace(
        _quest_chat_refresh_pending=base,
        _quest_director=SimpleNamespace(
            active_catalog=SimpleNamespace(complete=True, result=(original,), revision=5)
        ),
        logger=SimpleNamespace(
            log_event=lambda event_type, **fields: events.append((event_type, fields))
        ),
        state_machine=SimpleNamespace(state=SimpleNamespace(value="QUEST_REFRESH_PENDING")),
        session=SimpleNamespace(cycle_id="cycle-1"),
        _stop_leveling_unsafe=lambda reason: stops.append(reason) or True,
    )

    QuestRuntimeMixin._reconcile_quest_chat_refresh(runtime)

    assert runtime._quest_chat_refresh_pending is None
    assert events[-1][0] == "quest_chat_resource_requirement_confirmed"
    assert not stops


def replace_pending_attempts(
    pending: PendingQuestChatRefresh, attempts: int
) -> PendingQuestChatRefresh:
    return PendingQuestChatRefresh(
        pending.evidence,
        pending.trigger_revision,
        pending.trigger_snapshot_id,
        pending.started_monotonic,
        pending.deadline_monotonic,
        attempts,
        True,
        pending.next_retry_monotonic,
    )
