from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    checks: dict[str, bool]
    messages: list[str]


def run_preflight(*, require_live: bool) -> PreflightResult:
    checks: dict[str, bool] = {}
    messages: list[str] = []

    checks["screen_capture"] = _can_import_and_open_mss(messages)
    checks["mouse_position_read"] = _can_read_mouse_position(messages)
    checks["synthetic_click_api"] = _can_create_mouse_controller(messages)
    checks["global_hotkey_api"] = _can_create_keyboard_listener(messages)
    checks["accessibility_trusted"] = _is_accessibility_trusted(messages)
    checks["chrome_javascript_events"] = _is_chrome_javascript_allowed(messages)

    ok = checks["screen_capture"]
    if require_live:
        ok = checks["screen_capture"]
    if not ok:
        messages.append("Check System Settings -> Privacy & Security -> Screen & System Audio Recording.")
    return PreflightResult(ok=ok, checks=checks, messages=messages)


def _can_import_and_open_mss(messages: list[str]) -> bool:
    try:
        import mss

        with mss.mss() as screen:
            _ = screen.monitors
        return True
    except Exception as exc:
        messages.append(f"screen capture unavailable: {exc}")
        return False


def _can_read_mouse_position(messages: list[str]) -> bool:
    try:
        from pynput.mouse import Controller

        _ = Controller().position
        return True
    except Exception as exc:
        messages.append(f"mouse position unavailable: {exc}")
        return False


def _can_create_mouse_controller(messages: list[str]) -> bool:
    try:
        from pynput.mouse import Controller

        _ = Controller()
        return True
    except Exception as exc:
        messages.append(f"mouse controller unavailable: {exc}")
        return False


def _can_create_keyboard_listener(messages: list[str]) -> bool:
    try:
        from pynput import keyboard

        _ = keyboard
        return True
    except Exception as exc:
        messages.append(f"keyboard listener unavailable: {exc}")
        return False


def _is_accessibility_trusted(messages: list[str]) -> bool:
    try:
        import ApplicationServices

        trusted = bool(ApplicationServices.AXIsProcessTrusted())
    except Exception as exc:
        messages.append(f"accessibility trust check unavailable: {exc}")
        return False
    if not trusted:
        messages.append("current process is not trusted for Accessibility; manual input and hotkeys may be unavailable")
    return trusted


def _is_chrome_javascript_allowed(messages: list[str]) -> bool:
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Google Chrome" to execute active tab of front window javascript "true"',
            ],
            check=False,
            timeout=5,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        messages.append(f"chrome javascript event check unavailable: {exc}")
        return False
    if result.returncode != 0:
        messages.append(
            "Chrome JavaScript from Apple Events is disabled; recovery from non-hunt game screens cannot enter hunt automatically."
        )
        return False
    return result.stdout.strip().lower() == "true"
