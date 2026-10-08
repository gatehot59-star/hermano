"""Privacy guard: what may leave Hermano, and what an adult may read.

1. Only interest names go to Qloo, never a child's name, id or supports, and never text that
   looks like health or contact data. The Qloo client refuses that text by itself too.
2. Reports never carry health words, even if an adult typed one somewhere.
3. When a language model is added, it will see placeholders (STUDENT_1), never names.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable

from .i18n import t

# Word starts, accent-insensitive, Spanish and English: "autis" catches autismo, autista,
# autism and autistic.
HEALTH_STEMS = (
    "diagn", "autis", "asperg", "trastorn", "disorder", "sindrom", "syndrom",
    "discapac", "disabil", "dislex", "dyslex", "discalcul", "dyscalcul", "disgraf", "dysgraph",
    "disprax", "dysprax", "epilep", "convuls", "seizur",
    "medic", "farmac", "pharmac", "metilfenid", "methylphenid", "ritalin", "atomoxet",
    "risperid", "aripipraz", "fluoxet", "sertral", "melaton",
    "psiquiat", "psychiat", "neurolog", "patolog", "patholog", "enfermedad", "illness", "diseas",
)
# Whole words only: acronyms and short words that also live inside ordinary words.
HEALTH_WORDS = frozenset((
    "tea", "tdah", "tda", "adhd", "asd", "tgd", "toc", "ocd", "tel", "cud",
    "pastilla", "pastillas", "pill", "pills", "dosis", "dose", "doses", "mg", "concerta",
))
_TOKEN = re.compile(r"\w+", re.UNICODE)
_DOSE = re.compile(r"^\d+mg$")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
MAX_INTEREST_CHARS = 60
MAX_INTEREST_WORDS = 8


class UnsafeText(ValueError):
    """Text that must not leave Hermano. `reason` says why, without repeating the text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fold(text: str) -> str:
    """Lowercase without accents: Medicaci\u00f3n -> medicacion."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


def _is_health_token(token: str) -> bool:
    f = fold(token)
    return f in HEALTH_WORDS or f.startswith(HEALTH_STEMS) or bool(_DOSE.match(f))


def contains_health(text: str) -> bool:
    return any(_is_health_token(m.group(0)) for m in _TOKEN.finditer(text or ""))


def contains_contact(text: str) -> bool:
    """An e-mail, a web address, or 6 or more digits (a phone or an id number)."""
    text = text or ""
    if _EMAIL.search(text) or _URL.search(text):
        return True
    return sum(c.isdigit() for c in text) >= 6


def scrub(text: str, lang: str = "en") -> str:
    """Replace every health word with a neutral mark."""
    mark = t(lang, "redacted")
    return _TOKEN.sub(lambda m: mark if _is_health_token(m.group(0)) else m.group(0), text)


def validate_interest(name: str, roster: Iterable[str] = ()) -> str:
    """The interest name as it may be sent to Qloo, or UnsafeText.

    `roster` holds the children's names: an interest that contains one is refused, even when
    it is also a real title. Privacy wins over convenience.
    """
    clean = " ".join((name or "").split())
    if not clean:
        raise UnsafeText("empty")
    if len(clean) > MAX_INTEREST_CHARS or len(clean.split()) > MAX_INTEREST_WORDS:
        raise UnsafeText("too long")
    if contains_health(clean):
        raise UnsafeText("health detail")
    if contains_contact(clean):
        raise UnsafeText("contact data")
    names = {fold(tok) for person in roster for tok in _TOKEN.findall(person)}
    if any(fold(tok) in names for tok in _TOKEN.findall(clean)):
        raise UnsafeText("child name")
    return clean


def guard_query(text: str) -> str:
    """Last check inside the Qloo client: no health or contact data in a query."""
    if contains_health(text):
        raise UnsafeText("health detail")
    if contains_contact(text):
        raise UnsafeText("contact data")
    return text


class Pseudonymizer:
    """Swaps names for placeholders before text goes to a language model, and back."""

    def __init__(self, students: Iterable[str] = (), adults: Iterable[str] = ()) -> None:
        self.forward: Dict[str, str] = {}
        for i, name in enumerate(students, 1):
            self.forward[name] = f"STUDENT_{i}"
        for i, name in enumerate(adults, 1):
            self.forward[name] = f"ADULT_{i}"
        self.back = {v: k for k, v in self.forward.items()}

    @staticmethod
    def _swap(text: str, table: Dict[str, str], flags: int) -> str:
        for old in sorted(table, key=len, reverse=True):
            pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
            text = re.sub(pattern, table[old], text, flags=flags)
        return text

    def mask(self, text: str) -> str:
        return self._swap(text, self.forward, re.IGNORECASE)

    def unmask(self, text: str) -> str:
        return self._swap(text, self.back, 0)
