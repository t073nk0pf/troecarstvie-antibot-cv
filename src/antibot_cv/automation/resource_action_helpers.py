from __future__ import annotations

import json
import time


def resource_percent_from_open_result(
    parsed: dict[str, object],
    percent_key: str,
) -> float | None:
    resources = parsed.get("resources")
    if not isinstance(resources, dict):
        return None
    value = resources.get(percent_key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resource_percent_from_snapshot(
    injector: object,
    percent_key: str,
    *,
    client_id: str | None = None,
) -> float | None:
    try:
        kwargs: dict[str, object] = {"timeout_s": 2.5}
        if client_id:
            kwargs["client_id"] = client_id
        result = injector.execute("resource_snapshot", **kwargs)  # type: ignore[attr-defined]
    except Exception:
        return None
    if not getattr(result, "ok", False):
        return None
    try:
        parsed = json.loads(str(getattr(result, "message", "")))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(percent_key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def wait_resource_percent_after_use(
    injector: object,
    percent_key: str,
    previous_percent: object | None,
    *,
    client_id: str | None = None,
    timeout_s: float = 3.0,
    interval_s: float = 0.5,
) -> float | None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        previous = None if previous_percent is None else float(previous_percent)
    except (TypeError, ValueError):
        previous = None
    last_seen: float | None = None
    while True:
        current = resource_percent_from_snapshot(injector, percent_key, client_id=client_id)
        if current is not None:
            last_seen = current
            if previous is not None and current > previous:
                return current
            if previous is None:
                return current
        if time.monotonic() >= deadline:
            return last_seen
        time.sleep(max(0.05, interval_s))


def refresh_resource_source_after_use(
    injector: object,
    *,
    client_id: str | None = None,
) -> object | None:
    try:
        kwargs: dict[str, object] = {"timeout_s": 2.5}
        if client_id:
            kwargs["client_id"] = client_id
        return injector.execute("resource_refresh", **kwargs)  # type: ignore[attr-defined]
    except Exception:
        return None
