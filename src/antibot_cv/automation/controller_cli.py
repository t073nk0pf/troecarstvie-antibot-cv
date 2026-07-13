from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Iterable

import cv2
import numpy as np

from src.antibot_cv.automation.capture import calibrate_capture
from src.antibot_cv.automation.actions import ActionRequest, LiveMacActionSink
from src.antibot_cv.automation.controller import (
    AutomationController,
    AutomationRunOptions,
    load_config,
    run_automation,
)
from src.antibot_cv.automation.preflight import run_preflight
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.detection.resources import ResourceDetector, ResourceStatus
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger
from src.antibot_cv.telemetry.m1_recovery import assess_m1_recovery
from src.antibot_cv.viewport.coordinates import Rect


DEFAULT_TEMPLATE_PATHS: dict[str, str] = {
    "attack_button": "assets/templates/location/attack_button.png",
    "battle_panel": "assets/templates/battle/battle_panel.png",
    "enemy_header": "assets/templates/battle/enemy_header.png",
    "timer_hourglass": "assets/templates/battle/timer_hourglass.png",
    "ability_bar": "assets/templates/battle/ability_bar.png",
    "victory_popup": "assets/templates/battle_end/victory_popup.png",
    "exit_button": "assets/templates/battle_end/exit_button.png",
    "statistics_header": "assets/templates/statistics/statistics_header.png",
    "team_tables": "assets/templates/statistics/team_tables.png",
    "statistics_buttons": "assets/templates/statistics/statistics_buttons.png",
    "hunt_button": "assets/templates/statistics/hunt_button.png",
    "direction_pad": "assets/templates/direction_pad/direction_pad.png",
    "steppe_jackal": "assets/templates/targets/steppe_jackal.png",
    "young_lynx": "assets/templates/targets/young_lynx.png",
}

def command_calibrate(args: argparse.Namespace) -> int:
    config = load_config(args.config).capture
    try:
        report = calibrate_capture(config)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        print("Check macOS Screen & System Audio Recording permission.", file=sys.stderr)
        return 2

def command_validate_templates(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = TemplateRegistry.from_file(config.templates_path)
    issues = registry.validate()
    payload = [{"template_id": issue.template_id, "path": issue.path, "message": issue.message} for issue in issues]
    print(json.dumps({"ok": not issues, "issues": payload}, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if issues else 0


def command_assess_m1_recovery(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    try:
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"ok": False, "events": str(events_path), "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    result = assess_m1_recovery(events)
    payload = {"ok": True, "events": str(events_path), **result}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["offline_ready"] else 1

def command_inspect_resources(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.input:
        frame = cv2.imread(str(args.input), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"Could not read image: {args.input}", file=sys.stderr)
            return 1
    else:
        preflight = run_preflight(require_live=False)
        if not preflight.ok:
            print(json.dumps({"preflight_ok": False, "checks": preflight.checks, "messages": preflight.messages}, indent=2), file=sys.stderr)
            return 2
        _activate_configured_app(config, args)
        from src.antibot_cv.automation.capture import ScreenCapture

        with ScreenCapture(config.capture) as capture:
            frame = capture.capture_frame()
    status = ResourceDetector(config.resources).detect(frame)
    payload = _resource_status_payload(status)
    if args.output_frame:
        output_frame = Path(args.output_frame)
        output_frame.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_frame), frame)
        payload["output_frame"] = str(output_frame)
    if args.output_overlay:
        output_overlay = Path(args.output_overlay)
        output_overlay.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_overlay), _draw_resource_overlay(frame, config.resources, status))
        payload["output_overlay"] = str(output_overlay)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

def _parse_target_level_args(args: argparse.Namespace) -> tuple[int, ...] | None:
    values: list[object] = []
    for attr in ("target_level", "target_levels"):
        raw = getattr(args, attr, None)
        if raw is None:
            continue
        if isinstance(raw, list):
            values.extend(raw)
        else:
            values.append(raw)
    if not values:
        return None
    levels: list[int] = []
    for value in values:
        for part in str(value).replace(",", " ").split():
            try:
                level = int(part)
            except ValueError as exc:
                raise SystemExit(f"target level must be integer: {part}") from exc
            if level <= 0:
                raise SystemExit(f"target level must be positive: {part}")
            if level not in levels:
                levels.append(level)
    return tuple(sorted(levels))

def command_run(args: argparse.Namespace) -> int:
    try:
        run_automation(
            AutomationRunOptions(
                config_path=args.config,
                live=bool(args.live),
                preview=bool(args.preview),
                max_cycles=args.max_cycles,
                max_session_minutes=args.max_session_minutes,
                target_allowed_levels=_parse_target_level_args(args),
                target_allowed_names=tuple(getattr(args, "target_name", None) or ()) or None,
                goal_level=getattr(args, "goal_level", None),
                start_delay=args.start_delay,
                activate_app=args.activate_app,
                no_activate_app=bool(args.no_activate_app),
                hotkeys=bool(args.hotkeys),
                open_hunt_on_start=bool(args.open_hunt_on_start),
                browser_client_id=getattr(args, "client_id", None),
            )
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0

def command_replay(args: argparse.Namespace) -> int:
    config = load_config(args.config).with_overrides(
        dry_run=True,
        max_cycles=args.max_cycles,
        max_session_minutes=args.max_session_minutes,
    )
    controller = AutomationController(config, sink_mode="replay")
    controller.start()
    for frame_path in _iter_frame_paths(Path(args.input)):
        frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if frame is None:
            continue
        controller.process_frame(frame)
        if controller.state_machine.state == GameState.STOPPED:
            break
    summary = controller.finish()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

def command_capture_template(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from src.antibot_cv.automation.capture import ScreenCapture

    with ScreenCapture(config.capture) as capture:
        frame = capture.capture_frame()
    roi = cv2.selectROI("capture-template", frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow("capture-template")
    x, y, width, height = [int(value) for value in roi]
    if width <= 0 or height <= 0:
        print("No ROI selected.", file=sys.stderr)
        return 1
    output_path = Path(args.output or DEFAULT_TEMPLATE_PATHS.get(args.template_id, f"assets/templates/{args.template_id}.png"))
    if output_path.exists() and not args.overwrite:
        print(f"Refusing to overwrite existing template: {output_path}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame[y : y + height, x : x + width])
    entry = {
        "template_id": args.template_id,
        "path": str(output_path),
        "threshold": args.threshold,
        "scales": [1.0],
        "search_roi": {"x": x, "y": y, "width": width, "height": height},
        "max_results": 1,
    }
    templates_config = Path(args.templates_config or config.templates_path)
    updated = False
    if args.update_config:
        _upsert_template_config(templates_config, entry)
        updated = True
    print(
        json.dumps(
            {"saved": str(output_path), "updated_config": updated, "templates_config": str(templates_config), "config_entry": entry},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0

def command_injector_status(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    deadline = time.monotonic() + max(0.0, float(args.timeout))
    while time.monotonic() < deadline and not server.client_seen_recently(within_s=1.0):
        time.sleep(0.05)
    selected_client_id = getattr(args, "client_id", None)
    clients = server.client_snapshots(within_s=2.0)
    selected_client = server.client_snapshot(selected_client_id) if selected_client_id else None
    if selected_client is None and len([client for client in clients if client.get("client_seen") and client.get("version_ok")]) == 1:
        selected_client = [client for client in clients if client.get("client_seen") and client.get("version_ok")][0]
    client_seen = bool(selected_client.get("client_seen")) if selected_client is not None else server.client_seen_recently(within_s=2.0)
    version_ok = bool(selected_client.get("version_ok")) if selected_client is not None else server.last_client_version == CURRENT_BRIDGE_VERSION
    payload = {
        "ok": client_seen and version_ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_seen": client_seen,
        "client_id": None if selected_client is None else selected_client.get("client_id"),
        "client_version": None if selected_client is None else selected_client.get("client_version"),
        "required_version": CURRENT_BRIDGE_VERSION,
        "version_ok": version_ok,
        "clients": clients,
        "extension_path": str((Path.cwd() / "browser_injector").resolve()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if client_seen and version_ok else 1

def command_control_server(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector
    from src.antibot_cv.automation.control_server import AutomationControlApi

    server = global_browser_injector()
    server.start()
    api = AutomationControlApi(server, default_config=args.config, allow_live=bool(args.live))
    server.set_api_handler(api.handle)
    print(
        json.dumps(
            {
                "ok": True,
                "message": "control_server_started",
                "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
                "config": args.config,
                "live_allowed": bool(args.live),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        api.stop()
        server.set_api_handler(None)
        return 0

def command_injector_probe(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("probe_page", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["probe"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_inspect(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "inspect_functions",
        {"names": names},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["inspect"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_layout(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "layout",
        {"mode": args.mode, "chatHeight": int(args.chat_height), "stretch": args.stretch},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_layout_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("layout_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["snapshot"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_state_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    include = [value.strip() for item in args.include for value in str(item).split(",") if value.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "state_snapshot",
        {"include": include},
        timeout_s=max(0.1, float(args.timeout)),
        client_id=getattr(args, "client_id", None),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or getattr(args, "client_id", None) or server.last_client_id,
    }
    try:
        payload["snapshot"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_hunt_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("hunt_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["snapshot"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_hunt_candidates(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("hunt_candidates", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["hunt"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_bot_info(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "hunt_bot_info",
        {"bot_id": int(args.bot_id)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_visible_targets(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    allowed_levels = _parse_target_level_args(args)
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "visible_hunt_targets",
        {"margin": int(args.margin), "names": names, "allowedLevels": list(allowed_levels or ())},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["visible"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_resource_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("resource_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            resources = json.loads(result.message)
            if isinstance(resources, dict) and not args.debug:
                resources = {
                    "ok": resources.get("ok"),
                    "healthPercent": resources.get("healthPercent"),
                    "prowessPercent": resources.get("prowessPercent"),
                    "healthCandidate": resources.get("healthCandidate"),
                    "prowessCandidate": resources.get("prowessCandidate"),
                    "candidateCount": resources.get("candidateCount"),
                    "generatedAt": resources.get("generatedAt"),
                    "bridgeVersion": resources.get("bridgeVersion"),
                }
            payload["resources"] = resources
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_inventory_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "inventory_snapshot",
        {"names": names, "open": bool(args.open)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["inventory"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_use_recovery_items(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    health_names = [name.strip() for item in args.health_names for name in item.split(",") if name.strip()]
    prowess_names = [name.strip() for item in args.prowess_names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    logger = InMemoryEventLogger(dry_run=False)
    sink = LiveMacActionSink(logger)
    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            dry_run=False,
            metadata={
                "health_names": health_names,
                "prowess_names": prowess_names,
                "use_when_below_percent": float(args.threshold),
                "force_use": bool(args.force_use),
                "use_if_resources_missing": bool(args.use_if_missing),
                "open_hunt_after": bool(args.open_hunt_after),
                "timeout_s": max(0.1, float(args.timeout)),
                "health_restore_percent": float(args.health_restore_percent),
                "prowess_restore_percent": float(args.prowess_restore_percent),
                "max_uses_per_resource": int(args.max_uses_per_resource),
                "inventory_open_delay_ms": int(args.inventory_open_delay_ms),
                "confirm_delay_ms": int(args.confirm_delay_ms),
                "between_items_delay_ms": int(args.between_items_delay_ms),
            },
        )
    )
    payload: dict[str, object] = {
        "ok": ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": server.last_client_id,
        "events": logger.events,
    }
    if not ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1

def command_injector_attack_bot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "attack_bot",
        {"bot_id": int(args.bot_id), "confirmed": 1 if args.confirmed else 0},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_attack_visible(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    allowed_levels = _parse_target_level_args(args)
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "attack_visible_bot",
        {
            "margin": int(args.margin),
            "names": names,
            "allowedLevels": list(allowed_levels or ()),
            "confirmed": 1 if args.confirmed else 0,
        },
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_battle_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("battle_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["battle"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def command_injector_use_skill(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "use_skill_slot",
        {"slot": int(args.slot)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1

def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return max(0.0, min(100.0, number))

def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _upsert_template_config(path: Path, entry: dict[str, object]) -> None:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {"templates": []}
    templates = raw.setdefault("templates", [])
    for index, current in enumerate(templates):
        if current.get("template_id") == entry["template_id"]:
            templates[index] = {**current, **entry}
            break
    else:
        templates.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def _resource_status_payload(status: ResourceStatus) -> dict[str, object]:
    def bar_payload(bar: object) -> dict[str, object]:
        bbox = getattr(bar, "bbox")
        return {
            "detected": getattr(bar, "detected"),
            "percent": getattr(bar, "percent"),
            "confidence": getattr(bar, "confidence"),
            "bbox": None
            if bbox is None
            else {"x": bbox.x, "y": bbox.y, "width": bbox.width, "height": bbox.height},
        }

    return {
        "complete": status.complete,
        "health": bar_payload(status.health),
        "prowess": bar_payload(status.prowess),
    }

def _draw_resource_overlay(frame: np.ndarray, config: object, status: ResourceStatus) -> np.ndarray:
    preview = frame.copy()

    def draw_rect(rect: Rect | None, color: tuple[int, int, int], label: str) -> None:
        if rect is None:
            return
        cv2.rectangle(preview, (rect.x, rect.y), (rect.right, rect.bottom), color, 2)
        cv2.putText(
            preview,
            label,
            (rect.x, max(12, rect.y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    draw_rect(getattr(config, "roi", None), (0, 255, 255), "resource search roi")
    draw_rect(getattr(config, "health_bar_roi", None), (0, 0, 255), "health roi")
    draw_rect(getattr(config, "prowess_bar_roi", None), (255, 0, 0), "prowess roi")
    draw_rect(status.health.bbox, (0, 255, 0), f"health {status.health.percent}")
    draw_rect(status.prowess.bbox, (255, 255, 0), f"prowess {status.prowess.percent}")
    return preview


def _iter_frame_paths(input_dir: Path) -> Iterable[Path]:
    extensions = {".png", ".jpg", ".jpeg", ".bmp"}
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.iterdir() if path.suffix.lower() in extensions)


def _add_client_id_arg(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--client-id", default=None, help="Target browser injector client id")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local bounded CV automation harness")
    parser.add_argument("--config", default=None, help="Path to automation config JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--config", default=None)
    calibrate.set_defaults(func=command_calibrate)

    run = subparsers.add_parser("run")
    run.add_argument("--config", default=None)
    run.add_argument("--preview", action="store_true")
    run.add_argument("--live", action="store_true")
    run.add_argument("--max-cycles", type=int, default=None)
    run.add_argument("--max-session-minutes", type=int, default=None)
    run.add_argument("--target-level", action="append", default=[])
    run.add_argument("--target-levels", nargs="*", default=[])
    run.add_argument("--target-name", action="append", default=[], help="Allowed mob name; repeat for several names")
    run.add_argument("--goal-level", type=int, default=None, help="Stop after the character reaches this level")
    run.add_argument("--start-delay", type=float, default=None)
    run.add_argument("--activate-app", default=None)
    run.add_argument("--no-activate-app", action="store_true")
    run.add_argument("--hotkeys", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--open-hunt-on-start", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--client-id", default=None, help="Target browser injector client id when several Chrome windows are connected")
    run.set_defaults(func=command_run)

    control_server = subparsers.add_parser("control-server")
    control_server.add_argument("--config", default="config/automation.local.json")
    control_server.add_argument("--live", action="store_true", help="Explicitly allow live runs requested by the extension")
    control_server.set_defaults(func=command_control_server)

    validate = subparsers.add_parser("validate-templates")
    validate.add_argument("--config", default=None)
    validate.set_defaults(func=command_validate_templates)

    assess_recovery = subparsers.add_parser("assess-m1-recovery")
    assess_recovery.add_argument("--events", required=True, help="Path to a run events.jsonl file")
    assess_recovery.set_defaults(func=command_assess_m1_recovery)

    inspect_resources = subparsers.add_parser("inspect-resources")
    inspect_resources.add_argument("--config", default=None)
    inspect_resources.add_argument("--input", default=None)
    inspect_resources.add_argument("--output-frame", default=None)
    inspect_resources.add_argument("--output-overlay", default=None)
    inspect_resources.add_argument("--activate-app", default=None)
    inspect_resources.add_argument("--no-activate-app", action="store_true")
    inspect_resources.set_defaults(func=command_inspect_resources)

    capture_template = subparsers.add_parser("capture-template")
    capture_template.add_argument("--config", default=None)
    capture_template.add_argument("--template-id", required=True)
    capture_template.add_argument("--output", default=None)
    capture_template.add_argument("--templates-config", default=None)
    capture_template.add_argument("--threshold", type=float, default=0.8)
    capture_template.add_argument("--overwrite", action="store_true")
    capture_template.add_argument("--update-config", action=argparse.BooleanOptionalAction, default=True)
    capture_template.set_defaults(func=command_capture_template)

    replay = subparsers.add_parser("replay")
    replay.add_argument("--config", default=None)
    replay.add_argument("--input", required=True)
    replay.add_argument("--max-cycles", type=int, default=None)
    replay.add_argument("--max-session-minutes", type=int, default=None)
    replay.set_defaults(func=command_replay)

    injector_status = subparsers.add_parser("injector-status")
    injector_status.add_argument("--timeout", type=float, default=5.0)
    _add_client_id_arg(injector_status)
    injector_status.set_defaults(func=command_injector_status)

    injector_probe = subparsers.add_parser("injector-probe")
    injector_probe.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_probe)
    injector_probe.set_defaults(func=command_injector_probe)

    injector_inspect = subparsers.add_parser("injector-inspect")
    injector_inspect.add_argument(
        "--names",
        nargs="+",
        default=[
            "huntAttack",
            "botAttack",
            "getTarget",
            "getHuntApp",
            "useSkill",
            "fightOver",
            "fightFinished",
            "userAttack",
            "processMenu",
        ],
    )
    injector_inspect.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_inspect)
    injector_inspect.set_defaults(func=command_injector_inspect)

    injector_layout = subparsers.add_parser("injector-layout")
    injector_layout.add_argument("--mode", choices=["wide", "normal"], default="wide")
    injector_layout.add_argument("--chat-height", type=int, default=140)
    injector_layout.add_argument("--stretch", choices=["fit-width", "fit", "off"], default="fit-width")
    injector_layout.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_layout)
    injector_layout.set_defaults(func=command_injector_layout)

    injector_layout_snapshot = subparsers.add_parser("injector-layout-snapshot")
    injector_layout_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_layout_snapshot)
    injector_layout_snapshot.set_defaults(func=command_injector_layout_snapshot)

    injector_state_snapshot = subparsers.add_parser("injector-state-snapshot")
    injector_state_snapshot.add_argument(
        "--include",
        nargs="*",
        default=["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"],
    )
    injector_state_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_state_snapshot)
    injector_state_snapshot.set_defaults(func=command_injector_state_snapshot)

    injector_hunt_snapshot = subparsers.add_parser("injector-hunt-snapshot")
    injector_hunt_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_hunt_snapshot)
    injector_hunt_snapshot.set_defaults(func=command_injector_hunt_snapshot)

    injector_hunt_candidates = subparsers.add_parser("injector-hunt-candidates")
    injector_hunt_candidates.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_hunt_candidates)
    injector_hunt_candidates.set_defaults(func=command_injector_hunt_candidates)

    injector_bot_info = subparsers.add_parser("injector-bot-info")
    injector_bot_info.add_argument("--bot-id", type=int, required=True)
    injector_bot_info.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_bot_info)
    injector_bot_info.set_defaults(func=command_injector_bot_info)

    injector_visible_targets = subparsers.add_parser("injector-visible-targets")
    injector_visible_targets.add_argument("--margin", type=int, default=35)
    injector_visible_targets.add_argument("--names", nargs="*", default=[])
    injector_visible_targets.add_argument("--target-level", action="append", default=[])
    injector_visible_targets.add_argument("--target-levels", nargs="*", default=[])
    injector_visible_targets.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_visible_targets)
    injector_visible_targets.set_defaults(func=command_injector_visible_targets)

    injector_resource_snapshot = subparsers.add_parser("injector-resource-snapshot")
    injector_resource_snapshot.add_argument("--timeout", type=float, default=10.0)
    injector_resource_snapshot.add_argument("--debug", action="store_true")
    _add_client_id_arg(injector_resource_snapshot)
    injector_resource_snapshot.set_defaults(func=command_injector_resource_snapshot)

    injector_inventory_snapshot = subparsers.add_parser("injector-inventory-snapshot")
    injector_inventory_snapshot.add_argument("--names", nargs="*", default=["малый бурдюк жизни", "бурдюк жизни", "малый бурдюк удали", "бурдюк удали"])
    injector_inventory_snapshot.add_argument("--open", action=argparse.BooleanOptionalAction, default=True)
    injector_inventory_snapshot.add_argument("--timeout", type=float, default=8.0)
    _add_client_id_arg(injector_inventory_snapshot)
    injector_inventory_snapshot.set_defaults(func=command_injector_inventory_snapshot)

    injector_recovery_items = subparsers.add_parser("injector-use-recovery-items")
    injector_recovery_items.add_argument("--health-names", nargs="*", default=["малый бурдюк жизни", "бурдюк жизни"])
    injector_recovery_items.add_argument("--prowess-names", nargs="*", default=["малый бурдюк удали", "бурдюк удали"])
    injector_recovery_items.add_argument("--threshold", type=float, default=90.0)
    injector_recovery_items.add_argument("--force-use", action="store_true")
    injector_recovery_items.add_argument("--health-restore-percent", type=float, default=40.0)
    injector_recovery_items.add_argument("--prowess-restore-percent", type=float, default=30.0)
    injector_recovery_items.add_argument("--max-uses-per-resource", type=int, default=4)
    injector_recovery_items.add_argument("--inventory-open-delay-ms", type=int, default=1500)
    injector_recovery_items.add_argument("--confirm-delay-ms", type=int, default=700)
    injector_recovery_items.add_argument("--between-items-delay-ms", type=int, default=500)
    injector_recovery_items.add_argument("--use-if-missing", action=argparse.BooleanOptionalAction, default=True)
    injector_recovery_items.add_argument("--open-hunt-after", action=argparse.BooleanOptionalAction, default=True)
    injector_recovery_items.add_argument("--timeout", type=float, default=8.0)
    _add_client_id_arg(injector_recovery_items)
    injector_recovery_items.set_defaults(func=command_injector_use_recovery_items)

    injector_attack_bot = subparsers.add_parser("injector-attack-bot")
    injector_attack_bot.add_argument("--bot-id", type=int, required=True)
    injector_attack_bot.add_argument("--confirmed", action="store_true")
    injector_attack_bot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_attack_bot)
    injector_attack_bot.set_defaults(func=command_injector_attack_bot)

    injector_attack_visible = subparsers.add_parser("injector-attack-visible")
    injector_attack_visible.add_argument("--margin", type=int, default=35)
    injector_attack_visible.add_argument("--names", nargs="*", default=[])
    injector_attack_visible.add_argument("--target-level", action="append", default=[])
    injector_attack_visible.add_argument("--target-levels", nargs="*", default=[])
    injector_attack_visible.add_argument("--confirmed", action=argparse.BooleanOptionalAction, default=True)
    injector_attack_visible.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_attack_visible)
    injector_attack_visible.set_defaults(func=command_injector_attack_visible)

    injector_battle_snapshot = subparsers.add_parser("injector-battle-snapshot")
    injector_battle_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_battle_snapshot)
    injector_battle_snapshot.set_defaults(func=command_injector_battle_snapshot)

    injector_use_skill = subparsers.add_parser("injector-use-skill")
    injector_use_skill.add_argument("--slot", type=int, default=4)
    injector_use_skill.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_use_skill)
    injector_use_skill.set_defaults(func=command_injector_use_skill)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client_id = getattr(args, "client_id", None)
    if client_id:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        global_browser_injector().set_current_client_id(str(client_id))
    return int(args.func(args))
