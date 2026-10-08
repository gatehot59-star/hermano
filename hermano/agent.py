"""Hermano agent: plans the child's day with their own tastes and writes one report per adult.

The child picks a companion, hermano or hermana. Qloo turns the child's interests into
related books, music, shows, games and movies; Hermano uses them as cues, rewards, calm-down
options and readings, and rotates them so they stay fresh. At the end of the day the child
says how each step went, and every adult linked to that child gets a different report.
Hermano never diagnoses, and health words are scrubbed from every report.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import Adult, Block, School, Student

DOMAINS = ("book", "artist", "tv_show", "video_game", "movie")
FORBIDDEN_RE = re.compile(
    r"\b\w*(?:diagn|autis|trastorno)\w*\b|\btea\b|\btdah?\b|\badhd\b|\basd\b", re.IGNORECASE)
REDACTED = "[dato de salud omitido]"

PERSONA = {
    "hermano": "Soy tu hermano",
    "hermana": "Soy tu hermana",
}
REWARD_TEXT = {
    "book": "leer un ratito de «{x}»",
    "artist": "escuchar un tema de {x}",
    "tv_show": "dibujar algo de «{x}»",
    "video_game": "contar tu parte favorita de «{x}»",
    "movie": "elegir una escena de «{x}» y contarla",
}
DEFAULT_REWARD = "elegir un juego con tu docente"
CALM_BASE = ["respirar contando hasta 5", "ir un ratito al rincón tranquilo", "apretar la pelotita"]
FACES = ("bien", "mas_o_menos", "dificil")
FACE_ICON = {"bien": "😀", "mas_o_menos": "😐", "dificil": "😟"}
FACE_WORD = {"bien": "bien", "mas_o_menos": "más o menos", "dificil": "difícil"}


def scrub(text: str) -> str:
    """Remove health and diagnosis words: reports talk about supports, not conditions."""
    return FORBIDDEN_RE.sub(REDACTED, text)


@dataclass
class TasteProfile:
    student_id: str
    signals: List[str]
    by_domain: Dict[str, List[str]]

    def pool(self) -> List[Tuple[str, str]]:
        """All related items, interleaved by domain so consecutive rewards change domain."""
        lists = {d: list(self.by_domain.get(d, [])) for d in DOMAINS}
        out: List[Tuple[str, str]] = []
        while any(lists.values()):
            for d in DOMAINS:
                if lists[d]:
                    out.append((d, lists[d].pop(0)))
        return out


def build_profile(student: Student, qloo, take: int = 5) -> TasteProfile:
    """Expand the child's interests across domains with Qloo.

    Only interest names and Qloo entity ids are sent: never the child's alias, id or supports.
    """
    ids: List[str] = []
    for it in student.interests:
        eid = qloo.search(it.name, it.kind)
        if eid:
            ids.append(eid)
    own = {i.name.lower() for i in student.interests}
    by_domain: Dict[str, List[str]] = {}
    if ids:
        for d in DOMAINS:
            items = qloo.insights(d, ids, take=take)
            by_domain[d] = [x["name"] for x in items if x["name"].lower() not in own]
    return TasteProfile(student.id, [i.name for i in student.interests], by_domain)


class Rotation:
    """Remembers which motivators each child already got, so they stay fresh."""

    def __init__(self) -> None:
        self.used: Dict[str, List[Tuple[str, str]]] = {}

    def pick(self, student_id: str, pool: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
        if not pool:
            return None
        used = self.used.setdefault(student_id, [])
        fresh = [p for p in pool if p not in used]
        if not fresh:
            used.clear()
            fresh = list(pool)
        used.append(fresh[0])
        return fresh[0]


@dataclass
class Step:
    block: Block
    first_then: str
    warning: str
    calm: List[str]
    reward_domain: str
    reward_item: str
    reward: str
    reading: str = ""


def plan_day(student: Student, blocks: List[Block], profile: TasteProfile, rotation: Rotation) -> Dict:
    """One step per block: first/then cue, change warning, calm-down options, reward, reading."""
    pool = profile.pool()
    artists = profile.by_domain.get("artist", [])
    books = profile.by_domain.get("book", [])
    steps: List[Step] = []
    book_i = 0
    for i, b in enumerate(blocks):
        pick = rotation.pick(student.id, pool)
        if pick:
            dom, item = pick
            reward = REWARD_TEXT[dom].format(x=item)
        else:
            dom, item, reward = "", "", DEFAULT_REWARD
        warning = ""
        if i > 0:
            warning = f"En 5 minutos termina {blocks[i - 1].activity}. Después viene {b.activity}."
        calm = list(CALM_BASE)
        if artists:
            calm.insert(0, f"escuchar {artists[i % len(artists)]} con auriculares")
        reading = ""
        if b.kind == "class" and b.topic and books:
            reading = f"Para {b.topic}: «{books[book_i % len(books)]}» puede ser la puerta de entrada."
            book_i += 1
        steps.append(Step(b, f"Primero: {b.activity}. Después: {reward}.", warning, calm,
                          dom, item, reward, reading))
    greeting = f"{PERSONA[student.persona]}. Hoy te acompaño en cada paso del día."
    return {"student": student, "greeting": greeting, "steps": steps}


@dataclass
class CheckIn:
    """How a step went, told by the child with a face; the support that helped, if any."""
    activity: str
    face: str
    support_used: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.face not in FACES:
            raise ValueError(f"face must be one of {FACES}")


def demo_checkins(student: Student, plan: Dict, day: int) -> List[CheckIn]:
    """Deterministic invented check-ins for the demo."""
    out = []
    base = sum(map(ord, student.id))
    for i, st in enumerate(plan["steps"]):
        code = (base + i * 7 + day * 3) % 10
        face = "dificil" if code < 2 else ("mas_o_menos" if code < 5 else "bien")
        support = ""
        if face != "bien" and student.supports:
            support = student.supports[i % len(student.supports)]
        out.append(CheckIn(st.block.activity, face, support))
    return out


def _pairs(records: Dict, sid: str):
    return list(zip(records[sid]["plan"]["steps"], records[sid]["checkins"]))


def _teacher(adult: Adult, school: School, records: Dict, sids: List[str]) -> List[str]:
    out = [f"Informe del día para {adult.name}"]
    for sid in sids:
        st = school.students[sid]
        pairs = _pairs(records, sid)
        good = [(s, c) for s, c in pairs if c.face == "bien"]
        hard = [(s, c) for s, c in pairs if c.face == "dificil"]
        out.append("")
        out.append(f"{st.alias} ({st.grade})")
        if good:
            out.append("Funcionó: " + "; ".join(
                c.activity + (f", con «{s.reward_item}»" if s.reward_item else "") for s, c in good[:3]) + ".")
        if hard:
            out.append("Costó: " + "; ".join(
                c.activity + (f" (sirvió: {c.support_used})" if c.support_used else "") for s, c in hard) + ".")
            out.append(f"Para mañana: anticipar {hard[0][1].activity} con un aviso visual y un motivador nuevo.")
        reads = [s.reading for s, c in pairs if s.reading]
        if reads:
            out.append("Lecturas sugeridas: " + " ".join(reads))
    return out


def _principal(adult: Adult, school: School, records: Dict, sids: List[str]) -> List[str]:
    faces: Counter = Counter()
    supports: Counter = Counter()
    attention = []
    for sid in sids:
        cks = records[sid]["checkins"]
        faces.update(c.face for c in cks)
        supports.update(c.support_used for c in cks if c.support_used)
        if sum(c.face == "dificil" for c in cks) >= 2:
            attention.append(school.students[sid].alias)
    total = sum(faces.values())
    out = [f"Resumen del día para {adult.name}",
           f"Chicos acompañados: {len(sids)}",
           f"Momentos registrados: {total} ({FACE_ICON['bien']} {faces['bien']}, "
           f"{FACE_ICON['mas_o_menos']} {faces['mas_o_menos']}, {FACE_ICON['dificil']} {faces['dificil']})"]
    if supports:
        out.append("Apoyos más usados: " + "; ".join(f"{s} ({n})" for s, n in supports.most_common(3)))
    out.append("Para mirar mañana: " + (", ".join(attention) if attention
                                         else "nadie con dos o más momentos difíciles"))
    return out


def _family(adult: Adult, school: School, records: Dict, sids: List[str]) -> List[str]:
    out: List[str] = []
    for sid in sids:
        st = school.students[sid]
        pairs = _pairs(records, sid)
        good = [c for s, c in pairs if c.face == "bien"]
        hard = [c for s, c in pairs if c.face == "dificil"]
        if out:
            out.append("")
        out.append(f"Hola, familia de {st.alias}.")
        out.append(f"Hoy {st.alias} contó que {len(good)} de {len(pairs)} momentos le fueron bien.")
        if good:
            out.append(f"Lo que mejor le salió: {good[0].activity}.")
        if hard:
            extra = f" En la escuela sirvió: {hard[0].support_used}." if hard[0].support_used else ""
            out.append(f"Lo que más le costó: {hard[0].activity}.{extra}")
        items = [s.reward_item for s, c in pairs if s.reward_item and c.face == "bien"] or \
                [s.reward_item for s, c in pairs if s.reward_item]
        if items:
            out.append(f"Para charlar en casa: preguntale por «{items[0]}».")
    return out


def _specialist(adult: Adult, school: School, records: Dict, sids: List[str]) -> List[str]:
    out = [f"Registro de observación para {adult.name}"]
    for sid in sids:
        st = school.students[sid]
        out.append("")
        out.append(f"{st.alias} · {st.grade} · apoyos acordados: {', '.join(st.supports) or '-'}")
        out.append("hora | actividad | cómo le fue (autoinforme) | apoyo usado | motivador")
        for s, c in _pairs(records, sid):
            out.append(f"{s.block.start}-{s.block.end} | {c.activity} | {FACE_WORD[c.face]} | "
                       f"{c.support_used or '-'} | {s.reward_item or '-'}")
    return out


WRITERS = {"teacher": _teacher, "principal": _principal, "family": _family, "specialist": _specialist}


def report_for(adult: Adult, school: School, records: Dict) -> str:
    """The report for one adult, covering only the children linked to that adult."""
    sids = [sid for sid in adult.student_ids if sid in records]
    return scrub("\n".join(WRITERS[adult.role](adult, school, records, sids)))


def run_day(school: School, qloo, rotation: Rotation, day: int = 0,
            profiles: Optional[Dict[str, TasteProfile]] = None):
    """Plan and (invented) check-ins for every child; returns (records, profiles)."""
    if profiles is None:
        profiles = {sid: build_profile(st, qloo) for sid, st in school.students.items()}
    records = {}
    for sid, st in school.students.items():
        plan = plan_day(st, school.schedules[sid], profiles[sid], rotation)
        records[sid] = {"plan": plan, "checkins": demo_checkins(st, plan, day)}
    return records, profiles
