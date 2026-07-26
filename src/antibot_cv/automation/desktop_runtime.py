from __future__ import annotations

import subprocess
import sys

import cv2
import numpy as np

from src.antibot_cv.automation.capture import draw_mapping_preview


def activate_configured_app(config: object, args: object) -> bool:
    if getattr(args, "no_activate_app", False):
        return False
    app_name = getattr(args, "activate_app", None)
    if app_name is None:
        app_name = getattr(config, "activate_app", None)
    if not app_name:
        return False
    return activate_app(str(app_name))


def activate_app(app_name: str) -> bool:
    safe_name = app_name.replace('"', '\\"')
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{safe_name}" to activate'],
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        print(f"Could not activate {app_name}: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        print(f"Could not activate {app_name}: {message}", file=sys.stderr)
        return False
    return True


def show_preview(window_name: str, frame: np.ndarray, controller: object) -> None:
    preview = draw_mapping_preview(frame, controller.mapper)
    state_text = f"{controller.state_machine.state.value} cycle={controller.session.cycle_id}"
    cv2.putText(preview, state_text, (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    current_target = getattr(controller, "_current_target", None)
    if current_target is not None:
        cv2.rectangle(
            preview,
            (current_target.bbox.x, current_target.bbox.y),
            (current_target.bbox.right, current_target.bbox.bottom),
            (0, 255, 0),
            2,
        )
        cv2.drawMarker(
            preview,
            (int(current_target.interaction_point.x), int(current_target.interaction_point.y)),
            (0, 255, 255),
            cv2.MARKER_CROSS,
            18,
            2,
        )
    height, width = preview.shape[:2]
    scale = min(1.0, 900 / max(1, width), 650 / max(1, height))
    if scale < 1.0:
        preview = cv2.resize(preview, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.imshow(window_name, preview)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        cv2.destroyWindow(window_name)
