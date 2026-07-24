"""Shared bounded grammar for quest return and delivery clauses."""

from __future__ import annotations

import re


# Keep this grammar deliberately small.  It exists to make acquisition and
# turn-in parsers agree on known game wording, including the live typo
# ``возращайтесь``.  Unknown wording must remain fail-closed rather
# than being guessed from arbitrary prose.
RETURN_VERB_PATTERN = (
    r"(?:возвращайтесь|возращайтесь|вернитесь|возвратитесь)"
)
DELIVERY_VERB_PATTERN = r"(?:отнесите|отнести|передайте|передать)"
NAVIGATION_VERB_PATTERN = (
    rf"(?:{RETURN_VERB_PATTERN}|{DELIVERY_VERB_PATTERN}|"
    r"идите|отправляйтесь|следуйте|направляйтесь)"
)

_NAVIGATION_CLAUSE = re.compile(
    rf"(?:^|\s)(?:и|а\s+затем|после\s+чего|затем)?\s*{NAVIGATION_VERB_PATTERN}\b",
    re.IGNORECASE,
)
_RETURN_ADDRESS_CLAUSE = re.compile(
    r"(?:^|\s)(?:и|а\s+затем|после\s+чего|затем)\s+"
    r"(?:[а-яё-]+\s+){0,3}"
    r"(?:(?:в|во|на)\s+[^,.;]+?\s+к\s+[^,.;]+|"
    r"к\s+[^,.;]+?\s+(?:в|во|на)\s+[^,.;]+)$",
    re.IGNORECASE,
)
_UNKNOWN_IMPERATIVE_CLAUSE = re.compile(
    r"(?:^|[\s—–])(?:и\s+|а\s+затем\s+|после\s+чего\s+|затем\s+)?"
    r"[а-яё-]{3,}(?:йтесь|итесь|йте)\b",
    re.IGNORECASE,
)


def contains_navigation_clause(value: str) -> bool:
    """Whether an alleged item name contains a known action clause."""

    return bool(
        _NAVIGATION_CLAUSE.search(value)
        or _RETURN_ADDRESS_CLAUSE.search(value)
        or _UNKNOWN_IMPERATIVE_CLAUSE.search(value)
    )
