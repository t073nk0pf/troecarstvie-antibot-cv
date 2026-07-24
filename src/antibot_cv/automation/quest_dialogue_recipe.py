"""Declarative recipes for bounded quest dialogue puzzles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DialogueRecipe:
    quest_id: str
    title: str
    operations: tuple[tuple[str, int], ...]
    reset_text: str
    continue_text: str
    submit_text: str


_RECIPES = {
    "267": DialogueRecipe(
        quest_id="267",
        title="музыкальная шкатулка василисы",
        operations=(("гранатовый ключ", 4), ("янтарный ключ", 5), ("изумрудный ключ", 3)),
        reset_text="вернуть все ключи в исходное положение",
        continue_text="продолжить поворачивать ключи",
        submit_text="попробовать завести шкатулку",
    ),
}


def dialogue_recipe_timeout_s(quest_id: str, title: str) -> float:
    """Return the bounded dialogue lease needed by a reviewed recipe."""

    recipe = _RECIPES.get(str(quest_id))
    return 120.0 if recipe is not None and _normalized(title) == recipe.title else 20.0


class DialogueRecipeCursor:
    """Select and advance one exact action from a reviewed recipe."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._quest_id = ""
        self._reset_done = False
        self._operation_index = 0
        self._operation_count = 0
        self._pending_role: str | None = None
        self._pending_ref = ""
        self._pending_text = ""

    @property
    def pending_role(self) -> str | None:
        return self._pending_role

    def select(
        self, *, quest_id: str, title: str,
        actions: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object] | None:
        recipe = _RECIPES.get(quest_id)
        if recipe is None or _normalized(title) != recipe.title:
            return None
        if self._quest_id != quest_id:
            self.reset()
            self._quest_id = quest_id
        if self._pending_role is not None:
            matches = [
                action for action in actions
                if str(action.get("ref") or "").strip() == self._pending_ref
                and str(action.get("text") or "").strip() == self._pending_text
            ]
            return matches[0] if len(matches) == 1 else None
        if not self._reset_done:
            return self._match(actions, recipe.reset_text, "reset")
        if self._operation_index < len(recipe.operations):
            operation, _ = recipe.operations[self._operation_index]
            selected = self._match(actions, operation, "operation")
            if selected is not None:
                return selected
            return self._match(actions, recipe.continue_text, "continue")
        return self._match(actions, recipe.submit_text, "submit")

    def acknowledge(self) -> None:
        role = self._pending_role
        if role is None:
            return
        self._pending_role = None
        self._pending_ref = ""
        self._pending_text = ""
        if role == "reset":
            self._reset_done = True
        elif role == "operation":
            recipe = _RECIPES[self._quest_id]
            self._operation_count += 1
            if self._operation_count >= recipe.operations[self._operation_index][1]:
                self._operation_index += 1
                self._operation_count = 0

    def _match(
        self, actions: Sequence[Mapping[str, object]], needle: str, role: str,
    ) -> Mapping[str, object] | None:
        matches = [action for action in actions if needle in _normalized(action.get("text"))]
        if len(matches) != 1:
            return None
        self._pending_role = role
        self._pending_ref = str(matches[0].get("ref") or "").strip()
        self._pending_text = str(matches[0].get("text") or "").strip()
        return matches[0]


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").strip("* .").split())
