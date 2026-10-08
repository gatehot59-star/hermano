"""Command line demo.

    python -m hermano demo [--days N]    plan, child view and the report for every adult
    python -m hermano privacy [--days N] everything that was sent to Qloo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import Rotation, report_for, run_day
from .models import load_school
from .qloo import make_client

DATA = Path(__file__).resolve().parent.parent / "data"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hermano")
    ap.add_argument("cmd", choices=["demo", "privacy"])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--data", default=str(DATA))
    a = ap.parse_args(argv)
    school = load_school(Path(a.data) / "demo_school.json")
    qloo = make_client(a.data)
    rotation = Rotation()
    profiles = None
    for day in range(a.days):
        records, profiles = run_day(school, qloo, rotation, day, profiles)
        if a.cmd != "demo":
            continue
        print(f"=== Día {day + 1} · Qloo en modo {qloo.mode} · {school.name} ===")
        for rec in records.values():
            plan = rec["plan"]
            print(f"\n[{plan['student'].alias}] {plan['greeting']}")
            for s in plan["steps"]:
                print(f"  {s.block.start}  {s.first_then}")
                if s.warning:
                    print(f"         aviso: {s.warning}")
        for adult in school.adults.values():
            print("\n" + "-" * 64)
            print(report_for(adult, school, records))
    if a.cmd == "privacy":
        print(f"Qloo en modo {qloo.mode}: {len(qloo.sent)} pedidos")
        for q in qloo.sent:
            print("  " + q)
    return 0


if __name__ == "__main__":
    sys.exit(main())
