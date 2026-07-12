from __future__ import annotations

from src.antibot_cv.automation.controller import build_parser


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


def test_run_accepts_browser_client_id() -> None:
    args = build_parser().parse_args(["run", "--client-id", "chrome-test"])
    assert args.client_id == "chrome-test"


def test_control_server_cli() -> None:
    args = build_parser().parse_args(["control-server", "--config", "config/automation.local.json", "--live"])
    assert args.command == "control-server"
    assert args.live is True
    assert args.config == "config/automation.local.json"


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
