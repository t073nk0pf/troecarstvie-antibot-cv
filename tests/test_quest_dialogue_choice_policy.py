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


def test_live_perun_evidence_selects_unique_commitment_reply() -> None:
    refusal = {
        "ref": "3807",
        "text": (
            "Я не жрец, не ведаю, как говорить с богами. Почему хочешь ты, "
            "чтобы я совершил ритуал?"
        ),
    }
    commitment = {
        "ref": "3808",
        "text": (
            "Сказывай, почтенный, как провести сей ритуал. Сделаю я всё, что "
            "в моих силах, дабы умилостивить великого Перуна."
        ),
    }

    assert select_progress_dialogue_action((refusal, commitment)) is commitment


def test_generic_commitment_beats_refusal() -> None:
    commitment = {"ref": "10", "text": "Я готов помочь и выполню это поручение."}
    refusal = {"ref": "11", "text": "Нет, я не буду и не хочу помогать."}

    assert select_progress_dialogue_action((refusal, commitment)) is commitment


def test_sole_neutral_reply_remains_safe_without_branch_choice() -> None:
    neutral = {"ref": "12", "text": "Далее"}

    assert select_progress_dialogue_action((neutral,)) is neutral


def test_live_pridon_sole_greeting_continuation_is_safe() -> None:
    greeting = {
        "ref": "987",
        "text": (
            "Хочу я поприветствовать тебя, великого героя. И спросить, быть "
            "может, сгодится на что моя твердая рука? Рад я служить во благо "
            "Артании!"
        ),
    }

    assert select_progress_dialogue_action((greeting,)) is greeting


def test_live_pridon_greeting_does_not_win_when_a_branch_exists() -> None:
    greeting = {
        "ref": "987",
        "text": (
            "Хочу я поприветствовать тебя, великого героя. И спросить, быть "
            "может, сгодится на что моя твердая рука? Рад я служить во благо "
            "Артании!"
        ),
    }
    alternative = {"ref": "988", "text": "Расскажи подробнее."}

    assert select_progress_dialogue_action((greeting, alternative)) is None


def test_live_pridon_sole_country_report_is_not_mistaken_for_refusal() -> None:
    report = {
        "ref": "989",
        "text": (
            "По мне, хорошо всё в Артании. Стада плодятся, урожаи растут, "
            "людей всё больше, войн давно разорительных нет."
        ),
    }

    assert select_progress_dialogue_action((report,)) is report


def test_sole_atonement_question_is_safe_even_with_past_denial_words() -> None:
    apology = {
        "ref": "994",
        "text": (
            "Прошу, не карай меня так, доблестный воитель! Помутилось сознание моё, "
            "когда попытался я забрать сей нож. Никогда прежде не делал я такого "
            "и в будущем не поступлю подобным образом! Что могу сделать я, дабы "
            "искупить вину?"
        ),
    }

    assert select_progress_dialogue_action((apology,)) is apology


def test_exploration_clicks_single_neutral_question_and_skips_attempted_ref() -> None:
    from src.antibot_cv.automation.quest_dialogue_choice_policy import (
        select_exploratory_dialogue_action,
    )

    question = {"ref": "990", "text": "А что же поделать мы можем?"}

    assert select_exploratory_dialogue_action((question,)) is question
    assert select_exploratory_dialogue_action(
        (question,), attempted_refs=("990",)
    ) is None


def test_exploration_tries_safe_unknown_options_in_source_order() -> None:
    from src.antibot_cv.automation.quest_dialogue_choice_policy import (
        select_exploratory_dialogue_action,
    )

    first = {"ref": "991", "text": "Расскажи о стране."}
    second = {"ref": "992", "text": "Что случилось дальше?"}
    refusal = {"ref": "993", "text": "Нет, прощай."}

    assert select_exploratory_dialogue_action((first, second, refusal)) is first
    assert select_exploratory_dialogue_action(
        (first, second, refusal), attempted_refs=("991",)
    ) is second
    assert select_exploratory_dialogue_action(
        (first, second, refusal), attempted_refs=("991", "992")
    ) is None


def test_exploration_tries_story_branch_choices_in_source_order() -> None:
    from src.antibot_cv.automation.quest_dialogue_choice_policy import (
        select_exploratory_dialogue_action,
    )

    steal = {"ref": "6101", "text": "*Попытаться незаметно забрать нож со стола*"}
    buy = {"ref": "6102", "text": "*Попросить продать нож*"}

    assert select_exploratory_dialogue_action((steal, buy)) is steal
    assert select_exploratory_dialogue_action(
        (steal, buy), attempted_refs=("6101",)
    ) is buy


def test_exploration_advances_uncertain_story_question_but_not_refusal() -> None:
    from src.antibot_cv.automation.quest_dialogue_choice_policy import (
        select_exploratory_dialogue_action,
    )

    question = {"ref": "6201", "text": "Может быть, ты расскажешь, что случилось?"}
    refusal = {"ref": "6202", "text": "Нет, прощай."}

    assert select_exploratory_dialogue_action((question, refusal)) is question
    assert select_exploratory_dialogue_action(
        (question, refusal), attempted_refs=("6201",)
    ) is None


@pytest.mark.parametrize(
    "text",
    (
        "Я не согласен.",
        "Я не сделаю этого.",
        "Я не выполню поручение.",
        "Я не помогу тебе.",
        "Я не возьмусь за это.",
        "Я не готов помогать.",
        "Я не подготовлен.",
        "Нет, проваливай.",
    ),
)
def test_sole_explicit_denial_or_hostility_is_rejected(text: str) -> None:
    assert select_progress_dialogue_action(({"ref": "13", "text": text},)) is None


def test_sole_uncommitted_question_is_not_mistaken_for_future_commitment() -> None:
    assert (
        select_progress_dialogue_action(({"ref": "14", "text": "Что ты сделал?"},))
        is None
    )


@pytest.mark.parametrize(
    "text",
    (
        "Я не сделаю этого.",
        "Я не выполню поручение.",
        "Я не помогу тебе.",
        "Мы не поможем тебе.",
        "Я не добуду реликвию.",
        "Мы не добудем реликвию.",
        "Я не принесу письмо.",
        "Мы не принесем письмо.",
        "Я не найду дорогу.",
        "Мы не найдем дорогу.",
        "Я не проведу ритуал.",
        "Мы не проведем ритуал.",
        "Я не продолжу путь.",
        "Мы не продолжим путь.",
        "Я не отправлюсь туда.",
        "Мы не отправимся туда.",
        "Я не возьмусь за дело.",
        "Мы не возьмемся за дело.",
        "Я не согласен.",
        "Я не берусь за дело.",
        "Я не принимаю поручение.",
        "Я не готов помочь.",
        "Не где мне искать реликвию.",
        "Не куда мне идти.",
        "Не сказывай мне путь.",
        "Это не в моих силах.",
        "Я не думаю, что сделаю это.",
    ),
)
def test_bounded_negation_window_vetoes_every_positive_pattern_family(text: str) -> None:
    assert select_progress_dialogue_action(({"ref": "15", "text": text},)) is None


def test_explicit_refusal_family_vetoes_positive_commitment() -> None:
    action = {"ref": "16", "text": "Я согласен отказаться от поручения."}

    assert select_progress_dialogue_action((action,)) is None


@pytest.mark.parametrize(
    "text",
    (
        "Я не думаю, что когда-нибудь сделаю это.",
        "Я сомневаюсь, что помогу тебе.",
        "Вряд ли я помогу тебе.",
        "Может быть, я помогу тебе.",
        "Я готов помочь?",
        "Неужели я сделаю это?",
    ),
)
def test_uncertainty_and_question_shaped_commitments_are_hard_vetoed(text: str) -> None:
    assert select_progress_dialogue_action(({"ref": "17", "text": text},)) is None


@pytest.mark.parametrize(
    "text",
    (
        "Где мне искать моряка?",
        "Куда мне идти?",
    ),
)
def test_exact_safe_action_request_questions_remain_allowed(text: str) -> None:
    action = {"ref": "18", "text": text}

    assert select_progress_dialogue_action((action,)) is action


def test_action_request_question_with_extra_uncertainty_is_rejected() -> None:
    action = {"ref": "19", "text": "Может быть, где мне искать моряка?"}

    assert select_progress_dialogue_action((action,)) is None


def test_two_equally_positive_replies_remain_ambiguous() -> None:
    first = {"ref": "20", "text": "Я готов помочь и выполню поручение."}
    second = {"ref": "21", "text": "Я готов помочь и выполню поручение иначе."}

    assert select_progress_dialogue_action((first, second)) is None


def test_strong_positive_and_weaker_positive_remain_ambiguous() -> None:
    strong = {"ref": "22", "text": "Я готов помочь и выполню поручение."}
    weaker = {"ref": "23", "text": "Я помогу тебе."}

    assert select_progress_dialogue_action((strong, weaker)) is None


def test_positive_and_unknown_remain_ambiguous() -> None:
    commitment = {"ref": "24", "text": "Я готов выполнить поручение."}
    unknown = {"ref": "25", "text": "Расскажи подробнее."}

    assert select_progress_dialogue_action((commitment, unknown)) is None


def test_two_unknown_replies_remain_ambiguous() -> None:
    first = {"ref": "30", "text": "Расскажи о древнем городе."}
    second = {"ref": "31", "text": "Что здесь произошло?"}

    assert select_progress_dialogue_action((first, second)) is None


@pytest.mark.parametrize(
    "false_positive",
    (
        "Что ты сделал?",
        "Местный изготовитель уже ушёл.",
        "Я не подготовлен к дороге.",
    ),
)
def test_token_boundaries_and_negation_prevent_false_commitment(
    false_positive: str,
) -> None:
    refusal = {"ref": "33", "text": "Не буду этого делать."}
    candidate = {"ref": "32", "text": false_positive}

    assert select_progress_dialogue_action((candidate, refusal)) is None


def test_duplicate_exact_controls_are_not_uniquely_mutable() -> None:
    first = {"ref": "34", "text": "Далее", "control": "first"}
    duplicate = {"ref": "34", "text": "Далее", "control": "second"}

    assert select_progress_dialogue_action((first, duplicate)) is None


def test_promise_reminder_beats_explicit_threat() -> None:
    threat = {
        "ref": "4192",
        "text": "Лучше отдавай книгу, кудесник! Иначе составишь компанию этим мертвецам на полу!",
    }
    reminder = {
        "ref": "4193",
        "text": "Ремесленник посох колдовской для тебя починил, а ты ему книжку обещал. Нехорошо от своих обещаний отказываться.",
    }

    assert select_progress_dialogue_action((threat, reminder)) is reminder


@pytest.mark.parametrize(
    "actions",
    (
        ({"ref": "40", "text": "Я готов помочь."}, {"ref": None, "text": "Не буду."}),
        ({"ref": "40", "text": "Я готов помочь."}, {"ref": "41", "text": None}),
        ({"ref": "40", "text": "Я готов помочь."}, object()),
    ),
)
def test_malformed_alternative_keeps_progress_choice_fail_closed(actions: tuple[object, ...]) -> None:
    assert select_progress_dialogue_action(actions) is None  # type: ignore[arg-type]
