"""Command line demo.

    hermano demo [--days N] [--lang en|es]   plans, child view and the report for every adult
    hermano privacy [--days N]               everything that was sent to Qloo

With more than one day, the demo simulates each family approving one new idea per day.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import Rotation, report_for, run_day, simulate_family_approvals
from .approvals import Approvals
from .i18n import DEFAULT_LANG, LANGS, pick, t
from .models import load_school
from .qloo import make_client

DATA = Path(__file__).resolve().parent / "data"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hermano")
    ap.add_argument("cmd", choices=["demo", "privacy"])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--lang", choices=LANGS, default=DEFAULT_LANG)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--no-approvals", action="store_true",
                    help="do not simulate families approving new ideas between days")
    a = ap.parse_args(argv)
    lang = a.lang
    school = load_school(Path(a.data) / "demo_school.json")
    approvals = Approvals.load(Path(a.data) / "demo_approvals.json")
    qloo = make_client(a.data)
    rotation = Rotation()
    profiles = None
    for day in range(a.days):
        records, profiles = run_day(school, qloo, rotation, approvals, day, profiles, lang)
        if a.cmd == "demo":
            print(t(lang, "cli.day", day=day + 1, mode=qloo.mode, school=pick(school.name, lang)))
            for sid, rec in records.items():
                plan, prof = rec["plan"], profiles[sid]
                alias = plan["student"].alias
                print(f"\n[{alias}] {plan['greeting']}")
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
