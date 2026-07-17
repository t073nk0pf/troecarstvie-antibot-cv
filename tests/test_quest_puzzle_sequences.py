from src.antibot_cv.automation.quest_puzzle_sequences import (
    KATUR_RECIPE_SOURCES,
    quest_solution_search_queries,
    recipe_has_independent_consensus,
    select_verified_puzzle_action,
)


def test_katur_sequence_is_exact_and_ordered() -> None:
    actions = (
        {"ref": "1", "text": "*Крутить барабанчик на второй сверху скобе*"},
        {"ref": "2", "text": "*Крутить барабанчик на третьей сверху скобе*"},
    )
    selected = select_verified_puzzle_action("282", actions, href="", completed_drums=(1, 2))
    assert selected is not None and selected["ref"] == "2"
    value_actions = ({"ref": "9", "text": "*Установить значение барабанчика на 2*"},)
    value = select_verified_puzzle_action("282", value_actions, href="https://x/npc.php?ref=4206", completed_drums=())
    assert value is not None and value["puzzle_completed_drum"] == 3
    assert select_verified_puzzle_action("999", actions, href="", completed_drums=()) is None


def test_katur_sequence_rejects_missing_or_duplicate_control() -> None:
    duplicate = (
        {"ref": "2", "text": "*Крутить барабанчик на третьей сверху скобе*"},
        {"ref": "3", "text": "*Крутить барабанчик на третьей сверху скобе*"},
    )
    assert select_verified_puzzle_action("282", duplicate, href="", completed_drums=(1, 2)) is None


def test_katur_exact_initial_screen_treats_first_drum_as_implicit_three() -> None:
    actions = tuple(
        {"ref": str(drum), "text": f"*Крутить барабанчик на {ordinal} сверху скобе*"}
        for drum, ordinal in ((2, "второй"), (3, "третьей"), (4, "четвертой"), (5, "пятой"))
    ) + ({"ref": "9", "text": "*Попытаться открыть книгу*"},)
    selected = select_verified_puzzle_action("282", actions, href="", completed_drums=())
    assert selected is not None and selected["ref"] == "2"
    assert selected["puzzle_implicit_completed_drums"] == (1,)


def test_puzzle_research_policy_requires_two_independent_hosts() -> None:
    assert recipe_has_independent_consensus(KATUR_RECIPE_SOURCES)
    queries = quest_solution_search_queries("Загадочная книга Катура")
    assert len(queries) == 2
    assert "3kingdoms.ru" in queries[0] and "3k.ucoz.org" in queries[1]
