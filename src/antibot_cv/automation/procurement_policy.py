"""Action-free, fail-closed planning for quest-only resource procurement.

The observer and future executor remain separate: this module can only produce
an exact-deficit plan from complete, authoritative inputs.  It never invokes a
shop, auction, injector, or action sink.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Sequence


class ProcurementSource(str, Enum):
    AUCTION = "auction"
    NPC_SHOP = "npc_shop"


class ProcurementPlanStatus(str, Enum):
    READY = "ready"
    COMPLETE = "complete"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ProcurementRequirement:
    quest_id: str
    quest_fingerprint: str
    transport_client_id: str
    expected_character_name: str
    source: ProcurementSource
    item_name: str
    required: int
    item_id: str | None = None


@dataclass(frozen=True)
class ProcurementOrder:
    listing_id: str
    item_id: str
    item_name: str
    quantity: int
    unit_price: int
    total_price: int


@dataclass(frozen=True)
class ProcurementPlan:
    status: ProcurementPlanStatus
    reason: str
    source: ProcurementSource | None
    quest_id: str | None
    quest_fingerprint: str | None
    item_id: str | None
    item_name: str | None
    required: int
    inventory_count: int
    deficit: int
    currency: str | None
    balance: int | None
    total_price: int
    shop_id: str | None
    snapshot_id: str | None
    orders: tuple[ProcurementOrder, ...]


def plan_exact_deficit_procurement(
    requirement: ProcurementRequirement,
    inventory_snapshot: Mapping[str, object],
    procurement_snapshot: Mapping[str, object],
    *,
    now: datetime | None = None,
    max_snapshot_age_seconds: int = 30,
) -> ProcurementPlan:
    """Return READY only when an exact, round-currency deficit is affordable."""

    invalid = _validate_requirement(requirement)
    if invalid:
        return _unsafe(invalid)
    observed_at = datetime.now(timezone.utc) if now is None else now
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or not _positive_int(max_snapshot_age_seconds)
    ):
        return _unsafe("freshness_policy_invalid", requirement=requirement)
    inventory_count, inventory_reason = _confirmed_inventory_count(
        requirement,
        inventory_snapshot,
        now=observed_at,
        max_age_seconds=max_snapshot_age_seconds,
    )
    if inventory_reason:
        return _unsafe(inventory_reason, requirement=requirement)
    assert inventory_count is not None
    deficit = requirement.required - inventory_count
    if deficit <= 0:
        return ProcurementPlan(
            ProcurementPlanStatus.COMPLETE,
            "requirement_already_satisfied",
            requirement.source,
            requirement.quest_id,
            requirement.quest_fingerprint,
            requirement.item_id,
            requirement.item_name,
            requirement.required,
            inventory_count,
            0,
            None,
            None,
            0,
            None,
            None,
            (),
        )

    snapshot, reason = _validate_snapshot(
        procurement_snapshot,
        requirement,
        now=observed_at,
        max_age_seconds=max_snapshot_age_seconds,
    )
    if reason:
        return _unsafe(reason, requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    assert snapshot is not None
    matching, reason = _matching_listings(requirement, snapshot["listings"])
    if reason:
        return _unsafe(reason, requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    assert matching

    orders: list[ProcurementOrder] = []
    remaining = deficit
    for listing in sorted(matching, key=lambda item: (item["unitPrice"], item["listingId"])):
        quantity = min(remaining, listing["availableQuantity"])
        if quantity <= 0:
            continue
        orders.append(
            ProcurementOrder(
                listing_id=listing["listingId"],
                item_id=listing["itemId"],
                item_name=listing["itemName"],
                quantity=quantity,
                unit_price=listing["unitPrice"],
                total_price=listing["unitPrice"] * quantity,
            )
        )
        remaining -= quantity
        if remaining == 0:
            break
    if remaining:
        return _unsafe("available_quantity_insufficient", requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    total_price = sum(order.total_price for order in orders)
    balance = snapshot["balance"]
    if total_price > balance:
        return _unsafe("round_balance_insufficient", requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    if sum(order.quantity for order in orders) != deficit:
        return _unsafe("exact_deficit_not_preserved", requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    item_ids = {order.item_id for order in orders}
    if len(item_ids) != 1:
        return _unsafe("item_identity_ambiguous", requirement=requirement, inventory_count=inventory_count, deficit=deficit)
    return ProcurementPlan(
        ProcurementPlanStatus.READY,
        "exact_deficit_affordable",
        requirement.source,
        requirement.quest_id,
        requirement.quest_fingerprint,
        next(iter(item_ids)),
        requirement.item_name,
        requirement.required,
        inventory_count,
        deficit,
        "round",
        balance,
        total_price,
        snapshot["shopId"],
        snapshot["snapshotId"],
        tuple(orders),
    )


def _validate_requirement(requirement: object) -> str | None:
    if not isinstance(requirement, ProcurementRequirement):
        return "requirement_invalid"
    if not _text(requirement.quest_id) or not _text(requirement.quest_fingerprint):
        return "requirement_identity_invalid"
    if not isinstance(requirement.source, ProcurementSource):
        return "requirement_source_invalid"
    if not _text(requirement.transport_client_id) or not _text(requirement.expected_character_name):
        return "requirement_actor_binding_invalid"
    if not isinstance(requirement.item_name, str) or requirement.item_name != requirement.item_name.strip() or not _text(requirement.item_name):
        return "requirement_item_name_invalid"
    if not _positive_int(requirement.required):
        return "requirement_quantity_invalid"
    if requirement.item_id is not None and not _text(requirement.item_id):
        return "requirement_item_id_invalid"
    return None


def _confirmed_inventory_count(
    requirement: ProcurementRequirement,
    snapshot: Mapping[str, object],
    *,
    now: datetime,
    max_age_seconds: int,
) -> tuple[int | None, str | None]:
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("complete") is not True
        or snapshot.get("truncated") is not False
        or not _text(snapshot.get("snapshotId"))
    ):
        return None, "inventory_snapshot_unconfirmed"
    binding_reason = _binding_reason(snapshot, requirement, prefix="inventory")
    if binding_reason:
        return None, binding_reason
    freshness_reason = _freshness_reason(snapshot.get("generatedAt"), now, max_age_seconds, prefix="inventory")
    if freshness_reason:
        return None, freshness_reason
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)) or len(raw_items) > 200:
        return None, "inventory_items_invalid"
    total = 0
    observed_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            return None, "inventory_item_invalid"
        name, item_id, count = item.get("itemName"), item.get("itemId"), item.get("count")
        if not _text(name) or not _text(item_id) or not _nonnegative_int(count):
            return None, "inventory_item_invalid"
        if _normalized(name) != _normalized(requirement.item_name):
            continue
        observed_ids.add(item_id)
        if requirement.item_id is None or item_id == requirement.item_id:
            total += count
    if requirement.item_id is None and len(observed_ids) > 1:
        return None, "inventory_item_identity_ambiguous"
    return total, None


def _validate_snapshot(
    raw: Mapping[str, object],
    requirement: ProcurementRequirement,
    *,
    now: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, object] | None, str | None]:
    if (
        not isinstance(raw, Mapping)
        or raw.get("complete") is not True
        or raw.get("truncated") is not False
    ):
        return None, "procurement_snapshot_incomplete"
    if raw.get("source") != requirement.source.value:
        return None, "procurement_source_mismatch"
    if not _text(raw.get("shopId")) or not _text(raw.get("snapshotId")) or not _text(raw.get("generatedAt")):
        return None, "procurement_snapshot_identity_invalid"
    binding_reason = _binding_reason(raw, requirement, prefix="procurement")
    if binding_reason:
        return None, binding_reason
    freshness_reason = _freshness_reason(raw.get("generatedAt"), now, max_age_seconds, prefix="procurement")
    if freshness_reason:
        return None, freshness_reason
    if raw.get("currency") != "round" or not _nonnegative_safe_int(raw.get("balance")):
        return None, "round_currency_unconfirmed"
    listings = raw.get("listings")
    if not isinstance(listings, Sequence) or isinstance(listings, (str, bytes)) or not listings or len(listings) > 100:
        return None, "procurement_listings_invalid"
    return {
        "source": raw["source"],
        "shopId": raw["shopId"],
        "snapshotId": raw["snapshotId"],
        "balance": raw["balance"],
        "listings": listings,
    }, None


def _matching_listings(
    requirement: ProcurementRequirement, listings: Sequence[object]
) -> tuple[list[dict[str, object]], str | None]:
    matches: list[dict[str, object]] = []
    matching_name_ids: set[str] = set()
    seen_listing_ids: set[str] = set()
    for raw in listings:
        if not isinstance(raw, Mapping):
            return [], "procurement_listing_invalid"
        listing_id, item_id, name = raw.get("listingId"), raw.get("itemId"), raw.get("itemName")
        unit, total, available = raw.get("unitPrice"), raw.get("totalPrice"), raw.get("availableQuantity")
        if (
            not _text(listing_id)
            or not _text(item_id)
            or not _text(name)
            or not _positive_safe_int(unit)
            or not _positive_safe_int(available)
            or not _positive_safe_int(total)
            or unit > _MAX_SAFE_INTEGER // available
            or total != unit * available
            or raw.get("currency") not in {"round", "premium"}
        ):
            return [], "procurement_listing_invalid"
        if listing_id in seen_listing_ids:
            return [], "listing_identity_ambiguous"
        seen_listing_ids.add(listing_id)
        if _normalized(name) != _normalized(requirement.item_name):
            continue
        matching_name_ids.add(item_id)
        if requirement.item_id is None or item_id == requirement.item_id:
            if raw.get("currency") != "round":
                return [], "premium_currency_forbidden"
            matches.append(dict(raw))
    if requirement.item_id is None and len(matching_name_ids) > 1:
        return [], "item_identity_ambiguous"
    if not matches:
        return [], "exact_item_listing_missing"
    return matches, None


def _unsafe(
    reason: str,
    *,
    requirement: ProcurementRequirement | None = None,
    inventory_count: int = 0,
    deficit: int = 0,
) -> ProcurementPlan:
    return ProcurementPlan(
        ProcurementPlanStatus.UNSAFE,
        reason,
        requirement.source if requirement else None,
        requirement.quest_id if requirement else None,
        requirement.quest_fingerprint if requirement else None,
        requirement.item_id if requirement and _text(requirement.item_id) else None,
        requirement.item_name if requirement and _text(requirement.item_name) else None,
        requirement.required if requirement and _positive_int(requirement.required) else 0,
        inventory_count,
        deficit,
        None,
        None,
        0,
        None,
        None,
        (),
    )


def _binding_reason(
    snapshot: Mapping[str, object], requirement: ProcurementRequirement, *, prefix: str
) -> str | None:
    request_scope = snapshot.get("requestScope")
    expected = {
        "transportClientId": requirement.transport_client_id,
        "expectedCharacterName": requirement.expected_character_name,
        "observedCharacterName": requirement.expected_character_name,
        "characterStatus": "available",
    }
    scope_matches = isinstance(request_scope, Mapping) and request_scope.get("questId") == requirement.quest_id and request_scope.get("questFingerprint") == requirement.quest_fingerprint
    if any(snapshot.get(key) != value for key, value in expected.items()) or not scope_matches:
        return f"{prefix}_binding_mismatch"
    return None


def _freshness_reason(
    raw: object, now: datetime, max_age_seconds: int, *, prefix: str
) -> str | None:
    if not isinstance(raw, str):
        return f"{prefix}_generated_at_invalid"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"{prefix}_generated_at_invalid"
    if parsed.tzinfo is None:
        return f"{prefix}_generated_at_invalid"
    age = (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if age < -5 or age > max_age_seconds:
        return f"{prefix}_snapshot_stale"
    return None


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 240


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _positive_safe_int(value: object) -> bool:
    return _positive_int(value) and value <= _MAX_SAFE_INTEGER


def _nonnegative_safe_int(value: object) -> bool:
    return _nonnegative_int(value) and value <= _MAX_SAFE_INTEGER
