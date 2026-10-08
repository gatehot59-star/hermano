"""Qloo client.

With QLOO_API_KEY set, Hermano calls the hackathon API: GET only, key in the X-Api-Key header,
base URL https://hackathon.api.qloo.com and no other. Without a key, MockQloo answers from
data/mock_taste.json, which the Hermano team invented for the demo: it is NOT Qloo data.

Only interest names and Qloo entity ids go to Qloo, never a child's alias, id or supports, and
the client itself refuses query text that looks like health or contact data. Responses may be
cached on the server (cache/, git-ignored) and are never written to the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .guard import guard_query
from .models import ENTITY_TYPES

HACKATHON_BASE_URL = "https://hackathon.api.qloo.com"
BASE_URL = HACKATHON_BASE_URL
# /search names some types differently from /v2/insights (developer guide; /search OpenAPI
# reference read 2026-10-08): video games are urn:entity:videogame there.
SEARCH_TYPES = {
    "artist": "urn:entity:artist", "book": "urn:entity:book", "brand": "urn:entity:brand",
    "movie": "urn:entity:movie", "person": "urn:entity:person", "place": "urn:entity:place",
    "podcast": "urn:entity:podcast", "tv_show": "urn:entity:tv_show",
    "video_game": "urn:entity:videogame",
}
# filter.content_rating only applies to these Insights types.
RATED_KINDS = ("movie", "tv_show")
RETRY_STATUS = (429, 500, 502, 503, 504)


class QlooError(Exception):
    def __init__(self, status: Optional[int], message: str) -> None:
        super().__init__(f"{status or 'network'}: {message}")
        self.status = status


class QlooUnavailable(QlooError):
    """No answer after the retries: rate limit, server error, network or timeout."""


class UnsupportedType(QlooError):
    """Qloo answered 403 for an entity type: the developer guide says it is not supported."""


def urn(kind: str) -> str:
    """Insights filter.type URN, e.g. urn:entity:book."""
    if kind not in ENTITY_TYPES:
        raise ValueError(f"unsupported Qloo entity type: {kind}")
    return "urn:entity:" + kind


def search_urn(kind: str) -> str:
    """The /search `types` value for a kind."""
    if kind not in SEARCH_TYPES:
        raise ValueError(f"/search has no type for: {kind}")
    return SEARCH_TYPES[kind]


_KNOWN_TYPES = set(SEARCH_TYPES.values()) | {"urn:entity:" + k for k in ENTITY_TYPES}


def _type_matches(entity: dict, kind: str) -> bool:
    """False only when the entity says it is another known type. Qloo ignores invalid
    parameters silently, so a match of the wrong type is possible."""
    declared = entity.get("types") or entity.get("type") or entity.get("subtype") or []
    if isinstance(declared, str):
        declared = [declared]
    known = [d for d in declared if d in _KNOWN_TYPES]
    if not known:
        return True
    return any(d in (search_urn(kind), "urn:entity:" + kind) for d in known)


class ResponseCache:
    """Private server-side cache of Qloo answers, which the developer guide allows. Keyed by a
    hash of the request; the API key is never part of the key or of the file."""

    def __init__(self, directory, ttl: float = 7 * 24 * 3600,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(directory)
        self.ttl = ttl
        self.clock = clock

    @staticmethod
    def key(path: str, params: Dict[str, str]) -> str:
        raw = path + "?" + urllib.parse.urlencode(sorted(params.items()))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        p = self.dir / (key + ".json")
        if not p.is_file() or self.clock() - p.stat().st_mtime > self.ttl:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def put(self, key: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / (key + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.dir / (key + ".json"))


class QlooClient:
    """Real client for the hackathon environment."""

    mode = "qloo"

    def __init__(self, api_key: str, base_url: str = HACKATHON_BASE_URL,
                 opener: Optional[Callable] = None, timeout: float = 15.0, retries: int = 2,
                 backoff: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 cache: Optional[ResponseCache] = None) -> None:
        if not api_key:
            raise ValueError("api_key required")
        if base_url.rstrip("/") != HACKATHON_BASE_URL:
            raise ValueError("Hermano only talks to the Qloo hackathon API")
        self.api_key = api_key
        self.base_url = HACKATHON_BASE_URL
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.cache = cache
        self.sent: List[str] = []

    def _should_retry(self, attempt: int) -> bool:
        return attempt < self.retries

    def _wait(self, attempt: int, retry_after: Optional[str] = None) -> None:
        delay = self.backoff * (2 ** attempt)
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
        self.sleep(min(max(delay, 0.0), 30.0))

    def _get(self, path: str, params: Dict[str, str]) -> dict:
        if "query" in params:
            guard_query(params["query"])
        key = ResponseCache.key(path, params)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            return cached
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        attempt = 0
        while True:
            self.sent.append(url)
            req = urllib.request.Request(
                url, headers={"X-Api-Key": self.api_key, "Accept": "application/json"}, method="GET")
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code == 404 and path == "/search":
                    data = {"results": []}  # documented: 404 means no results
                    break
                if e.code == 403:
                    raise UnsupportedType(403, "entity type not supported")
                if e.code not in RETRY_STATUS:
                    raise QlooError(e.code, "request refused")
                if not self._should_retry(attempt):
                    raise QlooUnavailable(e.code, "no answer after retries")
                self._wait(attempt, e.headers.get("Retry-After") if e.headers else None)
                attempt += 1
            except (urllib.error.URLError, OSError):
                if not self._should_retry(attempt):
                    raise QlooUnavailable(None, "network error or timeout")
                self._wait(attempt)
                attempt += 1
            except ValueError:
                raise QlooError(None, "the answer was not JSON")
        if self.cache:
            self.cache.put(key, data)
        return data

    def search(self, name: str, kind: str) -> Optional[str]:
        """Qloo entity id for an interest name, or None."""
        data = self._get("/search", {"query": name, "types": search_urn(kind), "take": "3"})
        results = data.get("results") or []
        if isinstance(results, dict):
            results = results.get("entities") or []
        for r in results:
            if not _type_matches(r, kind):
                continue
            eid = r.get("entity_id") or r.get("id")
            if eid:
                return eid
        return None

    def insights(self, kind: str, entity_ids: Sequence[str], take: int = 5,
                 content_ratings: Sequence[str] = (), exclude_ids: Sequence[str] = ()) -> List[dict]:
        """Related entities of type `kind` for the given interest ids."""
        params = {"filter.type": urn(kind), "signal.interests.entities": ",".join(entity_ids),
                  "take": str(take)}
        if content_ratings and kind in RATED_KINDS:
            params["filter.content_rating"] = ",".join(content_ratings)
        if exclude_ids:
            params["filter.exclude.entities"] = ",".join(exclude_ids)
        data = self._get("/v2/insights", params)
        results = data.get("results") or {}
        ents = results.get("entities") if isinstance(results, dict) else results
        out = []
        for e in ents or []:
            if e.get("name"):
                out.append({"name": e["name"], "kind": kind,
                            "id": e.get("entity_id") or e.get("id") or ""})
        return out


class MockQloo:
    """Synthetic stand-in used until the API key arrives. It imitates the content-rating
    filter and the exclusions with ratings the team typed in mock_taste.json."""

    mode = "mock"

    def __init__(self, path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        ratings = raw.get("_content_ratings", {})
        self.ratings = {k.casefold(): v for k, v in ratings.items() if not k.startswith("_")}
        self.table = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.sent: List[str] = []

    def search(self, name: str, kind: str) -> Optional[str]:
        search_urn(kind)
        guard_query(name)
        self.sent.append(f"search {kind}: {name}")
        key = name.casefold()
        return "mock:" + key if key in self.table else None

    def insights(self, kind: str, entity_ids: Sequence[str], take: int = 5,
                 content_ratings: Sequence[str] = (), exclude_ids: Sequence[str] = ()) -> List[dict]:
        urn(kind)
        rated = bool(content_ratings) and kind in RATED_KINDS
        line = f"insights {kind}: {','.join(entity_ids)}"
        if rated:
            line += " | content_rating=" + ",".join(content_ratings)
        if exclude_ids:
            line += " | exclude=" + ",".join(exclude_ids)
        self.sent.append(line)
        names: List[str] = []
        for eid in entity_ids:
            key = eid.split(":", 1)[1] if eid.startswith("mock:") else eid
            for item in self.table.get(key, {}).get(kind, []):
                if item in names or "mock:" + item.casefold() in exclude_ids:
                    continue
                if rated and self.ratings.get(item.casefold()) not in content_ratings:
                    continue
                names.append(item)
        return [{"name": n, "kind": kind, "id": "mock:" + n.casefold()} for n in names[:take]]


def make_client(data_dir, cache_dir=None):
    """Real client, with a private cache, if QLOO_API_KEY is set; mock otherwise."""
    key = os.environ.get("QLOO_API_KEY", "").strip()
    if key:
        directory = cache_dir or os.environ.get("HERMANO_CACHE_DIR") or "cache/qloo"
        return QlooClient(key, cache=ResponseCache(directory))
    return MockQloo(Path(data_dir) / "mock_taste.json")
