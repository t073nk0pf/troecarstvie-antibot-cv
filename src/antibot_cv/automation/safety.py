from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from src.antibot_cv.automation.config import AutomationConfig


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = "allowed"


@dataclass
class SafetyGuard:
    config: AutomationConfig
    now: Callable[[], float] = time.monotonic
    paused: bool = False
    dry_run: bool = True
    emergency_stopped: bool = False
    consecutive_errors: int = 0
    action_times: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.dry_run = self.config.dry_run
        self.session_started = self.now()

    def set_dry_run(self, dry_run: bool) -> None:
        self.dry_run = dry_run

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def toggle_live_if_allowed(self) -> bool:
        if not self.config.allow_live_toggle:
            return self.dry_run
        self.dry_run = not self.dry_run
        return self.dry_run

    def emergency_stop(self) -> None:
        self.emergency_stopped = True
        self.paused = True

    def record_error(self) -> SafetyDecision:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.config.safety.max_consecutive_errors:
            self.emergency_stop()
            return SafetyDecision(False, "max_consecutive_errors")
        return SafetyDecision(True)

    def clear_errors(self) -> None:
        self.consecutive_errors = 0

    def allow_action(
        self,
        action_type: str,
        *,
        completed_cycles: int,
        requested_cycles: int,
        is_viewport_move: bool = False,
        viewport_moves_this_search: int = 0,
    ) -> SafetyDecision:
        if self.emergency_stopped:
            return SafetyDecision(False, "emergency_stop")
        if self.paused:
            return SafetyDecision(False, "paused")
        if completed_cycles >= requested_cycles:
            return SafetyDecision(False, "max_cycles")
        if self.now() - self.session_started > self.config.max_session_minutes * 60:
            return SafetyDecision(False, "max_session_minutes")
        if is_viewport_move and viewport_moves_this_search >= self.config.safety.max_viewport_moves_per_search:
            return SafetyDecision(False, "max_viewport_moves_per_search")

        cutoff = self.now() - 60
        while self.action_times and self.action_times[0] < cutoff:
            self.action_times.popleft()
        if len(self.action_times) >= self.config.safety.max_actions_per_minute:
            return SafetyDecision(False, "max_actions_per_minute")
        return SafetyDecision(True)

    def record_action(self) -> None:
        self.action_times.append(self.now())


class HotkeyController:
    """Optional global hotkey listener. Missing pynput fails closed for live mode."""

    def __init__(self, guard: SafetyGuard, logger: object | None = None) -> None:
        self.guard = guard
        self.logger = logger
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False

        def on_press(key: object) -> None:
            key_name = getattr(key, "name", None)
            if key_name == "f8":
                paused = self.guard.toggle_pause()
                _log(self.logger, "hotkey", action_type="toggle_pause", paused=paused)
            elif key_name == "f9":
                dry_run = self.guard.toggle_live_if_allowed()
                _log(self.logger, "hotkey", action_type="toggle_dry_run", dry_run=dry_run)
            elif key_name == "f12":
                self.guard.emergency_stop()
                _log(self.logger, "emergency_stop", action_type="f12")
            elif key_name == "esc":
                self.guard.pause()
                _log(self.logger, "hotkey", action_type="pause")

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.daemon = True
        self._listener.start()
        return True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def _log(logger: object | None, event_type: str, **fields: object) -> None:
    if logger and hasattr(logger, "log_event"):
        logger.log_event(event_type, **fields)
