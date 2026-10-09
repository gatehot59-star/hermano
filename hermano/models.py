"""Data model for Hermano.

Hermano stores the supports a child needs, picked from a closed catalog: never a diagnosis,
never a free-text note. The public demo uses invented students only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

from .guard import validate_interest
from .i18n import pick

ROLES = ("teacher", "principal", "family", "specialist")
# Who may say yes or no to a new motivator for a child linked to them.
APPROVER_ROLES = ("family", "teacher")
PERSONAS = ("hermano", "hermana")
BLOCK_KINDS = ("transition", "class", "break", "special")
# filter.type values accepted by /v2/insights (hackathon developer guide, read 2026-10-08).
ENTITY_TYPES = ("artist", "book", "brand", "destination", "movie", "person",
                "place", "podcast", "tv_show", "video_game")
# What an adult may enter as a child's interest: the kinds /search can look up by type.
INTEREST_KINDS = ("artist", "book", "brand", "movie", "person", "place", "podcast",
                  "tv_show", "video_game")

# Closed catalog: an adult picks supports, never types them.
SUPPORTS: Dict[str, Dict[str, str]] = {
    "warn_before_change": {"en": "a warning before every change of activity",
                           "es": "anticipar cada cambio de actividad"},
    "one_instruction": {"en": "short instructions, one at a time",
                        "es": "consignas cortas, de a una"},
    "quiet_corner": {"en": "a quiet corner available",
                     "es": "rinc\u00f3n tranquilo disponible"},
    "movement_break": {"en": "a movement break every 20 minutes",
                       "es": "pausa de movimiento cada 20 minutos"},
    "split_tasks": {"en": "long tasks split into parts",
                    "es": "tareas largas en partes"},
    "seat_away_from_door": {"en": "a seat away from the door",
                            "es": "lugar lejos de la puerta"},
    "warn_before_speaking": {"en": "a heads-up before speaking in front of the class",
                             "es": "aviso antes de hablar frente al curso"},
    "headphones_noisy_breaks": {"en": "headphones during noisy breaks",
                                "es": "auriculares en recreos ruidosos"},
    "visual_schedule": {"en": "a visual schedule of the day",
                        "es": "agenda visual del d\u00eda"},
    "extra_time": {"en": "extra time for tests",
                   "es": "m\u00e1s tiempo en las evaluaciones"},
}

# When each support makes sense, used to keep the invented demo check-ins coherent.
SUPPORT_KINDS: Dict[str, Tuple[str, ...]] = {
    "one_instruction": ("class", "special"),
    "movement_break": ("class",),
    "split_tasks": ("class",),
    "seat_away_from_door": ("class",),
    "warn_before_speaking": ("class", "special"),
    "headphones_noisy_breaks": ("break", "transition"),
    "extra_time": ("class",),
}

# Closed catalog of what a child's specialist may ask the child to practice at school: everyday
# moves and phrases, never a treatment and never a diagnosis. The specialist picks codes.
EXERCISES: Dict[str, Dict[str, str]] = {
    "breathe_square": {"en": "square breathing: in for 4, hold for 4, out for 4, wait for 4, three times",
                       "es": "respiraci\u00f3n cuadrada: tomar aire en 4, sostener 4, soltar en 4, esperar 4, tres veces"},
    "wall_push": {"en": "push the wall with both hands for 10 seconds, three times",
                  "es": "empujar la pared con las dos manos 10 segundos, tres veces"},
    "hand_squeeze": {"en": "squeeze your hands into fists and let go, five times",
                     "es": "apretar las manos y soltarlas, cinco veces"},
    "paper_balls": {"en": "make five small paper balls with your fingertips",
                    "es": "hacer cinco bolitas de papel con la punta de los dedos"},
    "balance_one_foot": {"en": "stand on one foot and count to 10, then on the other",
                         "es": "pararte en un pie y contar hasta 10, despu\u00e9s en el otro"},
    "feelings_word": {"en": "say in one word how you feel right now",
                      "es": "decir con una palabra c\u00f3mo te sent\u00eds ahora"},
    "ask_for_help": {"en": "practice asking for help with one sentence: can you help me?",
                     "es": "practicar pedir ayuda con una frase: \u00bfme ayud\u00e1s?"},
    "say_hello": {"en": "say hello to someone in your class, with words or with a wave",
                  "es": "saludar a alguien de tu clase, con palabras o con la mano"},
}

# Where each exercise fits in the school day.
EXERCISE_KINDS: Dict[str, Tuple[str, ...]] = {
    "breathe_square": ("transition", "break", "class"),
    "wall_push": ("break", "transition"),
    "hand_squeeze": ("class",),
    "paper_balls": ("class", "break"),
    "balance_one_foot": ("break",),
    "feelings_word": ("transition", "break"),
    "ask_for_help": ("class", "special"),
    "say_hello": ("transition",),
}

Text = Union[str, Dict[str, str]]


def support_text(code: str, lang: str) -> str:
    return pick(SUPPORTS[code], lang)


def exercise_text(code: str, lang: str) -> str:
    return pick(EXERCISES[code], lang)


@dataclass
class Interest:
    """Something the child likes, entered by an adult: a show, a game, a band."""
    name: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in INTEREST_KINDS:
            raise ValueError(f"unsupported interest kind: {self.kind}")


@dataclass
class Student:
    """A child. `persona` is the companion the child chose: hermano or hermana."""
    id: str
    alias: str
    age: int
    grade: Text
    persona: str
    supports: List[str]
    interests: List[Interest]
    exercises: List[str] = field(default_factory=list)  # codes the child's specialist picked

    def __post_init__(self) -> None:
        if self.persona not in PERSONAS:
            raise ValueError(f"persona must be one of {PERSONAS}")
        if not isinstance(self.age, int) or not 3 <= self.age <= 21:
            raise ValueError("age must be a whole number from 3 to 21")
        unknown = [s for s in self.supports if s not in SUPPORTS]
        if unknown:
            # The text is not echoed: it may be exactly what must not be stored.
            raise ValueError(f"{len(unknown)} support(s) of {self.id} are not in the catalog")
        unknown = [e for e in self.exercises if e not in EXERCISES]
        if unknown:
            raise ValueError(f"{len(unknown)} exercise(s) of {self.id} are not in the catalog")


@dataclass
class Adult:
    """An adult linked to some children. Each adult only sees their own children."""
    id: str
    name: Text
    role: str
    student_ids: List[str]

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")


@dataclass
class Block:
    """One step of the school day."""
    start: str
    end: str
    activity: Text
    kind: str
    topic: Text = ""

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise ValueError(f"block kind must be one of {BLOCK_KINDS}")

    def name(self, lang: str) -> str:
        return pick(self.activity, lang)

    def topic_name(self, lang: str) -> str:
        return pick(self.topic, lang)


@dataclass
class School:
    name: Text
    students: Dict[str, Student]
    adults: Dict[str, Adult]
    schedules: Dict[str, List[Block]]

    def roster(self) -> List[str]:
        """The children's names, which must never leave Hermano."""
        return [s.alias for s in self.students.values()]


def load_school(path) -> School:
    """Load a school from JSON; check links, the support catalog and every interest."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    students = {}
    for s in raw["students"]:
        students[s["id"]] = Student(
            id=s["id"], alias=s["alias"], age=s["age"], grade=s["grade"],
            persona=s["persona"], supports=list(s["supports"]),
            interests=[Interest(**i) for i in s["interests"]],
            exercises=list(s.get("exercises", [])))
    roster = [s.alias for s in students.values()]
    for st in students.values():
        for it in st.interests:
            validate_interest(it.name, roster)
    adults = {a["id"]: Adult(**a) for a in raw["adults"]}
    for a in adults.values():
        for sid in a.student_ids:
            if sid not in students:
                raise ValueError(f"adult {a.id} linked to unknown student {sid}")
    schedules = {sid: [Block(**b) for b in blocks] for sid, blocks in raw["schedules"].items()}
    for sid in students:
        if sid not in schedules:
            raise ValueError(f"student {sid} has no schedule")
    return School(raw["school"], students, adults, schedules)
