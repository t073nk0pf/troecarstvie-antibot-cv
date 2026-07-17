#!/usr/bin/env python3
"""Build path skill catalogs from the official 3kingdoms library."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


BASE_URL = "https://3kingdoms.ru/info/library/index.php?obj=cat&id={}"
PATHS = {
    25: "Витязь",
    26: "Следопыт",
    182: "Берсерк",
    27: "Чародей",
    23: "Волхв",
    183: "Заклинатель",
    22: "Разбойник",
    24: "Ратоборец",
    184: "Страж",
}
FILENAMES = {
    "Витязь": "VITYAZ.md",
    "Следопыт": "SLEDOPYT.md",
    "Берсерк": "BERSERK.md",
    "Чародей": "CHARODEY.md",
    "Волхв": "VOLKHV.md",
    "Заклинатель": "ZAKLINATEL.md",
    "Разбойник": "RAZBOYNIK.md",
    "Ратоборец": "RATOBORETS.md",
    "Страж": "STRAZH.md",
}
MODEL_RE = re.compile(
    r"_top\(\)\.art_alt\['AA_(\d+)'\]\s*=\s*(\{.*?\});</script>", re.S
)
RANK_RE = re.compile(r"^(.*?)\s+(I|II|III|IV|V|VI)(?:\s+(старая))?$", re.I)
RANK_LEVEL_BASE = {"I": 1, "II": 3, "III": 5, "IV": 7, "V": 9, "VI": 15}
DRAGON_LEVEL = {"I": 5, "II": 7, "III": 9, "IV": 11, "V": 13}
DRAGON_SKILLS = {"Бессильная злость", "Устрашение дракона", "Ярость дракона"}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = TextExtractor()
    parser.feed(html.unescape(value))
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return " ".join(line for line in lines if line)


def md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def fetch_models(category_id: int) -> list[dict]:
    with urllib.request.urlopen(BASE_URL.format(category_id), timeout=30) as response:
        page = response.read().decode("cp1251")
    models = []
    for reference_id, blob in MODEL_RE.findall(page):
        model = json.loads(blob)
        match = RANK_RE.match(model["title"].strip())
        if match:
            base_name, rank, legacy = match.groups()
        else:
            base_name, rank, legacy = model["title"].strip(), "—", None
        models.append(
            {
                "reference_id": reference_id,
                "title": model["title"].strip(),
                "base_name": base_name.strip(),
                "rank": rank,
                "legacy": bool(legacy),
                "kind": model.get("kind", {}).get("value", ""),
                "path": model.get("classw", {}).get("value", ""),
                "description": plain_text(model.get("desc", "")),
                "image": model.get("image", ""),
                "color": model.get("color", ""),
            }
        )
    standard_rank_one = [
        model["base_name"]
        for model in models
        if model["rank"] == "I" and model["base_name"] not in DRAGON_SKILLS
    ]
    family_position = {name: position for position, name in enumerate(standard_rank_one)}
    for model in models:
        if model["base_name"] in DRAGON_SKILLS:
            model["library_level"] = DRAGON_LEVEL.get(model["rank"])
        elif model["rank"] in RANK_LEVEL_BASE and model["base_name"] in family_position:
            model["library_level"] = RANK_LEVEL_BASE[model["rank"]] + family_position[model["base_name"]]
        else:
            model["library_level"] = None
    return models


def render_path(category_id: int, path_name: str, models: list[dict], checked: str) -> str:
    groups: dict[str, list[dict]] = {}
    for model in models:
        groups.setdefault(model["base_name"], []).append(model)
    lines = [
        f"# Способности пути «{path_name}»",
        "",
        f"Источник: [официальная библиотека]({BASE_URL.format(category_id)}). Проверено: {checked}.",
        "",
        "Статус: `LIBRARY`. Данные могут быть историческими; перед использованием в текущей механике их необходимо сверять с LIVE-tooltip книги заклинаний.",
        "",
        f"Моделей способностей: {len(models)}. Базовых названий: {len(groups)}.",
        "",
    ]
    for base_name, variants in groups.items():
        lines.extend(
            [
                f"## {base_name}",
                "",
                "| Ранг | Уровень по таблице | Reference ID | Эффект и ограничения |",
                "|---:|---:|---:|---|",
            ]
        )
        for model in variants:
            rank = model["rank"] + (" (старая)" if model["legacy"] else "")
            lines.append(
                f"| {rank} | {model['library_level'] if model['library_level'] is not None else '—'} | `{model['reference_id']}` | {md_escape(model['description'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Ограничения источника",
            "",
            "- Формулировки сохранены по tooltip-моделям библиотеки без пересчёта баланса.",
            "- Уровень взят из подписи под соответствующей иконкой в таблице библиотеки; это поле находится вне tooltip JSON и сопоставлено позиционно.",
            "- `Reference ID` относится к библиотечной модели и не является account-instance ID.",
            "- Отсутствие ограничения в тексте не доказывает отсутствие серверной проверки.",
            "- Различия между рангами сохранены дословно, включая повторяющиеся значения и legacy-записи.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    output_dir = Path("docs/3kingdoms/skills")
    output_dir.mkdir(parents=True, exist_ok=True)
    checked = dt.date.today().isoformat()
    summary = []
    for category_id, path_name in PATHS.items():
        models = fetch_models(category_id)
        filename = FILENAMES[path_name]
        (output_dir / filename).write_text(
            render_path(category_id, path_name, models, checked), encoding="utf-8"
        )
        bases = {model["base_name"] for model in models}
        summary.append((path_name, category_id, filename, len(models), len(bases)))
        print(f"wrote {output_dir / filename}: {len(models)} models")

    index_lines = [
        "# Способности всех путей развития",
        "",
        f"Сформировано: {checked}. Источник: официальная библиотека «Троецарствия».",
        "",
        "Это `LIBRARY`-каталог. [LIVE-книга текущего Стража](../SPELLBOOK_SKILLS.md) хранится отдельно.",
        "",
        "| Путь | Категория | Моделей | Базовых названий | Файл |",
        "|---|---:|---:|---:|---|",
    ]
    for path_name, category_id, filename, model_count, base_count in summary:
        index_lines.append(
            f"| {path_name} | [{category_id}]({BASE_URL.format(category_id)}) | {model_count} | {base_count} | [{filename}]({filename}) |"
        )
    index_lines.extend(
        [
            "",
            f"Всего моделей: {sum(item[3] for item in summary)}.",
            "",
            "Каталоги пересобираются командой `python3 scripts/build_3kingdoms_skill_catalogs.py`.",
            "",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(index_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
