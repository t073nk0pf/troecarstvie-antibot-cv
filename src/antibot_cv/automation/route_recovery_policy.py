"""Cadence and bounded-progress policy for route recovery polling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RouteRecoveryCadence:
    initial_interval_s: float = 0.5
    max_interval_s: float = 5.0
    max_unchanged: int = 20
    interval_s: float = 0.5
    next_poll_at: float = 0.0
    last_fingerprint: tuple[object, ...] | None = None
    unchanged_count: int = 0

    def reset(self) -> None:
        self.interval_s = self.initial_interval_s
        self.next_poll_at = 0.0
        self.last_fingerprint = None
        self.unchanged_count = 0

    def ready(self, now: float) -> bool:
        return now >= self.next_poll_at

    def observe(self, fingerprint: tuple[object, ...], now: float) -> bool:
        changed = fingerprint != self.last_fingerprint
        if changed:
            self.last_fingerprint = fingerprint
            self.unchanged_count = 0
            self.interval_s = self.initial_interval_s
        else:
            self.unchanged_count += 1
            self.interval_s = min(self.max_interval_s, self.interval_s * 2)
        self.next_poll_at = now + self.interval_s
        return changed

    @property
    def exhausted(self) -> bool:
        return self.unchanged_count >= self.max_unchanged


ROUTE_ABSOLUTE_BUDGET_MS = 120_000
ROUTE_MINIMUM_BUDGET_MS = 60_000
ROUTE_STEP_PROGRESS_DEADLINE_S = 20.0
