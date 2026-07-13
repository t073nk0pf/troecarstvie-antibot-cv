from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from src.antibot_cv.viewport.coordinates import Rect


@dataclass(frozen=True)
class ClickOffset:
    dx: float = 0.0
    dy: float = -35.0


@dataclass(frozen=True)
class GreenLabelConfig:
    green_h_min: int = 45
    green_h_max: int = 85
    green_s_min: int = 80
    green_v_min: int = 80
    detect_red_labels: bool = False
    min_label_width: int = 8
    max_label_width: int = 260
    min_label_height: int = 4
    max_label_height: int = 70


@dataclass(frozen=True)
class TargetConfig:
    mode: str = "template_label"
    allowed_targets: tuple[str, ...] = ("steppe_jackal", "young_lynx")
    preferred_target_order: tuple[str, ...] = ("steppe_jackal", "young_lynx")
    allowed_names: tuple[str, ...] = ()
    allowed_levels: tuple[int, ...] = ()
    stable_frames: int = 2
    double_click_to_attack: bool = True
    search_roi: Rect | None = None
    click_offset: ClickOffset = field(default_factory=ClickOffset)
    click_retry_offsets: tuple[ClickOffset, ...] = field(
        default_factory=lambda: (
            ClickOffset(0, 0),
            ClickOffset(0, 35),
            ClickOffset(0, -70),
            ClickOffset(-25, -35),
            ClickOffset(25, -35),
        )
    )
    click_retry_delay_ms: int = 250
    interaction_margin_px: int = 0
    per_target_click_offsets: dict[str, ClickOffset] = field(default_factory=dict)
    green_label: GreenLabelConfig = field(default_factory=GreenLabelConfig)


@dataclass(frozen=True)
class CaptureConfig:
    monitor_index: int = 1
    roi: Rect | None = None
    logical_width: int | None = None
    logical_height: int | None = None


@dataclass(frozen=True)
class ViewportConfig:
    strategy: str = "direction_pad"
    search_sequence: tuple[str, ...] = ("NORTH", "SOUTH", "WEST", "EAST")
    settle_ms: int = 700
    max_moves_per_search: int = 8
    direction_pad_roi: Rect | None = None
    direction_pad_points: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "NORTH": (0.5, 0.2),
            "WEST": (0.3, 0.51),
            "EAST": (0.71, 0.51),
            "SOUTH": (0.5, 0.78),
        }
    )
    scrollbar_enabled: bool = True
    scrollbar_roi: Rect | None = None
    scrollbar_min_confidence: float = 0.65
    scrollbar_step_px: int = 90
    scrollbar_sequence: tuple[str, ...] = ("DOWN", "UP")
    max_scrollbar_moves_per_search: int = 2
    scrollbar_every_direction_moves: int = 0


@dataclass(frozen=True)
class BattleConfig:
    confirm_frames: int = 2
    min_signals: int = 2
    weighted_threshold: float = 1.5


@dataclass(frozen=True)
class Ability4Config:
    use_once: bool = True
    require_ready_confirmation: bool = True
    slot_confidence_threshold: float = 0.5
    normalized_position: tuple[float, float] = (0.45, 0.89)
    pre_click_delay_ms: int = 0
    click_hold_ms: int = 0


@dataclass(frozen=True)
class CombatConfig:
    enabled: bool = True
    slot_index: int = 4
    slot_sequence: tuple[int, ...] = ()
    low_resource_fallback_enabled: bool = True
    low_resource_fallback_slot_index: int = 0
    low_resource_fallback_percent: float = 1.0
    click_interval_ms: int = 2500
    require_ready_confirmation: bool = False
    slot_confidence_threshold: float = 0.4
    pre_click_delay_ms: int = 0
    click_hold_ms: int = 0


@dataclass(frozen=True)
class BattleItemRecoveryConfig:
    enabled: bool = False
    health_use_when_below_percent: float = 35.0
    prowess_use_when_below_percent: float = 15.0
    health_slots: tuple[int, ...] = ()
    prowess_slots: tuple[int, ...] = ()
    health_names: tuple[str, ...] = ()
    prowess_names: tuple[str, ...] = ()
    damage_boost_enabled: bool = False
    damage_boost_slots: tuple[int, ...] = ()
    damage_boost_names: tuple[str, ...] = ()
    cooldown_ms: int = 3000
    max_uses_per_battle: int = 1
    pre_click_delay_ms: int = 0
    click_hold_ms: int = 0


@dataclass(frozen=True)
class LevelingConfig:
    enabled: bool = False
    target_level: int | None = None
    required_character_name: str = ""
    snapshot_interval_ms: int = 1000
    snapshot_stale_timeout_ms: int = 15000
    max_deaths_per_session: int = 3
    target_location_name: str = ""
    auto_target_level_offsets: tuple[int, ...] = (0,)
    free_revive_only: bool = True
    revive_verify_timeout_ms: int = 15000
    post_revive_resource_timeout_ms: int = 120000
    post_revive_max_item_attempts: int = 4
    checkpoint_interval_ms: int = 5000
    quest_refresh_every_cycles: int = 5
    quest_refresh_timeout_ms: int = 10000
    auto_navigate_quest_targets: bool = False
    navigator_timeout_ms: int = 10000
    navigator_max_transitions: int = 50
    route_settle_ms: int = 3000
    allow_soft_currency_purchases: bool = False
    max_soft_currency_spend: float = 0.0


@dataclass(frozen=True)
class DetectionConfig:
    confirm_frames: int = 2
    threshold: float = 0.7


@dataclass(frozen=True)
class ResourceConfig:
    enabled: bool = True
    health_min_percent: float = 35.0
    prowess_min_percent: float = 35.0
    recover_to_percent: float = 80.0
    require_recover_before_search: bool = False
    wait_in_battle_when_low: bool = True
    rest_check_interval_ms: int = 1000
    rest_refresh_after_ms: int = 20000
    rest_refresh_interval_ms: int = 20000
    max_rest_minutes: int = 0
    fail_open_if_missing: bool = False
    roi: Rect | None = None
    health_bar_roi: Rect | None = None
    prowess_bar_roi: Rect | None = None


@dataclass(frozen=True)
class ItemRecoveryConfig:
    enabled: bool = False
    use_after_cycle: bool = True
    use_when_below_percent: float = 90.0
    health_use_when_below_percent: float | None = None
    prowess_use_when_below_percent: float | None = None
    force_use: bool = False
    use_if_resources_missing: bool = False
    open_hunt_after: bool = True
    timeout_s: float = 6.0
    health_restore_percent: float = 40.0
    prowess_restore_percent: float = 30.0
    max_uses_per_resource: int = 4
    inventory_open_delay_ms: int = 1500
    confirm_delay_ms: int = 700
    between_items_delay_ms: int = 500
    health_names: tuple[str, ...] = ("малый бурдюк жизни", "бурдюк жизни")
    prowess_names: tuple[str, ...] = ("малый бурдюк удали", "бурдюк удали")


@dataclass(frozen=True)
class RecoveryConfig:
    enabled: bool = True
    viewport_exhausted_pause_ms: int = 750
    battle_wait_timeout_ms: int = 12000
    battle_active_timeout_ms: int = 240000
    battle_end_timeout_ms: int = 30000
    statistics_timeout_ms: int = 20000


@dataclass(frozen=True)
class SafetyConfig:
    max_actions_per_minute: int = 30
    max_viewport_moves_per_search: int = 8
    max_consecutive_errors: int = 3


@dataclass(frozen=True)
class AutomationConfig:
    dry_run: bool = True
    allow_live_toggle: bool = False
    activate_app: str | None = None
    max_cycles: int = 3
    max_session_minutes: int = 10
    capture_fps: int = 5
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    viewport: ViewportConfig = field(default_factory=ViewportConfig)
    battle: BattleConfig = field(default_factory=BattleConfig)
    ability4: Ability4Config = field(default_factory=Ability4Config)
    combat: CombatConfig = field(default_factory=CombatConfig)
    battle_end: DetectionConfig = field(default_factory=DetectionConfig)
    statistics: DetectionConfig = field(default_factory=DetectionConfig)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    item_recovery: ItemRecoveryConfig = field(default_factory=ItemRecoveryConfig)
    battle_item_recovery: BattleItemRecoveryConfig = field(default_factory=BattleItemRecoveryConfig)
    leveling: LevelingConfig = field(default_factory=LevelingConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    templates_path: str = "config/templates.example.json"
    runs_dir: str = "runs"

    @classmethod
    def from_file(cls, path: str | Path) -> "AutomationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AutomationConfig":
        return _build_dataclass(cls, raw)

    def with_overrides(
        self,
        *,
        dry_run: bool | None = None,
        max_cycles: int | None = None,
        max_session_minutes: int | None = None,
        target_allowed_levels: tuple[int, ...] | None = None,
        target_allowed_names: tuple[str, ...] | None = None,
        goal_level: int | None = None,
    ) -> "AutomationConfig":
        data = to_plain_dict(self)
        if dry_run is not None:
            data["dry_run"] = dry_run
        if max_cycles is not None:
            data["max_cycles"] = max_cycles
        if max_session_minutes is not None:
            data["max_session_minutes"] = max_session_minutes
        if target_allowed_levels is not None:
            data.setdefault("target", {})["allowed_levels"] = list(target_allowed_levels)
        if target_allowed_names is not None:
            data.setdefault("target", {})["allowed_names"] = list(target_allowed_names)
        if goal_level is not None:
            data.setdefault("leveling", {})["enabled"] = True
            data.setdefault("leveling", {})["target_level"] = int(goal_level)
        return AutomationConfig.from_dict(data)


def _build_dataclass(cls: type[Any], raw: Any) -> Any:
    if cls is Rect:
        return Rect(**raw)
    if cls is ClickOffset:
        return ClickOffset(**raw)
    if not is_dataclass(cls):
        return raw

    values: dict[str, Any] = {}
    for item in fields(cls):
        if not isinstance(raw, dict) or item.name not in raw:
            continue
        value = raw[item.name]
        if value is None:
            values[item.name] = None
            continue
        if item.name in {"roi", "direction_pad_roi"}:
            values[item.name] = Rect(**value)
        elif item.name == "capture":
            values[item.name] = _build_dataclass(CaptureConfig, value)
        elif item.name == "target":
            values[item.name] = _build_target_config(value)
        elif item.name == "viewport":
            values[item.name] = _build_viewport_config(value)
        elif item.name == "battle":
            values[item.name] = _build_dataclass(BattleConfig, value)
        elif item.name == "ability4":
            values[item.name] = _build_ability_config(value)
        elif item.name == "combat":
            values[item.name] = _build_combat_config(value)
        elif item.name == "battle_end":
            values[item.name] = _build_dataclass(DetectionConfig, value)
        elif item.name == "statistics":
            values[item.name] = _build_dataclass(DetectionConfig, value)
        elif item.name == "resources":
            values[item.name] = _build_resource_config(value)
        elif item.name == "item_recovery":
            values[item.name] = _build_item_recovery_config(value)
        elif item.name == "battle_item_recovery":
            values[item.name] = _build_battle_item_recovery_config(value)
        elif item.name == "leveling":
            values[item.name] = _build_leveling_config(value)
        elif item.name == "recovery":
            values[item.name] = _build_dataclass(RecoveryConfig, value)
        elif item.name == "safety":
            values[item.name] = _build_dataclass(SafetyConfig, value)
        else:
            values[item.name] = value
    return cls(**values)


def _build_target_config(raw: dict[str, Any]) -> TargetConfig:
    data = dict(raw)
    if "allowed_targets" in data:
        data["allowed_targets"] = tuple(data["allowed_targets"])
    if "preferred_target_order" in data:
        data["preferred_target_order"] = tuple(data["preferred_target_order"])
    if "allowed_names" in data:
        data["allowed_names"] = tuple(str(value).strip() for value in data["allowed_names"] if str(value).strip())
    if "allowed_levels" in data:
        data["allowed_levels"] = tuple(int(value) for value in data["allowed_levels"])
    if data.get("search_roi") is not None:
        data["search_roi"] = Rect(**data["search_roi"])
    if "click_offset" in data:
        data["click_offset"] = ClickOffset(**data["click_offset"])
    if "click_retry_offsets" in data:
        data["click_retry_offsets"] = tuple(ClickOffset(**value) for value in data["click_retry_offsets"])
    if "per_target_click_offsets" in data:
        data["per_target_click_offsets"] = {
            key: ClickOffset(**value) for key, value in data["per_target_click_offsets"].items()
        }
    if "green_label" in data:
        data["green_label"] = GreenLabelConfig(**data["green_label"])
    return TargetConfig(**data)


def _build_leveling_config(raw: dict[str, Any]) -> LevelingConfig:
    data = dict(raw)
    if "auto_target_level_offsets" in data:
        data["auto_target_level_offsets"] = tuple(int(value) for value in data["auto_target_level_offsets"])
    return LevelingConfig(**data)


def _build_viewport_config(raw: dict[str, Any]) -> ViewportConfig:
    data = dict(raw)
    if "search_sequence" in data:
        data["search_sequence"] = tuple(data["search_sequence"])
    if data.get("direction_pad_roi") is not None:
        data["direction_pad_roi"] = Rect(**data["direction_pad_roi"])
    if data.get("scrollbar_roi") is not None:
        data["scrollbar_roi"] = Rect(**data["scrollbar_roi"])
    if "direction_pad_points" in data:
        data["direction_pad_points"] = {
            key: tuple(value) for key, value in data["direction_pad_points"].items()
        }
    if "scrollbar_sequence" in data:
        data["scrollbar_sequence"] = tuple(data["scrollbar_sequence"])
    return ViewportConfig(**data)


def _build_ability_config(raw: dict[str, Any]) -> Ability4Config:
    data = dict(raw)
    if "normalized_position" in data:
        data["normalized_position"] = tuple(data["normalized_position"])
    return Ability4Config(**data)


def _build_combat_config(raw: dict[str, Any]) -> CombatConfig:
    data = dict(raw)
    for key in ("slot_index", "click_interval_ms", "pre_click_delay_ms", "click_hold_ms"):
        if key in data:
            data[key] = int(data[key])
    if "low_resource_fallback_slot_index" in data:
        data["low_resource_fallback_slot_index"] = int(data["low_resource_fallback_slot_index"])
    if "low_resource_fallback_percent" in data:
        data["low_resource_fallback_percent"] = float(data["low_resource_fallback_percent"])
    if "slot_sequence" in data:
        data["slot_sequence"] = tuple(int(value) for value in data["slot_sequence"])
    return CombatConfig(**data)


def _build_resource_config(raw: dict[str, Any]) -> ResourceConfig:
    data = dict(raw)
    for key in ("roi", "health_bar_roi", "prowess_bar_roi"):
        if data.get(key) is not None:
            data[key] = Rect(**data[key])
    return ResourceConfig(**data)


def _build_item_recovery_config(raw: dict[str, Any]) -> ItemRecoveryConfig:
    data = dict(raw)
    for key in ("health_names", "prowess_names"):
        if key in data:
            data[key] = tuple(str(value) for value in data[key])
    if "max_uses_per_resource" in data:
        data["max_uses_per_resource"] = int(data["max_uses_per_resource"])
    for key in ("health_use_when_below_percent", "prowess_use_when_below_percent"):
        if key in data and data[key] is not None:
            data[key] = float(data[key])
    if "inventory_open_delay_ms" in data:
        data["inventory_open_delay_ms"] = int(data["inventory_open_delay_ms"])
    if "confirm_delay_ms" in data:
        data["confirm_delay_ms"] = int(data["confirm_delay_ms"])
    if "between_items_delay_ms" in data:
        data["between_items_delay_ms"] = int(data["between_items_delay_ms"])
    return ItemRecoveryConfig(**data)


def _build_battle_item_recovery_config(raw: dict[str, Any]) -> BattleItemRecoveryConfig:
    data = dict(raw)
    for key in ("health_slots", "prowess_slots", "damage_boost_slots"):
        if key in data:
            data[key] = tuple(int(value) for value in data[key])
    for key in ("health_names", "prowess_names", "damage_boost_names"):
        if key in data:
            data[key] = tuple(str(value) for value in data[key])
    for key in ("cooldown_ms", "max_uses_per_battle", "pre_click_delay_ms", "click_hold_ms"):
        if key in data:
            data[key] = int(data[key])
    for key in ("health_use_when_below_percent", "prowess_use_when_below_percent"):
        if key in data:
            data[key] = float(data[key])
    return BattleItemRecoveryConfig(**data)


def to_plain_dict(value: Any) -> Any:
    if isinstance(value, Rect):
        return {"x": value.x, "y": value.y, "width": value.width, "height": value.height}
    if isinstance(value, tuple):
        return [to_plain_dict(item) for item in value]
    if isinstance(value, list):
        return [to_plain_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain_dict(item) for key, item in value.items()}
    if is_dataclass(value):
        return {item.name: to_plain_dict(getattr(value, item.name)) for item in fields(value)}
    return value
