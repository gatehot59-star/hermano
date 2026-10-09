"""Age gate.

Every idea from Qloo passes this gate before any adult sees it, and the child's own favorites pass
it too before they become rewards. These are Hermano's defaults; a school can make them stricter.

Movies and TV shows: Hermano asks Qloo only for titles rated for the child's age, with
filter.content_rating (MPAA and TV Parental Guidelines values, Insights API reference read
2026-10-08), and checks the rating Qloo sends back again in code, in case a filter is ignored.
Video games: Qloo has no rating filter for them, but its answers carry the ESRB rating
(properties.content_rating, seen live 2026-10-08: Genshin Impact T, Goat Simulator M). Hermano
takes the stricter of Qloo's rating and the team's table, drops games rated above the child's
age, and for children under 13 also drops games with no rating at all.
Music and books: Qloo gives no age rating. For children under 13, a new artist or book is only
proposed when Qloo tags it for children or families (seen live 2026-10-08: Raffi, The Wiggles,
KIDZ BOP and Cocomelon carry Family-Oriented or Childrens; ChillinIT and Bizarrap do not).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

DATA = Path(__file__).resolve().parent / "data"

_UNDER_7 = ("G", "TV-Y", "TV-G")
_FROM_7 = _UNDER_7 + ("PG", "TV-Y7", "TV-Y7-FV", "TV-PG")
_FROM_13 = _FROM_7 + ("PG-13", "TV-14", "13+")
_FROM_16 = _FROM_13 + ("16+",)

# Below this age, ideas Qloo cannot place for children are not proposed at all.
CHILD_AGE = 13
# Qloo tag names that mark an artist or a book as made for children or families.
CHILD_TAGS = frozenset({"childrens", "children's", "children's music", "middle grade",
                        "picture books", "juvenile", "family-oriented", "family-friendly",
                        "child-friendly"})
# Tag names that keep a book or an artist away from every child.
ADULT_TAGS = frozenset({"erotica", "adult fiction", "explicit"})


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
    """True or False when the ESRB rating is known; None when it is not."""
    if not rating or rating not in ESRB_MIN_AGE:
        return None
    return age >= ESRB_MIN_AGE[rating]


def strictest(*ratings: Optional[str]) -> str:
    """The known ESRB rating with the highest minimum age, or '' if none is known."""
    known = [r for r in ratings if r in ESRB_MIN_AGE]
    return max(known, key=ESRB_MIN_AGE.__getitem__) if known else ""


def block_reason(domain: str, age: int, rating: str = "", tags: Iterable[str] = (),
                 team_rating: Optional[str] = None, typed_by_adult: bool = False) -> str:
    """Why an idea is not offered for a child of `age` ('rating', 'unrated', 'not_for_children'
    or 'adult'), or '' when it may go on. typed_by_adult is for the child's own interests, which
    an adult entered: a known rating still applies to them, a missing one does not."""
    rating = (rating or "").strip()
    names = {str(t).strip().casefold() for t in tags or ()}
    if domain in ("movie", "tv_show"):
        if rating:
            return "" if rating in content_ratings_for(age) else "rating"
        return "" if typed_by_adult else "unrated"
    if domain == "video_game":
        allowed = game_allowed(strictest(rating, team_rating), age)
        if allowed is False:
            return "rating"
        if allowed is None and age < CHILD_AGE and not typed_by_adult:
            return "unrated"
        return ""
    if names & ADULT_TAGS:
        return "adult"
    if typed_by_adult or age >= CHILD_AGE or domain not in ("book", "artist"):
        return ""
    return "" if names & CHILD_TAGS else "not_for_children"


def load_game_ratings(path=None) -> Dict[str, str]:
    raw = json.loads(Path(path or DATA / "game_ratings.json").read_text(encoding="utf-8"))
    return {k.casefold(): v for k, v in raw.items() if not k.startswith("_")}
