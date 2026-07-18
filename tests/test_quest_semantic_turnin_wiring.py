from __future__ import annotations

import time
from types import MappingProxyType, SimpleNamespace

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_state import QuestChainLease
from src.antibot_cv.automation.quest_compiler import compile_quest_plan
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
from src.antibot_cv.automation.quest_turnin_coordinator import QuestTurnInCoordinatorMixin
from src.antibot_cv.automation.quest_turnin_runtime import QuestTurnInRuntime


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

    def bind_accepted_ref(self, ref: QuestRef) -> QuestChainLease:
        self.bind_calls += 1
        raise AssertionError("semantic admission must precede binding")


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

    assert runtime._maybe_begin_quest_turn_in() is True
    assert runtime._quest_turn_in.pending is not None
    assert runtime.routes and runtime.routes[0][1] == "quest_turn_in"
    assert runtime.chain.bind_calls == 0
    assert runtime._quest_chat_terminal_completion_evidence is not None
    assert runtime._quest_inventory_terminal_completion_evidence is not None


def test_q304_blood_only_is_blocked_without_action_or_binding() -> None:
    runtime = _Harness("304", satisfied=1)

    assert runtime._maybe_begin_quest_turn_in() is True
    assert runtime._quest_turn_in.pending is None
    assert runtime.routes == []
    assert runtime.chain.bind_calls == 0
    assert runtime.stopped_reason is None


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

    assert runtime._maybe_begin_quest_turn_in() is True
    assert runtime._quest_turn_in.pending is None
    assert runtime.routes == []
    assert runtime.chain.bind_calls == 0
    assert (runtime.stopped_reason is not None) is unsafe
