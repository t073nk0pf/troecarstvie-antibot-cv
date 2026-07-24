"""Quest-local outcome helpers shared by durable coordinators."""

from __future__ import annotations

def local_quarantine_reason(prefix: str, reason: str) -> str:
    value = "_".join(part for part in (prefix, reason) if part)
    if not value or len(value) > 160 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value
    ):
        raise ValueError("local quarantine reason is invalid")
    return value


__all__ = ["local_quarantine_reason"]
