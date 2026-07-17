from __future__ import annotations

import json

import pytest

from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.controller_cli import build_parser, main
from src.antibot_cv.telemetry.m1_recovery import M1_RECOVERY_PHASES


def test_run_accepts_no_hotkeys_flag() -> None:
    args = build_parser().parse_args(["run", "--no-hotkeys"])
    assert args.hotkeys is False


def test_run_disables_hotkeys_by_default() -> None:
    args = build_parser().parse_args(["run"])
    assert args.hotkeys is False


def test_run_accepts_target_level_filters() -> None:
    args = build_parser().parse_args(["run", "--target-level", "3", "--target-levels", "4,5"])
    assert args.target_level == ["3"]
    assert args.target_levels == ["4,5"]


def test_run_accepts_goal_level() -> None:
    args = build_parser().parse_args(["run", "--goal-level", "6"])
    assert args.goal_level == 6


def test_run_accepts_open_hunt_on_start_flag() -> None:
    args = build_parser().parse_args(["run", "--open-hunt-on-start"])
    assert args.open_hunt_on_start is True


def test_run_accepts_combat_only_flag() -> None:
    args = build_parser().parse_args(["run", "--combat-only"])

    assert args.combat_only is True


def test_run_accepts_browser_client_id() -> None:
    args = build_parser().parse_args(["run", "--client-id", "chrome-test"])
    assert args.client_id == "chrome-test"


@pytest.mark.parametrize(
    "argv",
    [
        ["--config", "config/automation.local.json", "run"],
        ["run", "--config", "config/automation.local.json"],
    ],
)
def test_run_preserves_config_before_or_after_subcommand(argv) -> None:
    args = build_parser().parse_args(argv)

    assert args.config == "config/automation.local.json"


@pytest.mark.parametrize(
    "argv",
    [
        ["--config", "config/automation.local.json", "run"],
        ["run", "--config", "config/automation.local.json"],
    ],
)
def test_run_handler_receives_config_from_either_position(argv, monkeypatch) -> None:
    import src.antibot_cv.automation.controller_cli as controller_cli

    captured = []
    monkeypatch.setattr(
        controller_cli,
        "run_automation",
        lambda options: captured.append(options),
    )

    assert main(argv) == 0
    assert len(captured) == 1
    assert captured[0].config_path == "config/automation.local.json"


def test_run_rejects_conflicting_global_and_subcommand_configs() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([
            "--config",
            "config/first.json",
            "run",
            "--config",
            "config/second.json",
        ])

    assert exc_info.value.code == 2


def test_control_server_cli() -> None:
    args = build_parser().parse_args(["control-server", "--config", "config/automation.local.json", "--live"])
    assert args.command == "control-server"
    assert args.live is True
    assert args.config == "config/automation.local.json"


def test_assess_m1_recovery_cli_reports_offline_readiness(tmp_path, capsys) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "".join(
            json.dumps({"recovery_id": "recovery-1", "event_type": phase}) + "\n"
            for phase in M1_RECOVERY_PHASES
        ),
        encoding="utf-8",
    )

    exit_code = main(["assess-m1-recovery", "--events", str(events_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["offline_ready"] is True
    assert payload["complete_attempts"] == 1


def test_assess_m1_recovery_cli_fails_when_latest_attempt_is_incomplete(tmp_path, capsys) -> None:
    events_path = tmp_path / "events.jsonl"
    phases = [("complete", phase) for phase in M1_RECOVERY_PHASES]
    phases += [("incomplete", phase) for phase in M1_RECOVERY_PHASES[:-1]]
    events_path.write_text(
        "".join(
            json.dumps({"recovery_id": recovery_id, "event_type": phase}) + "\n"
            for recovery_id, phase in phases
        ),
        encoding="utf-8",
    )

    exit_code = main(["assess-m1-recovery", "--events", str(events_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["offline_ready"] is False
    assert payload["latest_attempt_passed"] is False
    assert payload["consecutive_complete_attempts"] == 0
    assert payload["longest_complete_streak"] == 1


def test_injector_hunt_snapshot_cli() -> None:
    args = build_parser().parse_args(["injector-hunt-snapshot", "--timeout", "1", "--client-id", "chrome-test"])
    assert args.command == "injector-hunt-snapshot"
    assert args.timeout == 1
    assert args.client_id == "chrome-test"


def test_injector_attack_bot_cli() -> None:
    args = build_parser().parse_args(["injector-attack-bot", "--bot-id", "1675", "--confirmed", "--timeout", "1"])
    assert args.command == "injector-attack-bot"
    assert args.bot_id == 1675
    assert args.confirmed is True
    assert args.timeout == 1
    assert args.live is False


def test_injector_hunt_candidates_cli() -> None:
    args = build_parser().parse_args(["injector-hunt-candidates", "--timeout", "1"])
    assert args.command == "injector-hunt-candidates"
    assert args.timeout == 1


def test_injector_layout_cli() -> None:
    args = build_parser().parse_args(["injector-layout", "--mode", "wide", "--chat-height", "120", "--stretch", "fit", "--timeout", "1"])
    assert args.command == "injector-layout"
    assert args.mode == "wide"
    assert args.chat_height == 120
    assert args.stretch == "fit"
    assert args.timeout == 1


def test_injector_layout_snapshot_cli() -> None:
    args = build_parser().parse_args(["injector-layout-snapshot", "--timeout", "1"])
    assert args.command == "injector-layout-snapshot"
    assert args.timeout == 1


def test_injector_state_snapshot_cli() -> None:
    args = build_parser().parse_args(
        ["injector-state-snapshot", "--include", "player,location", "quests", "--timeout", "1", "--client-id", "chrome-test"]
    )
    assert args.command == "injector-state-snapshot"
    assert args.include == ["player,location", "quests"]
    assert args.timeout == 1
    assert args.client_id == "chrome-test"


def test_injector_bot_info_cli() -> None:
    args = build_parser().parse_args(["injector-bot-info", "--bot-id", "1675", "--timeout", "1"])
    assert args.command == "injector-bot-info"
    assert args.bot_id == 1675
    assert args.timeout == 1


def test_injector_visible_targets_cli() -> None:
    args = build_parser().parse_args(
        ["injector-visible-targets", "--margin", "50", "--names", "Пепельный", "--target-level", "3", "--timeout", "1"]
    )
    assert args.command == "injector-visible-targets"
    assert args.margin == 50
    assert args.names == ["Пепельный"]
    assert args.target_level == ["3"]
    assert args.timeout == 1


def test_injector_attack_visible_cli() -> None:
    args = build_parser().parse_args(
        ["injector-attack-visible", "--margin", "50", "--names", "Пепельный", "--target-levels", "3,4", "--timeout", "1"]
    )
    assert args.command == "injector-attack-visible"
    assert args.margin == 50
    assert args.names == ["Пепельный"]
    assert args.target_levels == ["3,4"]
    assert args.confirmed is True
    assert args.timeout == 1
    assert args.live is False


def test_injector_battle_snapshot_cli() -> None:
    args = build_parser().parse_args(["injector-battle-snapshot", "--timeout", "1"])
    assert args.command == "injector-battle-snapshot"
    assert args.timeout == 1


def test_injector_inventory_snapshot_cli() -> None:
    args = build_parser().parse_args(["injector-inventory-snapshot", "--names", "малый бурдюк жизни", "--no-open", "--timeout", "1"])
    assert args.command == "injector-inventory-snapshot"
    assert args.names == ["малый бурдюк жизни"]
    assert args.open is False
    assert args.timeout == 1


def test_injector_use_skill_cli() -> None:
    args = build_parser().parse_args(["injector-use-skill", "--slot", "4", "--timeout", "1"])
    assert args.command == "injector-use-skill"
    assert args.slot == 4
    assert args.timeout == 1
    assert args.live is False


def test_injector_use_recovery_items_cli() -> None:
    args = build_parser().parse_args(
        [
            "injector-use-recovery-items",
            "--health-names",
            "бурдюк здоровья",
            "--prowess-names",
            "бурдюк удали",
            "--threshold",
            "85",
            "--health-restore-percent",
            "40",
            "--prowess-restore-percent",
            "30",
            "--max-uses-per-resource",
            "4",
            "--inventory-open-delay-ms",
            "1600",
            "--confirm-delay-ms",
            "900",
            "--between-items-delay-ms",
            "250",
            "--no-open-hunt-after",
            "--timeout",
            "1",
        ]
    )
    assert args.command == "injector-use-recovery-items"
    assert args.health_names == ["бурдюк здоровья"]
    assert args.prowess_names == ["бурдюк удали"]
    assert args.threshold == 85
    assert args.health_restore_percent == 40
    assert args.prowess_restore_percent == 30
    assert args.max_uses_per_resource == 4
    assert args.inventory_open_delay_ms == 1600
    assert args.confirm_delay_ms == 900
    assert args.between_items_delay_ms == 250
    assert args.open_hunt_after is False
    assert args.timeout == 1
    assert args.live is False


class FakeCliInjector:
    def __init__(self) -> None:
        self.port = 8765
        self.last_client_id = "fake-client"
        self.start_calls = 0
        self.set_client_calls: list[str] = []
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.timeouts: list[tuple[str, float | None]] = []

    def start(self) -> None:
        self.start_calls += 1

    def set_current_client_id(self, client_id: str) -> None:
        self.set_client_calls.append(client_id)
        self.last_client_id = client_id

    def execute(self, command, payload=None, **kwargs):
        self.commands.append((command, dict(payload or {})))
        self.timeouts.append((command, kwargs.get("timeout_s")))
        if command == "open_recovery_item":
            message = json.dumps({"message": "recovery_item_not_needed", "percent": 100})
        else:
            message = json.dumps({"message": f"{command}_ok"})
        return InjectorResult(True, message, client_id=self.last_client_id)


MUTATING_CLI_CASES = (
    (["injector-attack-bot", "--bot-id", "1675", "--confirmed", "--timeout", "1.25"], "attack_visible_bot"),
    (["injector-attack-visible", "--names", "Пепельный", "--timeout", "1.25"], "attack_visible_bot"),
    (["injector-use-skill", "--slot", "4", "--timeout", "1.25"], "use_skill_slot"),
    (
        [
            "injector-use-recovery-items",
            "--health-names",
            "бурдюк здоровья",
            "--prowess-names",
            "бурдюк удали",
            "--no-open-hunt-after",
            "--inventory-open-delay-ms",
            "0",
            "--confirm-delay-ms",
            "0",
            "--between-items-delay-ms",
            "0",
        ],
        "open_recovery_item",
    ),
)


@pytest.mark.parametrize(("argv", "expected_command"), MUTATING_CLI_CASES)
def test_mutating_injector_cli_requires_explicit_live_without_bridge_calls(
    monkeypatch,
    capsys,
    argv,
    expected_command,
) -> None:
    import src.antibot_cv.automation.actions as actions_module
    import src.antibot_cv.automation.browser_injector as injector_module

    fake = FakeCliInjector()
    monkeypatch.setattr(injector_module, "global_browser_injector", lambda: fake)
    monkeypatch.setattr(actions_module, "global_browser_injector", lambda: fake)

    assert main([*argv, "--client-id", "blocked-client"]) == 2

    payload = json.loads(capsys.readouterr().err)
    assert payload["reason"] == "explicit_live_flag_required"
    assert fake.start_calls == 0
    assert fake.set_client_calls == []
    assert fake.commands == []


@pytest.mark.parametrize(("argv", "expected_command"), MUTATING_CLI_CASES)
def test_mutating_injector_cli_live_uses_shared_action_path(
    monkeypatch,
    capsys,
    argv,
    expected_command,
) -> None:
    import src.antibot_cv.automation.actions as actions_module
    import src.antibot_cv.automation.browser_injector as injector_module

    fake = FakeCliInjector()
    monkeypatch.setattr(injector_module, "global_browser_injector", lambda: fake)
    monkeypatch.setattr(actions_module, "global_browser_injector", lambda: fake)

    result = main([*argv, "--live"])

    payload = json.loads(capsys.readouterr().out)
    assert fake.start_calls == 1
    if expected_command == "use_skill_slot":
        assert result == 1
        assert payload["ok"] is False
        assert expected_command not in [command for command, _ in fake.commands]
        return
    assert result == 0
    assert payload["ok"] is True
    assert expected_command in [command for command, _ in fake.commands]
    assert any(event["event_type"] != "intended_action" for event in payload["events"])
    if expected_command in {"attack_visible_bot", "use_skill_slot"}:
        assert (expected_command, 1.25) in fake.timeouts


@pytest.mark.parametrize(
    "argv",
    (
        ["--config", "chosen.json", "injector-use-skill", "--slot", "4", "--live"],
        ["injector-use-skill", "--config", "chosen.json", "--slot", "4", "--live"],
    ),
)
def test_live_action_config_is_preserved_before_or_after_subcommand(
    monkeypatch,
    capsys,
    argv,
) -> None:
    from src.antibot_cv.automation.config import AutomationConfig
    import src.antibot_cv.automation.actions as actions_module
    import src.antibot_cv.automation.browser_injector as injector_module
    import src.antibot_cv.automation.controller_cli as cli_module

    fake = FakeCliInjector()
    loaded: list[str | None] = []
    monkeypatch.setattr(injector_module, "global_browser_injector", lambda: fake)
    monkeypatch.setattr(actions_module, "global_browser_injector", lambda: fake)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda path: loaded.append(path) or AutomationConfig(),
    )

    assert main(argv) == 1

    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert loaded == ["chosen.json"]


def test_recover_catalog_navigation_parser_requires_explicit_apply() -> None:
    planned = build_parser().parse_args([
        "recover-catalog-navigation", "--client-id", "client-a",
    ])
    applied = build_parser().parse_args([
        "recover-catalog-navigation", "--client-id", "client-a", "--apply",
    ])

    assert planned.apply is False
    assert applied.apply is True


def test_recover_legacy_npc_open_parser_requires_events_and_explicit_apply() -> None:
    planned = build_parser().parse_args([
        "recover-legacy-npc-open", "--client-id", "client-a", "--events", "events.jsonl",
    ])
    applied = build_parser().parse_args([
        "recover-legacy-npc-open", "--client-id", "client-a", "--events", "events.jsonl", "--apply",
    ])
    assert planned.apply is False and planned.events == "events.jsonl"
    assert applied.apply is True
