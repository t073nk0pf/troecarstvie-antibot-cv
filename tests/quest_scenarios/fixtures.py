from __future__ import annotations

from types import MappingProxyType

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry


def entry(
    quest_id: str,
    title: str,
    objective: str,
    *,
    kind: str = "unknown",
    navigation: tuple[tuple[str, str], ...] = (),
    progress: object = None,
) -> ActiveQuestEntry:
    data = MappingProxyType({
        "id": quest_id,
        "title": title,
        "status": "active",
        "objective": objective,
        "objectiveKind": kind,
        "navigation": tuple(
            MappingProxyType({"text": text, "target": target})
            for text, target in navigation
        ),
        "progress": progress,
    })
    return ActiveQuestEntry(quest_id, title, data)


def q280() -> ActiveQuestEntry:
    return entry(
        "280", "Фамильная ступка",
        "Вернитесь к разбойнику Аскорду в Земли Пращуров.",
        kind="dialogue",
        navigation=(("Земли Пращуров", "Земли Пращуров"),),
    )

def q304() -> ActiveQuestEntry:
    return entry(
        "304", "Цветочная болезнь",
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите "
        "в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб "
        "и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, "
        "возвращайтесь к колдунье Вилене.",
        kind="collect",
        navigation=(
            ("Непобедимый кабан", "Непобедимый кабан [5]"),
            ("Пристанище трёх ветров", "Пристанище трёх ветров"),
            ("Длани Рода", "Длань Рода"),
            ("Просторах безмолвия", "Просторы безмолвия"),
        ),
        progress={"current": 5, "required": 5, "complete": False},
    )


def q31() -> ActiveQuestEntry:
    return entry(
        "31", "Поиски Рокоша",
        "Добыть 3 осиных крыла, купить 3 панциря у Богдана, "
        "взять бычий рог у Оксайта.",
        navigation=(("Городская площадь", "Городская площадь"),),
    )


def q360() -> ActiveQuestEntry:
    return entry(
        "360", "Зов Лихих земель",
        "Отправляйтесь к стражу Всебою на Заставу храбрых и сделайте все, "
        "о чем он попросит, затем возвращайтесь к богатырю Туру на городскую площадь Арсы.",
        navigation=(
            ("Заставу храбрых", "Застава храбрых"),
            ("городскую площадь Арсы", "Город Арса"),
        ),
    )
