"""Web views for the demo: the child's phone view and one view for each adult.

No request ever calls the language model or Qloo. The day's plans are built once per day and
language: the rule-based plan at once and then, if a Groq key is set, the agent's plan in the
background. Both are kept in memory and in a private cache file (cache/plans, git-ignored like
Qloo's answers), so judges opening the demo do not spend the free quota. Every visitor gets
their own copy of the approvals and of the child's check-ins, kept in memory for a few hours,
so judges do not change each other's demo. No scripts in the pages; standard library only.
"""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import html
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .agent import (FACE_ICON, FACES, Candidate, CheckIn, Rotation, Step, _show, build_profile,
                    calm_steps, demo_checkins, plan_day, report_for, run_day)
from .approvals import PENDING, Approvals
from .i18n import DEFAULT_LANG, LANGS, pick, t
from .llm import make_llm
from .models import APPROVER_ROLES, ROLES, School, exercise_text, load_school
from .planner import forbidden_names, llm_plan_day
from .qloo import make_client

# Argentina keeps UTC-3 all year; a fixed offset needs no time zone database on the host.
ART = _dt.timezone(_dt.timedelta(hours=-3))
SESSION_COOKIE = "hermano_session"
SESSION_TTL = 6 * 3600
MAX_SESSIONS = 2000
MAX_BODY = 4096
MAX_IDEAS_SHOWN = 3
REFRESH_WHILE_PLANNING = 20  # seconds
CSP = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
       "frame-ancestors 'none'")
Response = Tuple[int, List[Tuple[str, str]], bytes]

CSS = """
:root{--ink:#1f2933;--muted:#52606d;--bg:#f7f5f0;--card:#fff;--accent:#2f6f5e;--warn:#8a5a00}
*{box-sizing:border-box}
body{margin:0;font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{display:flex;gap:.75rem;align-items:center;padding:.75rem 1rem;background:var(--accent);color:#fff}
header a{color:#fff}
.brand{font-weight:700;font-size:1.2rem;text-decoration:none}
.badge{font-size:.8rem;background:rgba(255,255,255,.2);padding:.1rem .5rem;border-radius:1rem}
.lang{margin-left:auto}
main{max-width:46rem;margin:0 auto;padding:1rem}
.card,.step{background:var(--card);border-radius:1rem;padding:1rem;margin:.75rem 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.hello{font-size:1.5rem}
.time,.note{color:var(--muted);font-size:.9rem;margin:.25rem 0}
.ft{font-size:1.25rem;font-weight:600;margin:.25rem 0}
.warn{color:var(--warn);margin:.25rem 0}
.faces{margin-top:.5rem}
.faces button{font-size:1.1rem;border:2px solid #d9d4c7;background:#fff;border-radius:.75rem;padding:.3rem .6rem;margin:.2rem .3rem .2rem 0}
.faces button.on{border-color:var(--accent);background:#e6f2ee}
.flash{background:#e6f2ee;border-left:4px solid var(--accent);padding:.5rem .75rem;border-radius:.5rem}
.why{color:var(--muted)}
.yes{background:var(--accent);color:#fff;border:0;border-radius:.5rem;padding:.4rem 1rem;font-size:1rem}
.no{background:#fff;border:1px solid #b8b2a7;border-radius:.5rem;padding:.4rem 1rem;font-size:1rem}
.report p{margin:.25rem 0}
.ex{background:#eef3fb;border-radius:.75rem;padding:.5rem .75rem;margin:.5rem 0}
.did button{font-size:1rem;border:2px solid #d9d4c7;background:#fff;border-radius:.75rem;padding:.25rem .6rem;margin:.2rem .3rem .2rem 0}
.did button.picked{border-color:var(--accent);background:#e6f2ee}
.sos button{font-size:1rem;border:2px solid #9db4d6;background:#eef3fb;border-radius:.75rem;padding:.3rem .8rem;margin:.4rem 0}
.calmbox{background:#eef3fb;border-left:4px solid #5b7fb3;border-radius:.5rem;padding:.5rem .75rem;margin:.5rem 0}
ul.plain{list-style:none;padding:0}
footer{max-width:46rem;margin:2rem auto;padding:0 1rem;color:var(--muted);font-size:.9rem}
"""


def today_art() -> str:
    return _dt.datetime.now(ART).date().isoformat()


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _one(d: Dict[str, List[str]], key: str) -> str:
    v = d.get(key) or [""]
    return v[0]


# ---- the day's plans as JSON, for the private cache ----

def _cand(c: Candidate) -> dict:
    return {"domain": c.domain, "name": c.name, "entity_id": c.entity_id}


def _uncand(d: dict) -> Candidate:
    return Candidate(d["domain"], d["name"], d.get("entity_id", ""))


def dump_records(records: Dict) -> Dict:
    out = {}
    for sid, rec in records.items():
        p = rec["plan"]
        out[sid] = {
            "greeting": p["greeting"], "planner": p.get("planner", "rules"),
            "model": p.get("model", ""), "planner_note": p.get("planner_note", ""),
            "steps": [{"activity": s.activity, "first_then": s.first_then, "warning": s.warning,
                       "calm": list(s.calm), "reward_domain": s.reward_domain,
                       "reward_item": s.reward_item, "reward": s.reward, "reading": s.reading,
                       "exercise": s.exercise, "movement": s.movement, "reinforce": s.reinforce}
                      for s in p["steps"]],
            "pending": [_cand(c) for c in p["pending"]],
            "suggested": [{"candidate": _cand(x["candidate"]), "reason": x["reason"]}
                          for x in p.get("suggested", [])],
        }
    return out


def load_records(school: School, data: Dict, lang: str) -> Dict:
    if set(data) != set(school.students):
        raise ValueError("cached plans do not match the school")
    records = {}
    for sid, d in data.items():
        blocks = school.schedules[sid]
        if len(blocks) != len(d["steps"]):
            raise ValueError("cached plan does not match the schedule")
        steps = [Step(b, s["activity"], s["first_then"], s["warning"], list(s["calm"]),
                      s["reward_domain"], s["reward_item"], s["reward"], s["reading"],
                      s.get("movement", ""), s.get("reinforce", ""), s.get("exercise", ""))
                 for b, s in zip(blocks, d["steps"])]
        plan = {"student": school.students[sid], "lang": lang, "greeting": d["greeting"],
                "steps": steps, "pending": [_uncand(c) for c in d["pending"]],
                "suggested": [{"candidate": _uncand(x["candidate"]), "reason": x["reason"]}
                              for x in d["suggested"]],
                "planner": d["planner"], "model": d["model"]}
        if d.get("planner_note"):
            plan["planner_note"] = d["planner_note"]
        records[sid] = {"plan": plan}
    return records


class Session:
    """One visitor's own demo: their approvals, the child's check-ins and a one-time note."""

    def __init__(self, approvals_raw: dict, now: float) -> None:
        self.reset(approvals_raw)
        self.seen = now

    def reset(self, approvals_raw: dict) -> None:
        self.approvals = Approvals(json.loads(json.dumps(approvals_raw)))
        self.faces: Dict[str, Dict[int, str]] = {}
        self.did: Dict[str, Dict[int, bool]] = {}  # the child's own answer about each exercise
        self.nervous: Dict[str, set] = {}  # steps where the child pressed "I feel nervous"
        self.flash = ""


class App:
    """Answers every request from the day's cached plans. make_app() builds one."""

    def __init__(self, school: School, approvals_raw: dict, make_qloo: Callable,
                 planners: Sequence[Tuple[str, Callable]], cache_dir, tag: str = "",
                 background: bool = True, today: Callable[[], str] = today_art,
                 clock: Callable[[], float] = time.time, secure_cookie: bool = False) -> None:
        if not planners:
            raise ValueError("at least one planner")
        self.school = school
        self.approvals_raw = approvals_raw
        self.make_qloo = make_qloo
        self.planners = list(planners)
        self.cache_dir = Path(cache_dir)
        self.tag = tag
        self.background = background
        self.today = today
        self.clock = clock
        self.secure_cookie = secure_cookie
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()  # quick rule-based builds
        self.agent_lock = threading.Lock()  # slow agent builds, one at a time, never blocking the above
        self.qloo_lock = threading.Lock()
        self.ready: Dict[str, dict] = {}
        self.failed: set = set()
        self.builds: Dict[str, int] = {name: 0 for name, _ in self.planners}
        self.errors: List[str] = []
        self.sessions: "OrderedDict[str, Session]" = OrderedDict()
        self._profiles = None
        self._profiles_date = ""
        self._sent: List[str] = []
        self._mode = ""

    @property
    def has_llm(self) -> bool:
        return len(self.planners) > 1

    def _note(self, what: str) -> None:
        self.errors = (self.errors + [what])[-20:]

    # ---- plans: built once per day and language, then only read ----

    def _path(self, date: str, lang: str) -> Path:
        return self.cache_dir / f"plans-{date}-{lang}-{self.tag}.json"

    def _read(self, date: str, lang: str) -> Optional[dict]:
        p = self._path(date, lang)
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if raw.get("tag") != self.tag or raw.get("date") != date or raw.get("lang") != lang:
                return None
            return {"date": date, "lang": lang, "planner": raw["planner"], "mode": raw["mode"],
                    "sent": list(raw["sent"]), "records": load_records(self.school, raw["plans"], lang)}
        except (ValueError, KeyError, TypeError):
            return None

    def _write(self, day: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        raw = {"tag": self.tag, "date": day["date"], "lang": day["lang"], "planner": day["planner"],
               "mode": day["mode"], "sent": day["sent"], "plans": dump_records(day["records"])}
        p = self._path(day["date"], day["lang"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def _base_approvals(self) -> Approvals:
        return Approvals(json.loads(json.dumps(self.approvals_raw)))

    def _profiles_for(self, date: str):
        """The children's Qloo profiles for `date`: asked once per day, shared by every plan."""
        with self.qloo_lock:
            if self._profiles_date != date:
                qloo = self.make_qloo()
                roster = self.school.roster()
                approvals = self._base_approvals()
                self._profiles = {sid: build_profile(st, qloo, roster, approvals)
                                  for sid, st in self.school.students.items()}
                self._profiles_date = date
                self._sent = list(getattr(qloo, "sent", []))
                self._mode = getattr(qloo, "mode", "")
            return self._profiles, list(self._sent), self._mode

    def _build(self, date: str, lang: str, name: str, planner: Callable) -> dict:
        profiles, sent, mode = self._profiles_for(date)
        records, _ = run_day(self.school, None, Rotation(), self._base_approvals(), 0, profiles, lang,
                             planner)
        with self.lock:
            self.builds[name] += 1
        return {"date": date, "lang": lang, "planner": name, "mode": mode, "sent": sent,
                "records": records}

    def plans(self, lang: str) -> dict:
        """Today's plans in `lang`: from memory, else from the cache file, else built now."""
        date = self.today()
        with self.lock:
            cur = self.ready.get(lang)
        if cur is not None and cur["date"] == date:
            return cur
        later = False
        with self.build_lock:
            with self.lock:
                cur = self.ready.get(lang)
            if cur is not None and cur["date"] == date:
                return cur
            day = self._read(date, lang)
            if day is None:
                name, planner = self.planners[0]
                day = self._build(date, lang, name, planner)
                later = self.has_llm
                if not later:
                    self._write(day)
            with self.lock:
                self.ready[lang] = day
        if later:
            self._later(date, lang)
        return day

    def _later(self, date: str, lang: str) -> None:
        """The agent's plan, built after the rule-based one; it replaces it when ready."""
        def work() -> None:
            try:
                with self.agent_lock:
                    for name, planner in self.planners[1:]:
                        day = self._build(date, lang, name, planner)
                        self._write(day)
                        with self.lock:
                            if self.ready.get(lang, {}).get("date") in (None, date):
                                self.ready[lang] = day
            except Exception as e:  # the rule-based plan stays
                with self.lock:
                    self.failed.add((date, lang))
                    self._note(type(e).__name__)
        if self.background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    # ---- visitors ----

    def _session(self, cookie_header: str) -> Tuple[Session, str]:
        token = ""
        try:
            jar = SimpleCookie(cookie_header or "")
            if SESSION_COOKIE in jar:
                token = jar[SESSION_COOKIE].value
        except CookieError:
            token = ""
        now = self.clock()
        with self.lock:
            s = self.sessions.get(token) if token else None
            if s is not None and now - s.seen <= SESSION_TTL:
                s.seen = now
                self.sessions.move_to_end(token)
                return s, ""
            if s is not None:
                del self.sessions[token]
            token = secrets.token_urlsafe(18)
            s = Session(self.approvals_raw, now)
            self.sessions[token] = s
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.popitem(last=False)
            return s, token

    def _records_for(self, day: dict, session: Session) -> Dict:
        """The day's records as this visitor sees them: their decisions and the child's faces."""
        out = {}
        for sid, rec in day["records"].items():
            def waiting(c: Candidate, sid: str = sid) -> bool:
                return session.approvals.status(sid, c.domain, c.name) == PENDING
            plan = dict(rec["plan"])
            plan["pending"] = [c for c in plan["pending"] if waiting(c)]
            plan["suggested"] = [x for x in plan.get("suggested", []) if waiting(x["candidate"])]
            told = session.faces.get(sid, {})
            invented = demo_checkins(self.school.students[sid], rec["plan"], 0)
            checkins = [CheckIn(c.activity, told[i], exercise_done=c.exercise_done) if i in told else c
                        for i, c in enumerate(invented)]
            did = session.did.get(sid, {})
            checkins = [replace(c, exercise_done=did[i]) if i in did else c for i, c in enumerate(checkins)]
            calm = session.nervous.get(sid, set())
            checkins = [replace(c, nervous=True) if i in calm else c for i, c in enumerate(checkins)]
            out[sid] = {"plan": plan, "checkins": checkins, "told": len(told)}
        return out

    # ---- requests ----

    def handle(self, method: str, path: str, query: Dict[str, List[str]],
               form: Dict[str, List[str]], cookie: str = "") -> Response:
        lang = _one(form, "lang") or _one(query, "lang")
        lang = lang if lang in LANGS else DEFAULT_LANG
        if method == "GET" and path.rstrip("/") == "/healthz":  # monitors get no session
            return self._health()
        session, new_token = self._session(cookie)
        try:
            status, headers, body = self._route(method, path, lang, form, session)
        except Exception as e:  # a visitor never sees a traceback
            self._note(type(e).__name__)
            status, headers, body = self._message(500, lang, "web.bad_request", "/")
        headers = list(headers) + [
            ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")]
        if new_token:
            cookie_out = (f"{SESSION_COOKIE}={new_token}; Path=/; HttpOnly; SameSite=Lax; "
                          f"Max-Age={SESSION_TTL}")
            if self.secure_cookie:
                cookie_out += "; Secure"
            headers.append(("Set-Cookie", cookie_out))
        return status, headers, body

    def _route(self, method: str, path: str, lang: str, form, session: Session) -> Response:
        segs = tuple(s for s in path.split("/") if s)
        simple = {(): ("GET", lambda: self._home(lang)),
                  ("healthz",): ("GET", self._health),
                  ("privacy",): ("GET", lambda: self._privacy(lang)),
                  ("reset",): ("POST", lambda: self._reset(lang, session))}
        if segs in simple:
            want, fn = simple[segs]
            return fn() if method == want else self._message(405, lang, "web.bad_request", "/")
        if len(segs) in (2, 3) and segs[0] in ("child", "adult"):
            kind, ident = segs[0], segs[1]
            action = segs[2] if len(segs) == 3 else ""
            if action not in ({"": 0, "checkin": 0} if kind == "child" else {"": 0, "decide": 0}):
                return self._message(404, lang, "web.not_found", "/")
            known = self.school.students if kind == "child" else self.school.adults
            if ident not in known:
                return self._message(404, lang, "web.not_found", "/")
            if method != ("POST" if action else "GET"):
                return self._message(405, lang, "web.bad_request", "/")
            if kind == "child":
                if action:
                    return self._checkin(lang, session, ident, form)
                return self._child(lang, session, ident)
            if action:
                return self._decide(lang, session, ident, form)
            return self._adult(lang, session, ident)
        return self._message(404, lang, "web.not_found", "/")

    # ---- pages ----

    def _page(self, lang: str, title: str, body: str, path: str, refresh: int = 0) -> bytes:
        other = "es" if lang == "en" else "en"
        again = f"<meta http-equiv='refresh' content='{int(refresh)}'>" if refresh else ""
        reset = (f"<form method='post' action='/reset'><input type='hidden' name='lang' value='{lang}'>"
                 f"<button class='no' type='submit'>{esc(t(lang, 'web.reset'))}</button></form>")
        return ("<!doctype html>"
                f"<html lang='{lang}'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"{again}<title>{esc(title)}</title><style>{CSS}</style></head><body>"
                f"<header><a class='brand' href='/?lang={lang}'>Hermano</a>"
                f"<span class='badge'>{esc(t(lang, 'web.invented_short'))}</span>"
                f"<a class='lang' href='{esc(path)}?lang={other}'>{esc(t(lang, 'web.other_lang'))}</a>"
                f"</header><main>{body}</main><footer>"
                f"<p><a href='/?lang={lang}'>{esc(t(lang, 'web.home'))}</a> \u00b7 "
                f"<a href='/privacy?lang={lang}'>{esc(t(lang, 'web.privacy_link'))}</a></p>"
                f"<p>{esc(t(lang, 'web.invented'))}</p>{reset}</footer></body></html>").encode("utf-8")

    def _html(self, status: int, lang: str, title: str, body: str, path: str,
              refresh: int = 0) -> Response:
        return (status, [("Content-Type", "text/html; charset=utf-8")],
                self._page(lang, title, body, path, refresh))

    def _message(self, status: int, lang: str, key: str, path: str) -> Response:
        return self._html(status, lang, "Hermano", f"<p>{esc(t(lang, key))}</p>", path)

    @staticmethod
    def _redirect(location: str) -> Response:
        return 303, [("Location", location)], b""

    @staticmethod
    def _flash(session: Session) -> str:
        msg, session.flash = session.flash, ""
        return f"<p class='flash' role='status'>{esc(msg)}</p>" if msg else ""

    def _building(self, day: dict) -> bool:
        return day["planner"] == "rules" and self.has_llm and (day["date"], day["lang"]) not in self.failed

    def _planner_line(self, day: dict, lang: str) -> str:
        plans = [r["plan"] for r in day["records"].values()]
        if self._building(day):
            return t(lang, "web.building")
        if plans and all(p.get("planner") == "llm" for p in plans):
            return t(lang, "cli.planner.llm", model=plans[0].get("model", ""))
        notes = [p["planner_note"] for p in plans if p.get("planner_note")]
        if notes:
            return t(lang, "cli.planner.fallback", reason=notes[0])
        return t(lang, "cli.planner.rules")

    def _home(self, lang: str) -> Response:
        day = self.plans(lang)
        parts = [f"<h1>Hermano</h1><p>{esc(t(lang, 'web.tagline'))}</p>",
                 f"<p class='note'>{esc(self._planner_line(day, lang))}</p>",
                 f"<h2>{esc(t(lang, 'web.children'))}</h2><ul class='plain'>"]
        for sid, st in self.school.students.items():
            parts.append(f"<li class='card'><a href='/child/{esc(sid)}?lang={lang}'><strong>{esc(st.alias)}"
                         f"</strong></a> \u00b7 {esc(t(lang, 'web.age', age=st.age))} \u00b7 "
                         f"{esc(pick(st.grade, lang))} \u00b7 {esc(st.persona)}</li>")
        parts.append(f"</ul><h2>{esc(t(lang, 'web.adults'))}</h2><ul class='plain'>")
        for role in ROLES:
            for aid, a in self.school.adults.items():
                if a.role == role:
                    parts.append(f"<li class='card'><a href='/adult/{esc(aid)}?lang={lang}'>"
                                 f"{esc(pick(a.name, lang))}</a> \u00b7 {esc(t(lang, 'web.role.' + role))}</li>")
        parts.append(f"</ul><p class='note'>{esc(t(lang, 'web.demo_login'))}</p>")
        # After a cold start the agent needs a minute or two: the home page reloads itself.
        return self._html(200, lang, "Hermano", "".join(parts), "/",
                          REFRESH_WHILE_PLANNING if self._building(day) else 0)

    def _child(self, lang: str, session: Session, sid: str) -> Response:
        """The child's own view. Only titles an adult approved; no suggestions, no reasons."""
        plan = self.plans(lang)["records"][sid]["plan"]
        told = session.faces.get(sid, {})
        did = session.did.get(sid, {})
        nervous = session.nervous.get(sid, set())
        parts = [self._flash(session), f"<h1 class='hello'>{esc(plan['greeting'])}</h1>",
                 f"<p class='note'>{esc(t(lang, 'web.only_approved'))}</p>"]
        for i, s in enumerate(plan["steps"]):
            b = s.block
            buttons = "".join(
                f"<button type='submit' name='face' value='{f}' class='{'on' if told.get(i) == f else ''}' "
                f"aria-pressed='{'true' if told.get(i) == f else 'false'}'>{FACE_ICON[f]} "
                f"{esc(t(lang, 'face.' + f))}</button>" for f in FACES)
            warn = (f"<p class='warn'>\u23f0 {esc(t(lang, 'cli.warning'))}: {esc(s.warning)}</p>"
                    if s.warning else "")
            calm = "".join(f"<li>{esc(c)}</li>" for c in s.calm)
            move = f"<p class='move'>\U0001F57A {esc(s.movement)}</p>" if s.movement else ""
            ex = ""
            if s.exercise:
                picks = "".join(
                    f"<button type='submit' name='did' value='{v}' class='{'picked' if did.get(i) is (v == 'yes') else ''}' "
                    f"aria-pressed='{'true' if did.get(i) is (v == 'yes') else 'false'}'>{esc(t(lang, 'web.did_' + v))}</button>"
                    for v in ("yes", "no"))
                ex = (f"<div class='ex'><p>\U0001F9E9 {esc(t(lang, 'exercise.child', x=exercise_text(s.exercise, lang)))}</p>"
                      f"<form class='did' method='post' action='/child/{esc(sid)}/checkin'>"
                      f"<input type='hidden' name='lang' value='{lang}'><input type='hidden' name='step' value='{i}'>"
                      f"<span>{esc(t(lang, 'web.did'))}</span> {picks}</form></div>")
            box = ""
            if i in nervous:
                items = "".join(f"<li>{esc(x)}</li>" for x in calm_steps(plan["student"], s, lang))
                box = (f"<div class='calmbox' id='c{i}' role='status'><p><strong>{esc(t(lang, 'nervous.title'))}</strong></p>"
                       f"<ol>{items}</ol></div>")
            sos = (f"{box}<form class='sos' method='post' action='/child/{esc(sid)}/checkin'>"
                   f"<input type='hidden' name='lang' value='{lang}'><input type='hidden' name='step' value='{i}'>"
                   f"<button type='submit' name='nervous' value='1'>\U0001F499 {esc(t(lang, 'nervous.button'))}</button></form>")
            parts.append(
                f"<section class='step kind-{esc(b.kind)}' id='s{i}'><p class='time'>{esc(b.start)}-{esc(b.end)}</p>"
                f"<p class='ft'>{esc(s.first_then)}</p>{warn}{move}{ex}"
                f"<details><summary>{esc(t(lang, 'web.calm'))}</summary><ul>{calm}</ul></details>{sos}"
                f"<form class='faces' method='post' action='/child/{esc(sid)}/checkin'>"
                f"<input type='hidden' name='lang' value='{lang}'><input type='hidden' name='step' value='{i}'>"
                f"<span>{esc(t(lang, 'web.how'))}</span> {buttons}</form></section>")
        alias = self.school.students[sid].alias
        return self._html(200, lang, f"Hermano \u00b7 {alias}", "".join(parts), f"/child/{sid}")

    def _checkin(self, lang: str, session: Session, sid: str, form) -> Response:
        steps = self.plans(lang)["records"][sid]["plan"]["steps"]
        raw, face, did = _one(form, "step"), _one(form, "face"), _one(form, "did")
        if not (raw.isascii() and raw.isdigit() and len(raw) < 4) or int(raw) >= len(steps):
            return self._message(400, lang, "web.bad_request", f"/child/{sid}")
        i = int(raw)
        nervous = _one(form, "nervous")
        if nervous:  # the child pressed "I feel nervous" on this step
            if nervous != "1":
                return self._message(400, lang, "web.bad_request", f"/child/{sid}")
            with self.lock:
                session.nervous.setdefault(sid, set()).add(i)
                session.flash = t(lang, "nervous.flash")
            return self._redirect(f"/child/{sid}?lang={lang}#c{i}")
        if did:  # the child's answer about the exercise of this step
            if did not in ("yes", "no") or not steps[i].exercise:
                return self._message(400, lang, "web.bad_request", f"/child/{sid}")
            with self.lock:
                session.did.setdefault(sid, {})[i] = did == "yes"
                session.flash = t(lang, "web.saved", face=t(lang, "web.did_" + did))
            return self._redirect(f"/child/{sid}?lang={lang}#s{i}")
        if face not in FACES:
            return self._message(400, lang, "web.bad_request", f"/child/{sid}")
        with self.lock:
            session.faces.setdefault(sid, {})[i] = face
            session.flash = t(lang, "web.saved", face=f"{FACE_ICON[face]} {t(lang, 'face.' + face)}")
        return self._redirect(f"/child/{sid}?lang={lang}#s{i}")

    @staticmethod
    def _ideas(plan: dict) -> List[Tuple[Candidate, str]]:
        """The agent's suggestions with their reasons first, then other new ideas, up to 3."""
        out = [(x["candidate"], x["reason"]) for x in plan.get("suggested", [])]
        seen = {(c.domain, c.name.casefold()) for c, _ in out}
        for c in plan["pending"]:
            if len(out) >= MAX_IDEAS_SHOWN:
                break
            if (c.domain, c.name.casefold()) not in seen:
                out.append((c, ""))
                seen.add((c.domain, c.name.casefold()))
        return out

    def _adult(self, lang: str, session: Session, aid: str) -> Response:
        """One adult's page: only their children; the family and the teacher can decide."""
        adult = self.school.adults[aid]
        day = self.plans(lang)
        records = self._records_for(day, session)
        sids = [sid for sid in adult.student_ids if sid in records]
        names = ", ".join(self.school.students[s].alias for s in sids)
        parts = [self._flash(session), f"<h1>{esc(pick(adult.name, lang))}</h1>",
                 f"<p class='note'>{esc(t(lang, 'web.role.' + adult.role))} \u00b7 "
                 f"{esc(t(lang, 'web.your_children', names=names))}</p>",
                 f"<p class='note'>{esc(self._planner_line(day, lang))}</p>"]
        if adult.role in APPROVER_ROLES:
            parts.append(f"<h2>{esc(t(lang, 'web.pending'))}</h2>")
            shown = False
            for sid in sids:
                ideas = self._ideas(records[sid]["plan"])
                if not ideas:
                    continue
                shown = True
                alias = self.school.students[sid].alias
                parts.append(f"<h3>{esc(t(lang, 'web.for_child', alias=alias))}</h3>")
                for c, reason in ideas:
                    why = f"<p class='why'>{esc(t(lang, 'web.why', reason=reason))}</p>" if reason else ""
                    hidden = (f"<input type='hidden' name='lang' value='{lang}'>"
                              f"<input type='hidden' name='sid' value='{esc(sid)}'>"
                              f"<input type='hidden' name='domain' value='{esc(c.domain)}'>"
                              f"<input type='hidden' name='name' value='{esc(c.name)}'>")
                    parts.append(
                        f"<div class='card'><p><strong>{esc(_show(lang, c.domain, c.name))}</strong> "
                        f"({esc(t(lang, 'domain.' + c.domain))})</p>{why}"
                        f"<form method='post' action='/adult/{esc(aid)}/decide'>{hidden}"
                        f"<button class='yes' name='ok' value='1' type='submit'>{esc(t(lang, 'web.yes'))}</button> "
                        f"<button class='no' name='ok' value='0' type='submit'>{esc(t(lang, 'web.no'))}</button>"
                        f"</form></div>")
            if not shown:
                parts.append(f"<p>{esc(t(lang, 'web.none'))}</p>")
        report = report_for(adult, self.school, records, lang)
        lines = "".join(f"<p>{esc(line)}</p>" if line.strip() else "<hr>" for line in report.split("\n"))
        told = " ".join(t(lang, "web.checkins", alias=self.school.students[s].alias,
                          told=records[s]["told"], n=len(records[s]["checkins"])) for s in sids)
        parts.append(f"<h2>{esc(t(lang, 'web.report'))}</h2><div class='card report'>{lines}</div>"
                     f"<p class='note'>{esc(told)}</p>")
        return self._html(200, lang, f"Hermano \u00b7 {pick(adult.name, lang)}", "".join(parts),
                          f"/adult/{aid}")

    def _decide(self, lang: str, session: Session, aid: str, form) -> Response:
        adult = self.school.adults[aid]
        sid, domain, name, ok = (_one(form, k) for k in ("sid", "domain", "name", "ok"))
        back = f"/adult/{aid}"
        if adult.role not in APPROVER_ROLES or sid not in adult.student_ids:
            return self._message(403, lang, "web.forbidden", back)
        if ok not in ("0", "1"):
            return self._message(400, lang, "web.bad_request", back)
        records = self._records_for(self.plans(lang), session)
        match = [c for c in records[sid]["plan"]["pending"]
                 if c.domain == domain and c.name.casefold() == name.casefold()]
        if not match:
            return self._message(400, lang, "web.bad_request", back)
        c = match[0]
        try:
            with self.lock:
                session.approvals.decide(adult, sid, c.domain, c.name, ok == "1", c.entity_id)
                session.flash = t(lang, "web.approved" if ok == "1" else "web.rejected",
                                  item=_show(lang, c.domain, c.name))
        except PermissionError:
            return self._message(403, lang, "web.forbidden", back)
        return self._redirect(f"{back}?lang={lang}")

    def _privacy(self, lang: str) -> Response:
        day = self.plans(lang)
        items = "".join(f"<li><code>{esc(line)}</code></li>" for line in day["sent"])
        body = (f"<h1>{esc(t(lang, 'web.privacy'))}</h1>"
                f"<p class='note'>{esc(t(lang, 'web.privacy_note'))}</p>"
                f"<p>{esc(t(lang, 'cli.privacy', mode=day['mode'], n=len(day['sent'])))}</p><ul>{items}</ul>")
        return self._html(200, lang, "Hermano", body, "/privacy")

    def _reset(self, lang: str, session: Session) -> Response:
        with self.lock:
            session.reset(self.approvals_raw)
        return self._redirect(f"/?lang={lang}")

    def _health(self) -> Response:
        with self.lock:
            body = {"ok": True, "version": __version__, "today": self.today(),
                    "ready": {lang: [d["date"], d["planner"]] for lang, d in self.ready.items()},
                    "builds": dict(self.builds), "errors": len(self.errors),
                    "sessions": len(self.sessions)}
        return 200, [("Content-Type", "application/json")], json.dumps(body).encode("utf-8")


def make_app(data_dir, planner: str = "auto", cache_dir=None, background: bool = True) -> App:
    """The demo app: rules always; the agent too when GROQ_API_KEY is set (unless planner=rules)."""
    data = Path(data_dir)
    school_bytes = (data / "demo_school.json").read_bytes()
    approvals_bytes = (data / "demo_approvals.json").read_bytes()
    school = load_school(data / "demo_school.json")
    approvals_raw = json.loads(approvals_bytes.decode("utf-8"))
    llm = None if planner == "rules" else make_llm(forbidden=forbidden_names(school))
    planners: List[Tuple[str, Callable]] = [("rules", plan_day)]
    if llm is not None:
        planners.append(("llm", functools.partial(llm_plan_day, llm=llm)))
    mode = "qloo" if os.environ.get("QLOO_API_KEY") else "mock"
    tag = hashlib.sha256(school_bytes + approvals_bytes + "|".join(
        [__version__, mode] + [n for n, _ in planners]).encode("utf-8")).hexdigest()[:12]
    directory = cache_dir or os.environ.get("HERMANO_PLAN_DIR") or "cache/plans"
    return App(school, approvals_raw, functools.partial(make_client, str(data)), planners,
               directory, tag, background,
               secure_cookie=os.environ.get("HERMANO_HTTPS") == "1")


def make_server(app: App, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "hermano/" + __version__
        sys_version = ""

        def _serve(self, method: str) -> None:
            parts = urlsplit(self.path)
            form: Dict[str, List[str]] = {}
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    self._send(413, [("Content-Type", "text/plain; charset=utf-8")], b"too large")
                    return
                form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            status, headers, body = app.handle(method, parts.path, parse_qs(parts.query), form,
                                               self.headers.get("Cookie", ""))
            self._send(status, headers, body)

        def _send(self, status: int, headers, body: bytes) -> None:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def log_message(self, fmt, *args) -> None:  # no access log: no visitor addresses kept
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(app: App, host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = make_server(app, host, port)
    for lang in LANGS:  # warm up, so the first judge never waits
        app.plans(lang)
    print(f"Hermano on http://{host}:{httpd.server_address[1]}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
