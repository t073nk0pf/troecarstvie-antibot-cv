from __future__ import annotations

import time
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_state import QuestChainLease
from src.antibot_cv.automation.quest_compiler import compile_quest_plan
from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_local_block_journal import (
    LocalBlockEnsureResult,
    LocalBlockEnsureState,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_evidence import (
    EvidenceEnvelope,
    EvidenceSourceKind,
    FactKind,
    SatisfiedFact,
)
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationContext
from src.antibot_cv.automation.quest_plan_model import Acquire, TurnIn, iter_requirements
from src.antibot_cv.automation.quest_turnin_admission import ActiveCatalogAuthority
from src.antibot_cv.automation.quest_turnin_coordinator import (
    QuestTurnInCoordinatorMixin,
    _turn_in_authority_id,
)
from src.antibot_cv.automation.quest_turnin_outcome import QuestTurnInStartOutcome
from src.antibot_cv.automation.quest_turnin_runtime import QuestTurnInRuntime
from src.antibot_cv.automation.quest_runtime import QuestRuntimeMixin
from src.antibot_cv.automation.quest_refresh_runtime import QuestRefreshRuntimeMixin
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
)
from src.antibot_cv.automation.area_object_activity import AreaObjectPlanStatus


def _entry(quest_id: str) -> ActiveQuestEntry:
    if quest_id == "280":
        title = "Фамильная ступка"
        objective = "Вернитесь к разбойнику Аскорду в Земли Пращуров."
        navigation = ({"text": "Земли Пращуров", "target": "Земли Пращуров"},)
    else:
        title = "Цветочная болезнь"
        objective = (
            "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите "
            "в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб "
            "и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, "
            "возвращайтесь к колдунье Вилене."
        )
        navigation = (
            {"text": "Непобедимый кабан", "target": "Непобедимый кабан [5]"},
            {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
            {"text": "Длани Рода", "target": "Длань Рода"},
            {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
        )
    return ActiveQuestEntry(quest_id, title, MappingProxyType({
        "id": quest_id, "title": title, "status": "active", "objective": objective,
        "objectiveKind": "dialogue" if quest_id == "280" else "collect",
        "navigation": tuple(MappingProxyType(item) for item in navigation),
        "progress": MappingProxyType({"current": 5, "required": 5, "complete": False}),
    }))


class _Chain:
    def __init__(self, lease: QuestChainLease) -> None:
        self.lease = lease
        self.bind_calls = 0
        self.quarantined: list[tuple[str, str]] = []
        self.local_authorities: list[str] = []
        self.pending_active_catalog_navigation = None

    def bind_accepted_ref(self, ref: QuestRef) -> QuestChainLease:
        self.bind_calls += 1
        raise AssertionError("semantic admission must precede binding")

    def ensure_local_block(self, _quest_id, _title, _fingerprint, *, authority_id, **_kwargs):
        counted = authority_id not in self.local_authorities
        if counted:
            self.local_authorities.append(authority_id)
        attempts = len(self.local_authorities)
        state = (
            LocalBlockEnsureState.QUARANTINE_REQUIRED
            if attempts >= 2
            else LocalBlockEnsureState.STAGED
            if self.pending_active_catalog_navigation is not None
            else LocalBlockEnsureState.REFRESH_REQUIRED
        )
        return LocalBlockEnsureResult(state, attempts, counted)

    def clear_local_block(self, *_args, **_kwargs) -> bool:
        self.local_authorities.clear()
        self.pending_active_catalog_navigation = None
        return True


class _Logger:
    def log_event(self, *args, **kwargs) -> None:
        pass


class _Harness(QuestTurnInCoordinatorMixin):
    def __init__(self, quest_id: str, *, satisfied: int | None = None, authority_change=None) -> None:
        entry = _entry(quest_id)
        compiled = compile_quest_plan(entry)
        assert compiled.plan is not None
        plan = compiled.plan
        fingerprint, reason = quest_step_fingerprint(entry)
        assert fingerprint is not None, reason
        giver = "Разбойник Аскорд" if quest_id == "280" else "Колдунья Вилена"
        location = "Земли Пращуров" if quest_id == "280" else "Пристанище трёх ветров"
        ref = QuestRef(quest_id, entry.title, location=location, giver_names=(giver,))
        lease = QuestChainLease(
            quest_id, entry.title, 4, fingerprint, (fingerprint,),
            accepted_ref=ref, turn_in_ref_fingerprint=fingerprint,
        )
        self.chain = _Chain(lease)
        self._quest_director = SimpleNamespace(
            active_snapshot_fresh=True,
            active_catalog=SimpleNamespace(complete=True, result=(entry,), revision=10),
            chain=self.chain,
            quarantine_active_quest=lambda quest_id, reason: self.chain.quarantined.append(
                (quest_id, reason)
            ),
        )
        self.config = SimpleNamespace(leveling=SimpleNamespace(quest_engine_mode="q280_q304"))
        self._quest_turn_in = QuestTurnInRuntime()
        self._quest_turn_in_started_monotonic = None
        self._quest_turn_in_verify_retries = 0
        self._quest_chat_terminal_completion_evidence = object()
        self._quest_inventory_terminal_completion_evidence = object()
        self.current_location_name = "Другая локация"
        self.logger = _Logger()
        self.state_machine = SimpleNamespace(state=SimpleNamespace(value="QUEST_REFRESH_PENDING"))
        self.session = SimpleNamespace(cycle_id=1)
        self.stopped_reason = None
        self.routes: list[tuple[str, str, str]] = []
        self.refreshes: list[str] = []
        now = time.time()
        self._quest_semantic_evaluation_context = EvaluationContext(
            "client", "profile", "tab", "baseline", 5, now,
        )
        leaves = tuple(iter_requirements(plan.graph.root))
        prerequisites = tuple(leaf for leaf in leaves if not isinstance(leaf, TurnIn))
        count = len(prerequisites) if satisfied is None else satisfied
        self._quest_semantic_evidence = tuple(
            _evidence(plan, leaf, now=now, revision=6 + index)
            for index, leaf in enumerate(prerequisites[:count])
        )
        authority = ActiveCatalogAuthority(
            (entry,), 10, "catalog-10", now - 0.1, 5.0,
            "client", "profile", "tab", "baseline", plan.fingerprint, fingerprint,
        )
        self._quest_active_catalog_authority = (
            authority_change(authority) if authority_change else authority
        )

    def _stop_leveling_unsafe(self, reason: str) -> bool:
        self.stopped_reason = reason
        return True

    def _start_location_route(self, destination: str, *, kind: str, reason: str) -> None:
        self.routes.append((destination, kind, reason))

    def _request_active_quest_snapshot(self, reason: str) -> bool:
        self.refreshes.append(reason)
        self.chain.pending_active_catalog_navigation = object()
        return True


class _RefreshCallerHarness(_Harness):
    _handle_quest_refresh = QuestRuntimeMixin._handle_quest_refresh

    def __init__(self, quest_id: str, *, satisfied: int | None = None) -> None:
        super().__init__(quest_id, satisfied=satisfied)
        self._quest_active_snapshot_requested = False
        self._quest_active_page_requested = None
        self._quest_catalog_page_requested = None
        self._quest_refresh_requested_monotonic = time.monotonic()
        self._quest_director.refresh_in_progress = False

    def _handle_pending_quest_turn_in(self) -> bool:
        return False

    def _handle_pending_quest_dialogue(self) -> bool:
        return False

    def _handle_pending_quest_acceptance(self) -> bool:
        return False

    def _handle_pending_quest_chat_refresh(self, **_kwargs) -> bool:
        return False


class _NonCombatCallerHarness(_Harness):
    _begin_non_combat_quest_executor = QuestRefreshRuntimeMixin._begin_non_combat_quest_executor


def _evidence(plan, leaf, *, now: float, revision: int) -> EvidenceEnvelope:
    assert isinstance(leaf, Acquire)
    return EvidenceEnvelope(
        plan.quest_id, plan.quest_title, plan.fingerprint, leaf.step_id,
        leaf.requirement_id, EvidenceSourceKind.INVENTORY, f"inventory-{leaf.requirement_id}",
        revision, "client", "profile", "tab", now - 0.2, 5.0, True, "baseline",
        SatisfiedFact(FactKind.COUNT_AT_LEAST, leaf.item, leaf.count, leaf.count), {},
    )


@pytest.mark.parametrize("quest_id", ["280", "304"])
def test_exact_semantic_prerequisites_start_turn_in(quest_id: str) -> None:
    runtime = _Harness(quest_id)

    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.STARTED
    assert runtime._quest_turn_in.pending is not None
    assert runtime.routes and runtime.routes[0][1] == "quest_turn_in"
    assert runtime.chain.bind_calls == 0
    assert runtime._quest_chat_terminal_completion_evidence is not None
    assert runtime._quest_inventory_terminal_completion_evidence is not None


def test_q304_blood_only_is_blocked_without_action_or_binding() -> None:
    runtime = _Harness("304", satisfied=1)

    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert runtime.chain.quarantined == []
    assert runtime.refreshes == ["quest_turn_in_local_blocked_refresh"]


def test_refresh_production_caller_consumes_real_local_block_without_fallthrough() -> None:
    runtime = _RefreshCallerHarness("304", satisfied=1)

    runtime._handle_quest_refresh()
    runtime._handle_quest_refresh()

    assert runtime.stopped_reason is None
    assert runtime.routes == []
    assert runtime.refreshes == ["quest_turn_in_local_blocked_refresh"]


def test_post_pin_production_caller_consumes_real_local_block_without_stop(
    monkeypatch,
) -> None:
    runtime = _NonCombatCallerHarness("304", satisfied=1)
    runtime._quest_director.active_route_plan = SimpleNamespace(
        quest_id="304",
        status=ObjectiveRouteStatus.READY,
        kind=ObjectiveRouteKind.TURN_IN,
        reason="typed_turn_in",
        unmet_requirements=(),
    )
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_refresh_runtime.parse_area_object_plan",
        lambda _entry: SimpleNamespace(status=AreaObjectPlanStatus.UNSAFE),
    )

    assert runtime._begin_non_combat_quest_executor("304") is True

    assert runtime.stopped_reason is None
    assert runtime.routes == []
    assert runtime.refreshes == ["quest_turn_in_local_blocked_refresh"]
    assert runtime.chain.local_authorities
    runtime._quest_active_catalog_authority = replace(
        runtime._quest_active_catalog_authority,
        snapshot_id="catalog-11",
        revision=11,
    )
    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert runtime._quest_turn_in.pending is None
    assert runtime.routes == []
    assert runtime.chain.bind_calls == 0
    assert runtime.stopped_reason is None
    assert runtime.chain.quarantined == [
        ("304", "turn_in_no_progress_turn_in_not_next_actionable_atom")
    ]
    assert runtime.refreshes == ["quest_turn_in_local_blocked_refresh"]


def test_q304_local_block_budget_survives_restart_and_quarantines_exact_step(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"

    first = _Harness("304", satisfied=1)
    entry = first._quest_director.active_catalog.result[0]
    ref = first.chain.lease.accepted_ref
    assert ref is not None
    chain = QuestChainRuntime(state_path=state_path)
    chain.pin_entry(entry, revision=10)
    chain.bind_accepted_ref(ref)
    first.chain = chain
    first._quest_director.chain = chain
    first._quest_director.quarantine_active_quest = lambda quest_id, reason: chain.quarantine_entry(
        entry, reason=reason, capability_version="objective_router_v3",
    )

    assert first._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert len(chain.local_blocks) == 1
    assert chain.local_blocks[0].attempts == 1
    assert not hasattr(chain.local_blocks[0], "refresh_claimed")

    restored_chain = QuestChainRuntime(state_path=state_path)
    second = _Harness("304", satisfied=1)
    second.chain = restored_chain
    second._quest_director.chain = restored_chain
    second._quest_director.quarantine_active_quest = (
        lambda quest_id, reason: restored_chain.quarantine_entry(
            entry, reason=reason, capability_version="objective_router_v3",
        )
    )
    second._quest_active_catalog_authority = replace(
        second._quest_active_catalog_authority,
        snapshot_id="catalog-11",
        revision=11,
    )

    assert second._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert restored_chain.lease is None
    assert restored_chain.local_blocks == ()
    assert len(restored_chain.quarantines) == 1
    assert restored_chain.quarantines[0].quest_id == "304"


def test_restart_after_persisted_first_record_resumes_refresh_transition(tmp_path) -> None:
    state_path = tmp_path / "quest-chain-first-record.json"
    runtime = _Harness("304", satisfied=1)
    entry = runtime._quest_director.active_catalog.result[0]
    ref = runtime.chain.lease.accepted_ref
    assert ref is not None
    chain = QuestChainRuntime(state_path=state_path)
    chain.pin_entry(entry, revision=10)
    chain.bind_accepted_ref(ref)
    authority_id = _turn_in_authority_id(runtime._quest_active_catalog_authority)
    first = chain.ensure_local_block(
        "304", entry.title, chain.lease.current_fingerprint,
        phase="turn_in", capability_version="semantic_turn_in_v1",
        reason="turn_in_not_next_actionable_atom", authority_id=authority_id,
    )
    assert first.state is LocalBlockEnsureState.REFRESH_REQUIRED

    restored = QuestChainRuntime(state_path=state_path)
    runtime.chain = restored
    runtime._quest_director.chain = restored

    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert runtime.refreshes == ["quest_turn_in_local_blocked_refresh"]
    assert restored.local_blocks[0].attempts == 1


def test_restart_after_persisted_exhaustion_finishes_quarantine(tmp_path) -> None:
    state_path = tmp_path / "quest-chain-exhausted.json"
    runtime = _Harness("304", satisfied=1)
    entry = runtime._quest_director.active_catalog.result[0]
    ref = runtime.chain.lease.accepted_ref
    assert ref is not None
    chain = QuestChainRuntime(state_path=state_path)
    chain.pin_entry(entry, revision=10)
    chain.bind_accepted_ref(ref)
    fingerprint = chain.lease.current_fingerprint
    for authority_id in ("authority-a", "authority-b"):
        chain.ensure_local_block(
            "304", entry.title, fingerprint,
            phase="turn_in", capability_version="semantic_turn_in_v1",
            reason="turn_in_not_next_actionable_atom", authority_id=authority_id,
        )

    restored = QuestChainRuntime(state_path=state_path)
    runtime.chain = restored
    runtime._quest_director.chain = restored
    runtime._quest_director.quarantine_active_quest = (
        lambda quest_id, reason: restored.quarantine_entry(
            entry, reason=reason, capability_version="objective_router_v3",
        )
    )
    runtime._quest_active_catalog_authority = replace(
        runtime._quest_active_catalog_authority,
        snapshot_id="catalog-b",
        revision=12,
    )
    persisted_authority = _turn_in_authority_id(runtime._quest_active_catalog_authority)
    # Simulate crash after the second evidence persist by making the replayed
    # authority already present in the exhausted record.
    block = restored.local_blocks[0]
    restored.local_blocks = (replace(
        block,
        authority_ids=(block.authority_ids[0], persisted_authority),
        attempts=2,
    ),)
    restored._persist()

    assert runtime._maybe_begin_quest_turn_in() is QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert restored.lease is None
    assert restored.local_blocks == ()
    assert len(restored.quarantines) == 1


@pytest.mark.parametrize(
    ("change", "unsafe"),
    [
        (lambda authority: authority.__class__(**{
            **{name: getattr(authority, name) for name in authority.__dataclass_fields__},
            "generated_at": authority.generated_at - 10,
        }), False),
        (lambda authority: authority.__class__(**{
            **{name: getattr(authority, name) for name in authority.__dataclass_fields__},
            "tab_id": "foreign",
        }), True),
    ],
)
def test_stale_or_foreign_authority_never_mutates(change, unsafe: bool) -> None:
    runtime = _Harness("280", authority_change=change)

    expected = QuestTurnInStartOutcome.STOPPED if unsafe else QuestTurnInStartOutcome.LOCAL_BLOCKED
    assert runtime._maybe_begin_quest_turn_in() is expected
    assert runtime._quest_turn_in.pending is None
    assert runtime.routes == []
    assert runtime.chain.bind_calls == 0
    assert (runtime.stopped_reason is not None) is unsafe
