from __future__ import annotations

import pytest

from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import _apply_runtime_overrides


@pytest.mark.parametrize("mode", ["legacy", "shadow", "q280_q304"])
def test_leveling_quest_engine_mode_accepts_supported_values(mode: str) -> None:
    config = AutomationConfig.from_dict({"leveling": {"quest_engine_mode": mode}})

    assert config.leveling.quest_engine_mode == mode


def test_leveling_quest_engine_mode_defaults_to_legacy() -> None:
    config = AutomationConfig.from_dict({})

    assert config.leveling.quest_engine_mode == "legacy"


@pytest.mark.parametrize("mode", ["", "unknown", "Q280_Q304"])
def test_leveling_quest_engine_mode_rejects_unsupported_values(mode: str) -> None:
    with pytest.raises(ValueError, match="leveling.quest_engine_mode"):
        AutomationConfig.from_dict({"leveling": {"quest_engine_mode": mode}})


def test_runtime_override_applies_validated_quest_engine_mode() -> None:
    config = AutomationConfig.from_dict({})

    overridden = _apply_runtime_overrides(config, {"questEngineMode": "shadow"})

    assert overridden.leveling.quest_engine_mode == "shadow"


def test_runtime_override_rejects_invalid_quest_engine_mode() -> None:
    config = AutomationConfig.from_dict({})

    with pytest.raises(ValueError, match="leveling.quest_engine_mode"):
        _apply_runtime_overrides(config, {"questEngineMode": "invalid"})


def test_quest_engine_mode_survives_plain_dict_round_trip() -> None:
    config = AutomationConfig.from_dict({"leveling": {"quest_engine_mode": "q280_q304"}})

    restored = AutomationConfig.from_dict(to_plain_dict(config))

    assert restored.leveling.quest_engine_mode == "q280_q304"
