"""Command line demo.

    hermano demo [--days N] [--lang en|es] [--planner auto|rules|llm]
                                             plans, child view and the report for every adult
    hermano privacy [--days N]               everything that was sent to Qloo
    hermano serve [--host H] [--port P]      the web views: the child's phone view and each adult's

With more than one day, the demo simulates each family approving one new idea per day.
With GROQ_API_KEY set, --planner auto (the default) lets the language model plan, checked by
Hermano's rules; without it, or with --planner rules, the rule-based planner runs alone.
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
from pathlib import Path

from .agent import Rotation, report_for, run_day, simulate_family_approvals
from .approvals import Approvals
from .i18n import DEFAULT_LANG, LANGS, pick, t
from .llm import make_llm
from .models import load_school
from .planner import forbidden_names, llm_plan_day
from .qloo import make_client

DATA = Path(__file__).resolve().parent / "data"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hermano")
    ap.add_argument("cmd", choices=["demo", "privacy", "serve"])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--lang", choices=LANGS, default=DEFAULT_LANG)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--no-approvals", action="store_true",
                    help="do not simulate families approving new ideas between days")
    ap.add_argument("--planner", choices=["auto", "rules", "llm"], default="auto",
                    help="auto: the language model if GROQ_API_KEY is set, rules otherwise")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    a = ap.parse_args(argv)
    lang = a.lang
    if a.cmd == "serve":
        from . import web
        app = web.make_app(a.data, planner=a.planner)
        if a.planner == "llm" and not app.has_llm:
            print("--planner llm needs GROQ_API_KEY", file=sys.stderr)
            return 2
        web.serve(app, a.host, a.port)
        return 0
    school = load_school(Path(a.data) / "demo_school.json")
    approvals = Approvals.load(Path(a.data) / "demo_approvals.json")
    qloo = make_client(a.data)
    llm = None if a.planner == "rules" else make_llm(forbidden=forbidden_names(school))
    if a.planner == "llm" and llm is None:
        print("--planner llm needs GROQ_API_KEY", file=sys.stderr)
        return 2
    planner = functools.partial(llm_plan_day, llm=llm)
    rotation = Rotation()
    profiles = None
    for day in range(a.days):
        records, profiles = run_day(school, qloo, rotation, approvals, day, profiles, lang, planner)
        if a.cmd == "demo":
            print(t(lang, "cli.day", day=day + 1, mode=qloo.mode, school=pick(school.name, lang)))
            for sid, rec in records.items():
                plan, prof = rec["plan"], profiles[sid]
                alias = plan["student"].alias
                print(f"\n[{alias}] {plan['greeting']}")
                if plan.get("planner") == "llm":
                    print("  " + t(lang, "cli.planner.llm", model=plan.get("model", "")))
                elif plan.get("planner_note"):
                    print("  " + t(lang, "cli.planner.fallback", reason=plan["planner_note"]))
                else:
                    print("  " + t(lang, "cli.planner.rules"))
                if prof.degraded:
                    print("  " + t(lang, "cli.degraded", alias=alias, reason=prof.degraded))
                if prof.refused:
                    print("  " + t(lang, "cli.refused", alias=alias, n=len(prof.refused)))
                for s in plan["steps"]:
                    print(f"  {s.block.start}  {s.first_then}")
                    if s.warning:
                        print(f"         {t(lang, 'cli.warning')}: {s.warning}")
            for adult in school.adults.values():
                print("\n" + "-" * 64)
                print(report_for(adult, school, records, lang))
        if not a.no_approvals and day + 1 < a.days:
            lines = simulate_family_approvals(school, records, approvals, lang)
            if a.cmd == "demo" and lines:
                print("\n" + "\n".join(lines) + "\n")
    if a.cmd == "privacy":
        print(t(lang, "cli.privacy", mode=qloo.mode, n=len(qloo.sent)))
        for line in qloo.sent:
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
