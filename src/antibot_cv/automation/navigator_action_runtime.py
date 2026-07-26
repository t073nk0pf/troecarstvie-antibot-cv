"""Bounded helpers for navigator action fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Mapping, Protocol


class InjectorResult(Protocol):
    ok: bool
    message: str
    client_id: str | None


@dataclass(frozen=True)
class NavigatorFallbackResult:
    ok: bool
    metadata: dict[str, object]
    block_reason: str | None = None


def open_location_navigator_fallback(
    failed: InjectorResult,
    *,
    metadata: Mapping[str, object],
    execute: Callable[[], InjectorResult],
    compact_message: Callable[[str], str],
) -> NavigatorFallbackResult | None:
    """Use the generic navigator only for an explicit missing quest link."""

    try:
        payload = json.loads(failed.message)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or payload.get("message") != "quest_navigator_link_missing":
        return None

    fallback = execute()
    result_metadata = {
        **metadata,
        "navigator_fallback": "location_navigator",
        "fallback_injector_message": compact_message(fallback.message),
        "fallback_injector_client_id": fallback.client_id,
    }
    if fallback.ok:
        return NavigatorFallbackResult(True, result_metadata)
    return NavigatorFallbackResult(
        False,
        result_metadata,
        "injector_open_quest_navigator_fallback_failed:"
        f"{compact_message(fallback.message)}",
    )
