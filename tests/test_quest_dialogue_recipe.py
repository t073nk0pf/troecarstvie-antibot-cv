from src.antibot_cv.automation.quest_dialogue_recipe import (
    DialogueRecipeCursor,
    dialogue_recipe_timeout_s,
)


def _actions(*texts: str):
    return [{"ref": str(index), "text": text} for index, text in enumerate(texts, 1)]


def test_vasilisa_recipe_resets_then_executes_reviewed_combination() -> None:
    cursor = DialogueRecipeCursor()
    menu = _actions(
        "*Один раз повернуть Гранатовый ключ.*",
        "*Один раз повернуть Изумрудный ключ.*",
        "*Один раз повернуть Янтарный ключ.*",
        "*Попробовать завести шкатулку.*",
        "*Вернуть все ключи в исходное положение.*",
    )
    submenu = _actions("*Продолжить поворачивать ключи.*", "*Попробовать завести шкатулку.*")

    selected = []
    for _ in range(40):
        actions = menu if not selected or "ключи" in selected[-1] or "поворачивать" in selected[-1] else submenu
        action = cursor.select(quest_id="267", title="Музыкальная шкатулка Василисы", actions=actions)
        assert action is not None
        selected.append(str(action["text"]).casefold())
        cursor.acknowledge()
        if "завести" in selected[-1]:
            break

    assert sum("гранатовый" in text for text in selected) == 4
    assert sum("янтарный" in text for text in selected) == 5
    assert sum("изумрудный" in text for text in selected) == 3
    assert "вернуть все ключи" in selected[0]
    assert "завести шкатулку" in selected[-1]


def test_recipe_repeats_same_exact_choice_for_two_snapshot_stability_gate() -> None:
    cursor = DialogueRecipeCursor()
    menu = _actions(
        "*Один раз повернуть Гранатовый ключ.*",
        "*Вернуть все ключи в исходное положение.*",
    )

    first = cursor.select(
        quest_id="267", title="Музыкальная шкатулка Василисы", actions=menu,
    )
    second = cursor.select(
        quest_id="267", title="Музыкальная шкатулка Василисы", actions=menu,
    )

    assert first == second
    assert "вернуть все ключи" in str(first["text"]).casefold()


def test_only_reviewed_long_recipe_gets_extended_bounded_deadline() -> None:
    assert dialogue_recipe_timeout_s("267", "Музыкальная шкатулка Василисы") == 120.0
    assert dialogue_recipe_timeout_s("267", "Другой квест") == 20.0
    assert dialogue_recipe_timeout_s("999", "Музыкальная шкатулка Василисы") == 20.0
