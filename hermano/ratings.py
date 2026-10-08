"""Age gate.

Movies and TV shows: Hermano asks Qloo only for titles rated for the child's age, with
filter.content_rating (MPAA and TV Parental Guidelines values, Insights API reference read
2026-10-08). Titles without one of those ratings are left out.
Video games: Qloo has no rating filter for them, so Hermano drops the games the team knows are
rated above the child's age (ESRB) and sends every other new game to an adult for approval.
These are Hermano's defaults; a school can make them stricter.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

DATA = Path(__file__).resolve().parent / "data"

_UNDER_7 = ("G", "TV-Y", "TV-G")
_FROM_7 = _UNDER_7 + ("PG", "TV-Y7", "TV-Y7-FV", "TV-PG")
_FROM_13 = _FROM_7 + ("PG-13", "TV-14", "13+")
_FROM_16 = _FROM_13 + ("16+",)


def content_ratings_for(age: int) -> Tuple[str, ...]:
    """The filter.content_rating values Hermano accepts for a child of `age`."""
    if age < 7:
        return _UNDER_7
    if age < 13:
        return _FROM_7
    if age < 16:
        return _FROM_13
    return _FROM_16


ESRB_MIN_AGE = {"E": 0, "E10+": 10, "T": 13, "M": 17, "AO": 18}


def game_allowed(rating: Optional[str], age: int) -> Optional[bool]:
    """True or False when the ESRB rating is known; None when it is not, and an adult decides."""
    if not rating or rating not in ESRB_MIN_AGE:
        return None
    return age >= ESRB_MIN_AGE[rating]


def load_game_ratings(path=None) -> Dict[str, str]:
    raw = json.loads(Path(path or DATA / "game_ratings.json").read_text(encoding="utf-8"))
    return {k.casefold(): v for k, v in raw.items() if not k.startswith("_")}
