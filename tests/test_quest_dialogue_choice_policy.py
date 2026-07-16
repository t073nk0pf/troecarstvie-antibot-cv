from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_dialogue_choice_policy import (
    select_progress_dialogue_action,
)


@pytest.mark.parametrize(
    "malformed",
    (
        {"ref": "", "text": "Не могу продолжить."},
        {"ref": "unknown", "text": "Не могу продолжить."},
        {"ref": 402, "text": "Не могу продолжить."},
        {"ref": "4" * 81, "text": "Не могу продолжить."},
        {"ref": "402", "text": ""},
        {"ref": "402", "text": None},
        {"ref": "402", "text": "x" * 1201},
    ),
)
def test_visible_malformed_choice_makes_selection_fail_closed(
    malformed: dict[str, object],
) -> None:
    progress = {
        "ref": "401",
        "text": "Я продолжу задание.",
        "visible": True,
    }

    selected = select_progress_dialogue_action(
        (progress, {**malformed, "visible": True})
    )

    assert selected is None
