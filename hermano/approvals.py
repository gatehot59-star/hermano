"""Adult approval.

Nothing new from Qloo reaches a child until the child's family or teacher says yes. A no is
remembered, and the rejected entity is sent to Qloo as filter.exclude.entities next time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set

from .models import APPROVER_ROLES, Adult

APPROVED, REJECTED, PENDING = "approved", "rejected", "pending"


def item_key(domain: str, name: str) -> str:
    return f"{domain}:{name.casefold()}"


class Approvals:
    """What the adults around each child said yes or no to."""

    def __init__(self, data=None) -> None:
        self.approved: Dict[str, Set[str]] = {}
        self.rejected: Dict[str, Set[str]] = {}
        self.excluded: Dict[str, List[str]] = {}
        for sid, d in (data or {}).items():
            if sid.startswith("_"):
                continue
            self.approved[sid] = {k.casefold() for k in d.get("approved", [])}
            self.rejected[sid] = {k.casefold() for k in d.get("rejected", [])}
            self.excluded[sid] = list(d.get("excluded_ids", []))

    @classmethod
    def load(cls, path) -> "Approvals":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def status(self, student_id: str, domain: str, name: str) -> str:
        key = item_key(domain, name)
        if key in self.rejected.get(student_id, ()):
            return REJECTED
        if key in self.approved.get(student_id, ()):
            return APPROVED
        return PENDING

    def decide(self, adult: Adult, student_id: str, domain: str, name: str, ok: bool,
               entity_id: str = "") -> None:
        """Record an adult's yes or no. Only the child's own family or teacher may decide."""
        if adult.role not in APPROVER_ROLES or student_id not in adult.student_ids:
            raise PermissionError("only the child's family or teacher can approve a motivator")
        key = item_key(domain, name)
        yes = self.approved.setdefault(student_id, set())
        no = self.rejected.setdefault(student_id, set())
        if ok:
            yes.add(key)
            no.discard(key)
            return
        no.add(key)
        yes.discard(key)
        excluded = self.excluded.setdefault(student_id, [])
        if entity_id and entity_id not in excluded:
            excluded.append(entity_id)

    def excluded_ids(self, student_id: str) -> List[str]:
        return list(self.excluded.get(student_id, []))
