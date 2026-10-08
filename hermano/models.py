"""Data model for Hermano.

Hermano stores the supports a child needs, never a diagnosis. The public demo uses
invented students only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

ROLES = ("teacher", "principal", "family", "specialist")
PERSONAS = ("hermano", "hermana")
# Entity types accepted by Qloo's Insights API (hackathon developer guide).
ENTITY_TYPES = ("artist", "book", "brand", "destination", "movie", "person",
                "place", "podcast", "tv_show", "video_game")


@dataclass
class Interest:
    """Something the child likes, entered by an adult: a show, a game, a band."""
    name: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_TYPES:
            raise ValueError(f"unsupported interest kind: {self.kind}")


@dataclass
class Student:
    """A child. `persona` is the companion the child chose: hermano or hermana."""
    id: str
    alias: str
    age: int
    grade: str
    persona: str
    supports: List[str]
    interests: List[Interest]

    def __post_init__(self) -> None:
        if self.persona not in PERSONAS:
            raise ValueError(f"persona must be one of {PERSONAS}")


@dataclass
class Adult:
    """An adult linked to some children. Each adult only sees their own children."""
    id: str
    name: str
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
    activity: str
    kind: str
    topic: str = ""


@dataclass
class School:
    name: str
    students: Dict[str, Student]
    adults: Dict[str, Adult]
    schedules: Dict[str, List[Block]]


def load_school(path) -> School:
    """Load a school from JSON and check that every link points to a real child."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    students = {}
    for s in raw["students"]:
        students[s["id"]] = Student(
            id=s["id"], alias=s["alias"], age=s["age"], grade=s["grade"],
            persona=s["persona"], supports=list(s["supports"]),
            interests=[Interest(**i) for i in s["interests"]])
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
