"""Qloo client.

With QLOO_API_KEY set, Hermano calls the hackathon API (GET only, key in the X-Api-Key
header). Without it, MockQloo answers from data/mock_taste.json, which the Hermano team
invented for the demo: it is NOT Qloo data.

Only interest names and Qloo entity ids go to Qloo. Never a child's alias, id or supports.
Qloo responses are never written to the repository.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import ENTITY_TYPES

BASE_URL = "https://hackathon.api.qloo.com"


def urn(kind: str) -> str:
    """Qloo entity type URN, e.g. urn:entity:book."""
    if kind not in ENTITY_TYPES:
        raise ValueError(f"unsupported Qloo entity type: {kind}")
    return "urn:entity:" + kind


class QlooClient:
    """Real client for the hackathon environment."""

    mode = "qloo"

    def __init__(self, api_key: str, base_url: str = BASE_URL,
                 opener: Optional[Callable] = None, timeout: float = 15.0) -> None:
        if not api_key:
            raise ValueError("api_key required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.sent: List[str] = []

    def _get(self, path: str, params: Dict[str, str]) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        self.sent.append(url)
        req = urllib.request.Request(
            url, headers={"X-Api-Key": self.api_key, "Accept": "application/json"}, method="GET")
        with self.opener(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def search(self, name: str, kind: str) -> Optional[str]:
        """Qloo entity id for a name, or None."""
        data = self._get("/search", {"query": name, "types": urn(kind), "take": "1"})
        results = data.get("results") or []
        if isinstance(results, dict):
            results = results.get("entities") or []
        for r in results:
            eid = r.get("entity_id") or r.get("id")
            if eid:
                return eid
        return None

    def insights(self, kind: str, entity_ids: List[str], take: int = 5) -> List[dict]:
        """Related entities of type `kind` for the given interest ids."""
        data = self._get("/v2/insights", {
            "filter.type": urn(kind),
            "signal.interests.entities": ",".join(entity_ids),
            "take": str(take)})
        results = data.get("results") or {}
        ents = results.get("entities") if isinstance(results, dict) else results
        out = []
        for e in ents or []:
            if e.get("name"):
                out.append({"name": e["name"], "kind": kind, "id": e.get("entity_id") or e.get("id") or ""})
        return out


class MockQloo:
    """Synthetic stand-in used until the API key arrives."""

    mode = "mock"

    def __init__(self, path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.table = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.sent: List[str] = []

    def search(self, name: str, kind: str) -> Optional[str]:
        urn(kind)
        self.sent.append(f"search {kind}: {name}")
        key = name.lower()
        return "mock:" + key if key in self.table else None

    def insights(self, kind: str, entity_ids: List[str], take: int = 5) -> List[dict]:
        urn(kind)
        self.sent.append(f"insights {kind}: {','.join(entity_ids)}")
        names: List[str] = []
        for eid in entity_ids:
            key = eid.split(":", 1)[1] if eid.startswith("mock:") else eid
            for item in self.table.get(key, {}).get(kind, []):
                if item not in names:
                    names.append(item)
        return [{"name": n, "kind": kind, "id": "mock:" + n.lower()} for n in names[:take]]


def make_client(data_dir):
    """Real client if QLOO_API_KEY is set, mock otherwise."""
    key = os.environ.get("QLOO_API_KEY", "").strip()
    if key:
        return QlooClient(key)
    return MockQloo(Path(data_dir) / "mock_taste.json")
