"""Shared test helpers: a fake network that records every request."""
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import hermano

DATA = Path(hermano.__file__).resolve().parent / "data"
ROOT = Path(__file__).resolve().parent.parent


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(b"{}"))


def default_reply(req):
    if "/search" in req.full_url:
        return {"results": [{"name": "x", "entity_id": "E-x"}]}
    return {"results": {"entities": [{"name": "Y", "entity_id": "E-y"}]}}


class Recorder:
    """Stands in for urllib's opener: records each request, answers with reply(request).
    A reply that is an exception is raised, like the network would."""

    def __init__(self, reply=None):
        self.requests = []
        self.reply = reply or default_reply

    def __call__(self, req, timeout):
        self.requests.append(req)
        out = self.reply(req)
        if isinstance(out, Exception):
            raise out
        return FakeResp(out)

    def urls(self):
        return [urllib.parse.unquote_plus(r.full_url) for r in self.requests]

    def params(self, i=-1):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.requests[i].full_url).query)
