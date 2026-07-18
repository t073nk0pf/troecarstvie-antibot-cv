"""Immutable, deterministic semantic model for quest plans.

This module deliberately contains no execution/runtime dependencies.  The model
is suitable both for planners and for persisted identity checks.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, TypeAlias


class AlternativePolicy(str, Enum):
    """How an ``AnyOf`` behaves when no alternative can be proved safe."""

    FAIL_CLOSED = "fail_closed"


class AcquireSourceKind(str, Enum):
    COMBAT_DROP = "combat_drop"
    AREA_OBJECT = "area_object"
    GATHERING = "gathering"
    PURCHASE = "purchase"


def _required(value: str, name: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(prefix: str, value: Any) -> str:
    payload = _canonical_json(value).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CombatDrop:
    target: str
    kind: AcquireSourceKind = field(default=AcquireSourceKind.COMBAT_DROP, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _required(self.target, "target"))


@dataclass(frozen=True, slots=True)
class AreaObject:
    object_name: str
    kind: AcquireSourceKind = field(default=AcquireSourceKind.AREA_OBJECT, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_name", _required(self.object_name, "object_name"))


@dataclass(frozen=True, slots=True)
class Gathering:
    resource: str
    kind: AcquireSourceKind = field(default=AcquireSourceKind.GATHERING, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", _required(self.resource, "resource"))


@dataclass(frozen=True, slots=True)
class Purchase:
    vendor: str
    kind: AcquireSourceKind = field(default=AcquireSourceKind.PURCHASE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "vendor", _required(self.vendor, "vendor"))


AcquireSource: TypeAlias = CombatDrop | AreaObject | Gathering | Purchase


def _source_data(source: AcquireSource) -> dict[str, Any]:
    if isinstance(source, CombatDrop):
        return {"kind": source.kind.value, "target": source.target}
    if isinstance(source, AreaObject):
        return {"kind": source.kind.value, "object_name": source.object_name}
    if isinstance(source, Gathering):
        return {"kind": source.kind.value, "resource": source.resource}
    if isinstance(source, Purchase):
        return {"kind": source.kind.value, "vendor": source.vendor}
    raise TypeError("unsupported acquire source")


class _IdentityMixin:
    @property
    def step_id(self) -> str:
        return _digest("step", canonical_node(self))


class _RequirementMixin(_IdentityMixin):
    @property
    def requirement_id(self) -> str:
        return _digest("req", canonical_node(self))


@dataclass(frozen=True, slots=True)
class Kill(_RequirementMixin):
    target: str
    count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _required(self.target, "target"))
        _positive(self.count, "count")


@dataclass(frozen=True, slots=True)
class Acquire(_RequirementMixin):
    item: str
    count: int = 1
    source: AcquireSource = field(default_factory=lambda: Gathering("unspecified"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "item", _required(self.item, "item"))
        _positive(self.count, "count")
        if not isinstance(self.source, (CombatDrop, AreaObject, Gathering, Purchase)):
            raise TypeError("source must be a typed acquire source")


@dataclass(frozen=True, slots=True)
class Visit(_RequirementMixin):
    location: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "location", _required(self.location, "location"))


@dataclass(frozen=True, slots=True)
class InteractNpc(_RequirementMixin):
    npc: str
    request: str | None = None
    opaque: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "npc", _required(self.npc, "npc"))
        if self.request is not None:
            request = str(self.request).strip()
            object.__setattr__(self, "request", request or None)
        if not isinstance(self.opaque, bool):
            raise TypeError("opaque must be boolean")
        if self.opaque and self.request is None:
            raise ValueError("opaque NPC interaction requires non-empty request text")


@dataclass(frozen=True, slots=True)
class TurnIn(_RequirementMixin):
    npc: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "npc", _required(self.npc, "npc"))


QuestLeaf: TypeAlias = Kill | Acquire | Visit | InteractNpc | TurnIn


def _nodes(value: tuple["QuestNode", ...], name: str) -> tuple["QuestNode", ...]:
    result = tuple(value)
    if not result:
        raise ValueError(f"{name} must contain at least one node")
    if not all(isinstance(node, (Sequence, AllOf, AnyOf, Kill, Acquire, Visit, InteractNpc, TurnIn)) for node in result):
        raise TypeError(f"{name} contains an unsupported node")
    return result


@dataclass(frozen=True, slots=True)
class Sequence(_IdentityMixin):
    nodes: tuple["QuestNode", ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", _nodes(self.nodes, "nodes"))


@dataclass(frozen=True, slots=True)
class AllOf(_IdentityMixin):
    nodes: tuple["QuestNode", ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", _nodes(self.nodes, "nodes"))


@dataclass(frozen=True, slots=True)
class AnyOf(_IdentityMixin):
    nodes: tuple["QuestNode", ...]
    policy: AlternativePolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", _nodes(self.nodes, "nodes"))
        if self.policy is not AlternativePolicy.FAIL_CLOSED:
            raise ValueError("AnyOf requires the explicit fail_closed policy")


QuestNode: TypeAlias = Sequence | AllOf | AnyOf | QuestLeaf


def iter_requirements(node: QuestNode) -> Iterator[QuestLeaf]:
    """Yield every leaf requirement in deterministic graph order."""

    if isinstance(node, (Sequence, AllOf, AnyOf)):
        for child in node.nodes:
            yield from iter_requirements(child)
        return
    if isinstance(node, (Kill, Acquire, Visit, InteractNpc, TurnIn)):
        yield node
        return
    raise TypeError("unsupported quest node")


def canonical_node(node: QuestNode) -> dict[str, Any]:
    """Return the identity-bearing canonical representation of a node."""

    if isinstance(node, (Sequence, AllOf, AnyOf)):
        value: dict[str, Any] = {
            "kind": {Sequence: "sequence", AllOf: "all_of", AnyOf: "any_of"}[type(node)],
            "nodes": [canonical_node(child) for child in node.nodes],
        }
        if isinstance(node, AnyOf):
            value["policy"] = node.policy.value
        return value
    if isinstance(node, Kill):
        return {"kind": "kill", "target": node.target, "count": node.count}
    if isinstance(node, Acquire):
        return {"kind": "acquire", "item": node.item, "count": node.count, "source": _source_data(node.source)}
    if isinstance(node, Visit):
        return {"kind": "visit", "location": node.location}
    if isinstance(node, InteractNpc):
        return {
            "kind": "interact_npc", "npc": node.npc,
            "request": node.request, "opaque": node.opaque,
        }
    if isinstance(node, TurnIn):
        return {"kind": "turn_in", "npc": node.npc}
    raise TypeError("unsupported quest node")


@dataclass(frozen=True, slots=True)
class QuestGraph:
    root: QuestNode

    def __post_init__(self) -> None:
        if not isinstance(self.root, (Sequence, AllOf, AnyOf, Kill, Acquire, Visit, InteractNpc, TurnIn)):
            raise TypeError("root must be a quest node")
        requirement_ids = [requirement.requirement_id for requirement in iter_requirements(self.root)]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("quest graph contains duplicate leaf requirement IDs")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(canonical_node(self.root)).encode("utf-8")).hexdigest()

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"root": canonical_node(self.root), "fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class QuestPlan:
    quest_id: str
    quest_title: str
    graph: QuestGraph

    def __post_init__(self) -> None:
        object.__setattr__(self, "quest_id", _required(self.quest_id, "quest_id"))
        object.__setattr__(self, "quest_title", _required(self.quest_title, "quest_title"))
        if not isinstance(self.graph, QuestGraph):
            raise TypeError("graph must be a QuestGraph")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.semantic_dict()).encode("utf-8")).hexdigest()

    def semantic_dict(self) -> dict[str, Any]:
        return {"quest_id": self.quest_id, "quest_title": self.quest_title, "root": canonical_node(self.graph.root)}

    def to_canonical_dict(self) -> dict[str, Any]:
        return {**self.semantic_dict(), "fingerprint": self.fingerprint}

    def to_canonical_json(self) -> str:
        return _canonical_json(self.to_canonical_dict())


__all__ = [
    "Acquire", "AcquireSource", "AcquireSourceKind", "AllOf", "AlternativePolicy",
    "AnyOf", "AreaObject", "CombatDrop", "Gathering", "InteractNpc", "Kill",
    "Purchase", "QuestGraph", "QuestLeaf", "QuestNode", "QuestPlan", "Sequence",
    "TurnIn", "Visit", "canonical_node", "iter_requirements",
]
