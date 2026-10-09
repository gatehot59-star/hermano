"""Agent planner: the language model chooses, the code decides what is allowed.

The model works only with tools and the placeholder STUDENT_1. It can do two things:
1. choose which adult-approved motivator follows each class or special block;
2. suggest up to two new ideas from Qloo's list for an adult to approve, with one sentence why.
Every choice is checked in code: a reward after recess, an idea nobody approved or a reason with
a health word is refused, and the model is told why. The child only ever sees titles an adult
approved; the model's own words reach adults' reports only. If the model is down or breaks a
privacy rule, the rule-based plan stands.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .agent import WORK_KINDS, Candidate, Rotation, TasteProfile, _show, motivators, plan_day
from .approvals import PENDING, Approvals
from .guard import Pseudonymizer, UnsafeText, contains_contact, contains_health
from .i18n import DEFAULT_LANG, LANGS, pick, t
from .llm import LLMUnavailable, run_tools
from .models import Block, School, Student

MAX_SUGGESTIONS = 2
MAX_CANDIDATES = 12
MAX_REASON = 160
LANG_NAME = {"en": "English", "es": "Spanish"}

SYSTEM = ("You are Hermano's planner for one school day of one child, called STUDENT_1. "
          "Never use or invent a name. First call get_day and get_motivators. Then call "
          "submit_plan once with: one approved motivator (an M id) for each class or special "
          "block, changing domain from one block to the next when you can and avoiding the "
          "recently used ones; never a reward after recess, arrival or dismissal; and at most "
          "{n} new ideas from the candidates (C ids), each with one short sentence in {language} "
          "about why it fits the child's tastes. If submit_plan lists problems, fix them and "
          "submit again. When it is accepted, answer with the word done.")


def forbidden_names(school: School) -> List[str]:
    """Every child's alias and every adult's display name, in every language."""
    names = list(school.roster())
    for adult in school.adults.values():
        names.extend(pick(adult.name, lang) for lang in LANGS)
    return sorted({n for n in names if n and n.strip()})


def _schema(description: str, properties: Optional[dict] = None, required=()) -> dict:
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = list(required)
    return {"description": description, "parameters": params}


def llm_plan_day(student: Student, blocks: List[Block], profile: TasteProfile,
                 rotation: Rotation, approvals: Approvals, lang: str = DEFAULT_LANG,
                 llm=None, max_steps: int = 6) -> Dict:
    """Same output as agent.plan_day, plus plan["planner"] and plan["suggested"]."""
    recent = list(rotation.used.get(student.id, []))
    plan = plan_day(student, blocks, profile, rotation, approvals, lang)
    plan["planner"] = "rules"
    plan["suggested"] = []
    if llm is None:
        return plan

    pseudo = Pseudonymizer([student.alias])
    ideas = profile.pool()
    approved = motivators(student, profile, approvals)
    candidates = [c for c in ideas if approvals.status(student.id, c.domain, c.name) == PENDING]
    m_ids = {f"M{i}": c for i, c in enumerate(approved, 1)}
    c_ids = {f"C{i}": c for i, c in enumerate(candidates[:MAX_CANDIDATES], 1)}
    b_ids = {f"B{i}": i - 1 for i in range(1, len(blocks) + 1)}
    used = {(d, n) for d, n in recent}
    assigned: Dict[int, Candidate] = {}
    suggested: List[dict] = []

    def get_day():
        out = []
        for bid, i in b_ids.items():
            b = blocks[i]
            item = {"id": bid, "kind": b.kind, "name": b.name(lang)}
            if b.topic_name(lang):
                item["topic"] = b.topic_name(lang)
            out.append(item)
        return {"student": "STUDENT_1", "age": student.age, "blocks": out}

    def get_motivators():
        return {"approved": [{"id": k, "domain": c.domain, "title": c.name,
                              "recently_used": (c.domain, c.name) in used}
                             for k, c in m_ids.items()],
                "candidates": [{"id": k, "domain": c.domain, "title": c.name}
                               for k, c in c_ids.items()]}

    def submit_plan(rewards=(), suggestions=()):
        problems: List[str] = []
        new_assigned: Dict[int, Candidate] = {}
        for r in rewards or []:
            bid, mid = str(r.get("block_id", "")), str(r.get("motivator_id", ""))
            i = b_ids.get(bid)
            if i is None:
                problems.append(f"{bid}: unknown block")
            elif blocks[i].kind not in WORK_KINDS:
                problems.append(f"{bid}: rewards only follow class or special blocks")
            elif mid not in m_ids:
                problems.append(f"{bid}: {mid} is not an approved motivator")
            else:
                new_assigned[i] = m_ids[mid]
        new_suggested: List[dict] = []
        for s in suggestions or []:
            cid = str(s.get("candidate_id", ""))
            reason = " ".join(str(s.get("reason", "")).split())[:MAX_REASON]
            if cid not in c_ids:
                problems.append(f"{cid}: unknown candidate")
            elif len(new_suggested) >= MAX_SUGGESTIONS:
                problems.append(f"at most {MAX_SUGGESTIONS} suggestions")
                break
            elif not reason or contains_health(reason) or contains_contact(reason):
                problems.append(f"{cid}: the reason must be one sentence about tastes")
            elif all(x["candidate"] is not c_ids[cid] for x in new_suggested):
                new_suggested.append({"candidate": c_ids[cid], "reason": reason})
        assigned.clear()
        assigned.update(new_assigned)
        suggested[:] = new_suggested
        return {"accepted_rewards": len(new_assigned), "accepted_suggestions": len(new_suggested),
                "problems": problems}

    tools = {
        "get_day": (_schema("Blocks of today's school day, in order, with ids."), get_day),
        "get_motivators": (_schema("Approved motivators (M ids) and new candidate ideas from "
                                   "Qloo (C ids) for STUDENT_1."), get_motivators),
        "submit_plan": (_schema("Submit the whole plan. Replaces any earlier submission.", {
            "rewards": {"type": "array", "items": {"type": "object", "properties": {
                "block_id": {"type": "string"}, "motivator_id": {"type": "string"}},
                "required": ["block_id", "motivator_id"]}},
            "suggestions": {"type": "array", "items": {"type": "object", "properties": {
                "candidate_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["candidate_id", "reason"]}}}, ("rewards",)), submit_plan),
    }
    messages = [{"role": "system", "content": SYSTEM.format(
                    n=MAX_SUGGESTIONS, language=LANG_NAME.get(lang, "English"))},
                {"role": "user", "content": "Plan today for STUDENT_1."}]
    try:
        out = run_tools(llm, messages, tools, max_steps=max_steps)
    except (LLMUnavailable, UnsafeText) as e:
        plan["planner_note"] = type(e).__name__
        return plan
    if not assigned and not suggested:
        plan["planner_note"] = "no valid plan"
        return plan

    for i, c in assigned.items():
        st = plan["steps"][i]
        st.reward_domain, st.reward_item = c.domain, c.name
        st.reward = t(lang, "reward." + c.domain, x=_show(lang, c.domain, c.name))
        st.first_then = t(lang, "first_then", activity=st.activity, then=st.reward)
    # Rotation remembers what the child actually got today.
    rotation.used[student.id] = recent + [(s.reward_domain, s.reward_item)
                                          for s in plan["steps"] if s.reward_item]
    plan["suggested"] = [{"candidate": s["candidate"], "reason": pseudo.unmask(s["reason"])}
                         for s in suggested]
    plan["planner"] = "llm"
    plan["model"] = out.get("model") or ""
    return plan
