"""Hermano agent: plans the child's day with their own tastes and writes one report per adult.

The child picks a companion, hermano or hermana. Qloo turns the child's interests into related
books, music, shows, games and movies, already filtered for the child's age. Each new idea
waits for an adult's OK before the child sees it. The child's own favorites, which an adult
typed, are rewards from day one; approved ideas join them after classes and as calm-down
options, and they rotate so they stay fresh. At the end of the day the child says how
each step went, and every adult linked to that child gets a different report. Hermano never
diagnoses, and health words are scrubbed from every report.

The rule-based planner here always runs first. planner.py lets a language model choose among
adult-approved ideas and suggest new ones for an adult to approve, all checked in code.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .approvals import APPROVED, PENDING, REJECTED, Approvals
from .guard import UnsafeText, scrub, validate_interest
from .i18n import DEFAULT_LANG, pick, q, t
from .models import BLOCK_KINDS, SUPPORT_KINDS, SUPPORTS, Adult, Block, School, Student, support_text
from .qloo import QlooError, UnsupportedType
from .ratings import block_reason, content_ratings_for, load_game_ratings

DOMAINS = ("book", "artist", "tv_show", "video_game", "movie")
# Rewards come after effort: never after recess, arrival or dismissal.
WORK_KINDS = ("class", "special")
FACES = ("bien", "mas_o_menos", "dificil")
FACE_ICON = {"bien": "\U0001F600", "mas_o_menos": "\U0001F610", "dificil": "\U0001F61F"}


@dataclass
class Candidate:
    """An idea from Qloo for one child."""
    domain: str
    name: str
    entity_id: str = ""


@dataclass
class TasteProfile:
    student_id: str
    signals: List[str]
    by_domain: Dict[str, List[Candidate]]
    blocked: List[Tuple[str, str]] = field(default_factory=list)  # kept out by the age gate
    why_blocked: Dict[str, str] = field(default_factory=dict)  # "domain:name" -> reason
    own: List[Candidate] = field(default_factory=list)  # the child's favorites, typed by an adult
    refused: List[str] = field(default_factory=list)  # why an interest was not sent; not the text
    degraded: str = ""

    def pool(self) -> List[Candidate]:
        """All ideas, interleaved by domain so consecutive rewards change domain."""
        lists = {d: list(self.by_domain.get(d, [])) for d in DOMAINS}
        out: List[Candidate] = []
        while any(lists.values()):
            for d in DOMAINS:
                if lists[d]:
                    out.append(lists[d].pop(0))
        return out


def _gate(profile: TasteProfile, student: Student, domain: str, item: dict, ratings: Dict[str, str],
          typed_by_adult: bool = False) -> bool:
    """True if the idea passes the age gate; otherwise it is recorded in profile.blocked."""
    why = block_reason(domain, student.age, item.get("rating", ""), item.get("tags", ()),
                       ratings.get(item["name"].casefold()), typed_by_adult)
    if not why:
        return True
    profile.blocked.append((domain, item["name"]))
    profile.why_blocked[f"{domain}:{item['name']}"] = why
    return False


def _lookup(qloo, name: str, kind: str) -> Optional[dict]:
    find = getattr(qloo, "search_entity", None)
    if find is not None:
        return find(name, kind)
    eid = qloo.search(name, kind)
    return {"id": eid} if eid else None


def build_profile(student: Student, qloo, roster=(), approvals: Optional[Approvals] = None,
                  take: int = 5, game_ratings: Optional[Dict[str, str]] = None) -> TasteProfile:
    """Expand the child's interests across domains with Qloo, for the child's age.

    Only safe interest names and Qloo entity ids are sent: never the child's alias, id or
    supports. Every idea passes the age gate in ratings.py. The child's own interests in a
    reward domain become profile.own once they pass the gate too. If Qloo does not answer, the
    profile has no new ideas and the day uses the child's own favorites or generic rewards; if
    Qloo refuses one type (400), only that type is skipped.
    """
    ratings = load_game_ratings() if game_ratings is None else game_ratings
    roster = list(roster) or [student.alias]
    profile = TasteProfile(student.id, [], {d: [] for d in DOMAINS})
    ids: List[str] = []
    for it in student.interests:
        try:
            name = validate_interest(it.name, roster)
            found = None if profile.degraded else _lookup(qloo, name, it.kind)
        except UnsafeText as e:
            profile.refused.append(e.reason)
            continue
        except QlooError as e:  # the child's own favorites do not need Qloo
            profile.degraded = "Qloo " + (str(e.status) if e.status else "network")
            found = None
        profile.signals.append(name)
        if found and found.get("id"):
            ids.append(found["id"])
        if it.kind in DOMAINS:
            item = dict(found or {}, name=name)
            if _gate(profile, student, it.kind, item, ratings, typed_by_adult=True):
                profile.own.append(Candidate(it.kind, name, item.get("id", "")))
    if not ids or profile.degraded:
        return profile
    try:
        own = {i.name.casefold() for i in student.interests}
        exclude = approvals.excluded_ids(student.id) if approvals else []
        allowed = content_ratings_for(student.age)
        for d in DOMAINS:
            try:
                items = qloo.insights(d, ids, take=take, content_ratings=allowed, exclude_ids=exclude)
            except UnsupportedType:
                continue
            except QlooError as e:
                if e.status != 400:
                    raise
                continue  # Qloo refused this one type (seen live 2026-10-08): keep the others
            for x in items:
                if x["name"].casefold() in own:
                    continue
                if not _gate(profile, student, d, x, ratings):
                    continue
                profile.by_domain[d].append(Candidate(d, x["name"], x.get("id", "")))
    except QlooError as e:
        profile.degraded = "Qloo " + (str(e.status) if e.status else "network")
    return profile


class Rotation:
    """Remembers which motivators each child already got, so they stay fresh."""

    def __init__(self) -> None:
        self.used: Dict[str, List[Tuple[str, str]]] = {}

    def pick(self, student_id: str, pool: List[Tuple[str, str]],
             avoid=()) -> Optional[Tuple[str, str]]:
        """The least recently used motivator in pool, never one in avoid (today's rewards so
        far); None if every motivator was already given today."""
        avoid = set(avoid)
        pool = [p for p in pool if p not in avoid]
        if not pool:
            return None
        used = self.used.setdefault(student_id, [])
        fresh = [p for p in pool if p not in used]
        if not fresh:
            used.clear()  # every motivator came once: a new round starts
            fresh = list(pool)
        used.append(fresh[0])
        return fresh[0]


@dataclass
class Step:
    block: Block
    activity: str
    first_then: str
    warning: str
    calm: List[str]
    reward_domain: str = ""
    reward_item: str = ""
    reward: str = ""
    reading: str = ""


def _show(lang: str, domain: str, name: str) -> str:
    """Titles go in quotation marks; artists' names do not."""
    return name if domain == "artist" else q(lang, name)


def _approved(approvals: Approvals, sid: str, c: Candidate) -> bool:
    return approvals.status(sid, c.domain, c.name) == APPROVED


def motivators(student: Student, profile: TasteProfile, approvals: Approvals) -> List[Candidate]:
    """What may reach the child: Qloo ideas an adult approved, then the child's own favorites
    (typed by an adult, already through the age gate). Nothing else."""
    out = [c for c in profile.pool() if _approved(approvals, student.id, c)]
    seen = {(c.domain, c.name.casefold()) for c in out}
    for c in profile.own:
        if (c.domain, c.name.casefold()) not in seen and \
                approvals.status(student.id, c.domain, c.name) != REJECTED:
            out.append(c)
            seen.add((c.domain, c.name.casefold()))
    return out


def plan_day(student: Student, blocks: List[Block], profile: TasteProfile, rotation: Rotation,
             approvals: Approvals, lang: str = DEFAULT_LANG) -> Dict:
    """One step per block: first/then cue, change warning, calm-down options, and a reward
    after classes. A Qloo idea comes at most once a day; the child's own favorites come back
    when the other approved ideas run out. Only ideas an adult approved, and the child's own
    favorites, reach the child."""
    ideas = profile.pool()
    pool = motivators(student, profile, approvals)
    pending = [c for c in ideas if approvals.status(student.id, c.domain, c.name) == PENDING]
    picks = [(c.domain, c.name) for c in pool]
    favorites = {(c.domain, c.name) for c in profile.own}
    artists = [c.name for c in pool if c.domain == "artist"]
    books = [c.name for c in pool if c.domain == "book"]
    steps: List[Step] = []
    today: List[Tuple[str, str]] = []
    book_i = 0
    for i, b in enumerate(blocks):
        activity = b.name(lang)
        dom = item = reward = ""
        if b.kind in WORK_KINDS:
            # A Qloo idea never comes twice in one day. The child's own favorites may: once
            # every approved motivator came, the rotation starts again with them.
            chosen = rotation.pick(student.id, picks, avoid=[p for p in today if p not in favorites])
            if chosen:
                today.append(chosen)
                dom, item = chosen
                reward = t(lang, "reward." + dom, x=_show(lang, dom, item))
            else:
                reward = t(lang, "reward.default")
            then = reward
        else:
            then = blocks[i + 1].name(lang) if i + 1 < len(blocks) else t(lang, "then_home")
        warning = t(lang, "warning", prev=blocks[i - 1].name(lang), next=activity) if i > 0 else ""
        calm = [t(lang, "calm.breathe"), t(lang, "calm.corner"), t(lang, "calm.ball")]
        if artists:
            calm.insert(0, t(lang, "calm.music", x=artists[i % len(artists)]))
        reading = ""
        if b.kind == "class" and b.topic_name(lang) and books:
            reading = t(lang, "reading", topic=b.topic_name(lang), book=q(lang, books[book_i % len(books)]))
            book_i += 1
        steps.append(Step(b, activity, t(lang, "first_then", activity=activity, then=then),
                          warning, calm, dom, item, reward, reading))
    return {"student": student, "lang": lang, "greeting": t(lang, "greeting." + student.persona),
            "steps": steps, "pending": pending}


@dataclass
class CheckIn:
    """How a step went, told by the child with a face; the support that helped, if any."""
    activity: str
    face: str
    support_used: str = ""

    def __post_init__(self) -> None:
        if self.face not in FACES:
            raise ValueError(f"face must be one of {FACES}")
        if self.support_used and self.support_used not in SUPPORTS:
            raise ValueError("support not in the catalog")


def demo_checkins(student: Student, plan: Dict, day: int) -> List[CheckIn]:
    """Deterministic invented check-ins for the demo."""
    out = []
    base = sum(map(ord, student.id))
    for i, st in enumerate(plan["steps"]):
        code = (base + i * 7 + day * 3) % 10
        face = "dificil" if code < 2 else ("mas_o_menos" if code < 5 else "bien")
        support = ""
        if face != "bien":
            fits = [s for s in student.supports if st.block.kind in SUPPORT_KINDS.get(s, BLOCK_KINDS)]
            if fits:
                support = fits[i % len(fits)]
        out.append(CheckIn(st.activity, face, support))
    return out


def _pairs(records: Dict, sid: str):
    return list(zip(records[sid]["plan"]["steps"], records[sid]["checkins"]))


def _pending_line(lang: str, key: str, records: Dict, sid: str, alias: str = "") -> str:
    plan = records[sid]["plan"]
    if plan.get("suggested"):  # the language model's picks, with its reason, for adults only
        items = "; ".join(
            f"{_show(lang, s['candidate'].domain, s['candidate'].name)} "
            f"({t(lang, 'domain.' + s['candidate'].domain)}): {s['reason'].rstrip('. ')}"
            for s in plan["suggested"])
        return t(lang, key.replace("pending.", "suggested."), items=items, alias=alias)
    ideas = plan["pending"][:3]
    if not ideas:
        return ""
    items = ", ".join(f"{_show(lang, c.domain, c.name)} ({t(lang, 'domain.' + c.domain)})" for c in ideas)
    return t(lang, key, items=items, alias=alias)


def _teacher(adult: Adult, school: School, records: Dict, sids: List[str], lang: str) -> List[str]:
    out = [t(lang, "teacher.title", name=pick(adult.name, lang))]
    for sid in sids:
        st = school.students[sid]
        pairs = _pairs(records, sid)
        good = [(s, c) for s, c in pairs if c.face == "bien"]
        hard = [(s, c) for s, c in pairs if c.face == "dificil"]
        out.append("")
        out.append(f"{st.alias} ({pick(st.grade, lang)})")
        if good:
            out.append(t(lang, "teacher.worked", items="; ".join(
                t(lang, "teacher.with", activity=c.activity, item=_show(lang, s.reward_domain, s.reward_item))
                if s.reward_item else c.activity for s, c in good[:3])))
        if hard:
            out.append(t(lang, "teacher.hard", items="; ".join(
                t(lang, "teacher.helped", activity=c.activity, support=support_text(c.support_used, lang))
                if c.support_used else c.activity for s, c in hard)))
            out.append(t(lang, "teacher.tomorrow", activity=hard[0][1].activity))
        reads = [s.reading for s, c in pairs if s.reading]
        if reads:
            out.append(t(lang, "teacher.readings", items=" ".join(reads)))
        line = _pending_line(lang, "pending.teacher", records, sid)
        if line:
            out.append(line)
    return out


def _principal(adult: Adult, school: School, records: Dict, sids: List[str], lang: str) -> List[str]:
    faces: Counter = Counter()
    supports: Counter = Counter()
    attention = []
    for sid in sids:
        cks = records[sid]["checkins"]
        faces.update(c.face for c in cks)
        supports.update(c.support_used for c in cks if c.support_used)
        if sum(c.face == "dificil" for c in cks) >= 2:
            attention.append(school.students[sid].alias)
    out = [t(lang, "principal.title", name=pick(adult.name, lang)),
           t(lang, "principal.children", n=len(sids)),
           t(lang, "principal.moments", total=sum(faces.values()),
             good=f"{FACE_ICON['bien']} {faces['bien']}",
             so=f"{FACE_ICON['mas_o_menos']} {faces['mas_o_menos']}",
             hard=f"{FACE_ICON['dificil']} {faces['dificil']}")]
    if supports:
        out.append(t(lang, "principal.supports", items="; ".join(
            f"{support_text(s, lang)} ({n})" for s, n in supports.most_common(3))))
    out.append(t(lang, "principal.watch",
                 names=", ".join(attention) if attention else t(lang, "principal.nobody")))
    return out


def _family(adult: Adult, school: School, records: Dict, sids: List[str], lang: str) -> List[str]:
    out: List[str] = []
    for sid in sids:
        st = school.students[sid]
        pairs = _pairs(records, sid)
        good = [c for s, c in pairs if c.face == "bien"]
        hard = [c for s, c in pairs if c.face == "dificil"]
        if out:
            out.append("")
        out.append(t(lang, "family.hello", alias=st.alias))
        out.append(t(lang, "family.count", alias=st.alias, good=len(good), n=len(pairs)))
        if good:
            out.append(t(lang, "family.best", activity=good[0].activity))
        if hard:
            extra = (t(lang, "family.helped", support=support_text(hard[0].support_used, lang))
                     if hard[0].support_used else "")
            out.append(t(lang, "family.hardest", activity=hard[0].activity) + extra)
        items = [s for s, c in pairs if s.reward_item and c.face == "bien"] or \
                [s for s, c in pairs if s.reward_item]
        if items:
            out.append(t(lang, "family.talk", item=_show(lang, items[0].reward_domain, items[0].reward_item)))
        line = _pending_line(lang, "pending.family", records, sid, st.alias)
        if line:
            out.append(line)
    return out


def _specialist(adult: Adult, school: School, records: Dict, sids: List[str], lang: str) -> List[str]:
    out = [t(lang, "specialist.title", name=pick(adult.name, lang))]
    for sid in sids:
        st = school.students[sid]
        agreed = ", ".join(support_text(s, lang) for s in st.supports) or "-"
        out.append("")
        out.append(t(lang, "specialist.child", alias=st.alias, grade=pick(st.grade, lang), supports=agreed))
        out.append(t(lang, "specialist.header"))
        for s, c in _pairs(records, sid):
            used = support_text(c.support_used, lang) if c.support_used else "-"
            out.append(f"{s.block.start}-{s.block.end} | {c.activity} | {t(lang, 'face.' + c.face)} | "
                       f"{used} | {s.reward_item or '-'}")
    return out


WRITERS = {"teacher": _teacher, "principal": _principal, "family": _family, "specialist": _specialist}


def report_for(adult: Adult, school: School, records: Dict, lang: str = DEFAULT_LANG) -> str:
    """The report for one adult, covering only the children linked to that adult."""
    sids = [sid for sid in adult.student_ids if sid in records]
    text = "\n".join(WRITERS[adult.role](adult, school, records, sids, lang))
    return scrub(text, lang)


def run_day(school: School, qloo, rotation: Rotation, approvals: Approvals, day: int = 0,
            profiles: Optional[Dict[str, TasteProfile]] = None, lang: str = DEFAULT_LANG,
            planner=None):
    """Plan and (invented) check-ins for every child; returns (records, profiles).
    planner has plan_day's signature; planner.llm_plan_day adds the language model."""
    planner = planner or plan_day
    if profiles is None:
        roster = school.roster()
        profiles = {sid: build_profile(st, qloo, roster, approvals)
                    for sid, st in school.students.items()}
    records = {}
    for sid, st in school.students.items():
        plan = planner(st, school.schedules[sid], profiles[sid], rotation, approvals, lang)
        records[sid] = {"plan": plan, "checkins": demo_checkins(st, plan, day)}
    return records, profiles


def simulate_family_approvals(school: School, records: Dict, approvals: Approvals,
                              lang: str = DEFAULT_LANG) -> List[str]:
    """Demo only: each family says yes to the first new idea waiting for them."""
    lines = []
    for adult in school.adults.values():
        if adult.role != "family":
            continue
        for sid in adult.student_ids:
            ideas = records[sid]["plan"]["pending"]
            if ideas:
                c = ideas[0]
                approvals.decide(adult, sid, c.domain, c.name, True, c.entity_id)
                lines.append(t(lang, "cli.approved", adult=pick(adult.name, lang), item=_show(lang, c.domain, c.name)))
    return lines
