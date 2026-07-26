"""Pure, shadow-only compilation of active quest cards into semantic plans.

The compiler describes requirements only.  It neither selects capabilities nor
imports an executor/controller, and an incomplete adapter result is never
reported as ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_plan_model import (
    Acquire,
    AllOf,
    AreaObject,
    CombatDrop,
    Gathering,
    InteractNpc,
    Purchase,
    QuestGraph,
    QuestNode,
    QuestPlan,
    Sequence,
    TurnIn,
    Visit,
)


class QuestCompileStatus(str, Enum):
    READY = "ready"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class QuestCompileResult:
    status: QuestCompileStatus
    plan: QuestPlan | None
    diagnostics: tuple[str, ...]

    @property
    def fingerprint(self) -> str | None:
        return self.plan.fingerprint if self.plan is not None else None


class CompilerAdapter(Protocol):
    def __call__(self, entry: ActiveQuestEntry) -> QuestCompileResult: ...


def _sequence(*nodes: QuestNode) -> QuestGraph:
    return QuestGraph(Sequence(tuple(nodes)))


def _q280() -> QuestGraph:
    return _sequence(
        Acquire("Фамильная ступка", 1, AreaObject("Фамильная ступка")),
        TurnIn("Разбойник Аскорд"),
    )


def _q304() -> QuestGraph:
    return _sequence(
        Acquire("Пузырёк крови", 5, CombatDrop("Непобедимый кабан")),
        AllOf((
            Acquire("Светящийся мох", 1, AreaObject("Светящийся мох")),
            Acquire("Пятнистый гриб", 1, AreaObject("Пятнистый гриб")),
            Acquire("Свежие листья кустарника", 5, AreaObject("Свежие листья кустарника")),
        )),
        TurnIn("Колдунья Вилена"),
    )


def _q31() -> QuestGraph:
    return _sequence(
        Acquire("Осиное крыло", 3, CombatDrop("Гигантская оса")),
        Acquire("Панцирь", 3, Purchase("Богдан")),
        InteractNpc("Оксайт"),
        TurnIn("Волхв Алстард"),
    )


def _q360() -> QuestGraph:
    return _sequence(
        Visit("Застава храбрых"),
        InteractNpc(
            "стражу Всебою",
            request="сделайте все, о чем он попросит",
            opaque=True,
        ),
        Visit("Город Арса"),
        TurnIn("богатырю Туру"),
    )


# Declarative recipes are immutable data; tuple order is identity-bearing.
QUEST_RECIPE_REGISTRY: Mapping[str, QuestGraph] = MappingProxyType({
    "31": _q31(),
    "280": _q280(),
    "304": _q304(),
    "360": _q360(),
})
_REGISTRY_TITLES: Mapping[str, str] = MappingProxyType({
    "31": "Поиски Рокоша",
    "280": "Фамильная ступка",
    "304": "Цветочная болезнь",
    "360": "Зов Лихих земель",
})
_REGISTRY_SOURCE_CONTRACTS: Mapping[str, tuple[str, str, tuple[tuple[str, str], ...]]] = MappingProxyType({
    "31": (
        "Добыть 3 осиных крыла, купить 3 панциря у Богдана, взять бычий рог у Оксайта.",
        "unknown",
        (("Городская площадь", "Городская площадь"),),
    ),
    "280": (
        "Вернитесь к разбойнику Аскорду в Земли Пращуров.",
        "dialogue",
        (("Земли Пращуров", "Земли Пращуров"),),
    ),
    "304": (
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, возвращайтесь к колдунье Вилене.",
        "collect",
        (("Непобедимый кабан", "Непобедимый кабан [5]"), ("Пристанище трёх ветров", "Пристанище трёх ветров"), ("Длани Рода", "Длань Рода"), ("Просторах безмолвия", "Просторы безмолвия")),
    ),
    "360": (
        "Отправляйтесь к стражу Всебою на Заставу храбрых и сделайте все, о чем он попросит, затем возвращайтесь к богатырю Туру на городскую площадь Арсы.",
        "unknown",
        (("Заставу храбрых", "Застава храбрых"), ("городскую площадь Арсы", "Город Арса")),
    ),
})


def compile_quest_plan(
    entry: ActiveQuestEntry, adapters: tuple[CompilerAdapter, ...] = (),
) -> QuestCompileResult:
    """Compile one immutable catalogue entry without authorizing execution."""

    if not isinstance(entry, ActiveQuestEntry):
        return QuestCompileResult(QuestCompileStatus.UNSAFE, None, ("entry_invalid",))
    if not entry.id.strip() or not entry.title.strip():
        return QuestCompileResult(QuestCompileStatus.UNSAFE, None, ("quest_identity_invalid",))
    source_reason = _validate_source(entry)
    if source_reason is not None:
        return QuestCompileResult(QuestCompileStatus.UNSAFE, None, (source_reason,))

    recipe = QUEST_RECIPE_REGISTRY.get(entry.id)
    if recipe is not None:
        if entry.title != _REGISTRY_TITLES[entry.id] or not _matches_registry_source(entry):
            return QuestCompileResult(QuestCompileStatus.UNSAFE, None, ("registry_identity_mismatch",))
        diagnostic = "ordered_internal_request_unknown" if entry.id == "360" else "registry_recipe"
        return _ready(entry, recipe, diagnostic)

    for adapter in tuple(adapters):
        result = adapter(entry)
        if not isinstance(result, QuestCompileResult):
            return QuestCompileResult(QuestCompileStatus.UNSAFE, None, ("compiler_adapter_invalid",))
        if result.status is not QuestCompileStatus.UNKNOWN:
            return result
    return QuestCompileResult(QuestCompileStatus.UNKNOWN, None, ("no_compiler_adapter_matched",))


def _validate_source(entry: ActiveQuestEntry) -> str | None:
    data = entry.data
    if not isinstance(data, Mapping) or data.get("status") != "active":
        return "unsafe_active_identity"
    if data.get("id") != entry.id or data.get("title") != entry.title:
        return "unsafe_active_identity"
    objective = data.get("objective")
    if not isinstance(objective, str) or objective != objective.strip() or not objective:
        return "unsafe_missing_objective"
    kind = data.get("objectiveKind")
    if kind is not None and (not isinstance(kind, str) or kind.strip().casefold() not in {"combat", "collect", "dialogue", "travel", "unknown"}):
        return "objective_kind_invalid"
    if _navigation_pairs(data.get("navigation")) is None:
        return "unsafe_navigation_mapping"
    return None


def _navigation_pairs(raw: object) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(raw, (tuple, list)):
        return None
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        text, target = item.get("text"), item.get("target")
        if not isinstance(text, str) or text != text.strip() or not text or not isinstance(target, str) or target != target.strip() or not target:
            return None
        pairs.append((text, target))
    return tuple(pairs)


def _matches_registry_source(entry: ActiveQuestEntry) -> bool:
    objective, kind, navigation = _REGISTRY_SOURCE_CONTRACTS[entry.id]
    return (
        entry.data.get("objective") == objective
        and entry.data.get("objectiveKind") == kind
        and _navigation_pairs(entry.data.get("navigation")) == navigation
    )


def _ready(entry: ActiveQuestEntry, graph: QuestGraph, *diagnostics: str) -> QuestCompileResult:
    return QuestCompileResult(
        QuestCompileStatus.READY,
        QuestPlan(entry.id, entry.title, graph),
        tuple(diagnostics),
    )


# Short aliases ease shadow integration without coupling callers to a class.
CompileStatus = QuestCompileStatus
CompileResult = QuestCompileResult
compile_active_quest = compile_quest_plan


__all__ = [
    "CompileResult", "CompileStatus", "CompilerAdapter", "QUEST_RECIPE_REGISTRY", "QuestCompileResult",
    "QuestCompileStatus", "compile_active_quest", "compile_quest_plan",
]
