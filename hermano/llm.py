"""Language model client: Qwen on Groq, OpenAI-compatible chat completions with tool calling.

Read live on 2026-10-08 (console.groq.com/docs): qwen/qwen3.8-27b is a Preview model (Groq says
for evaluation, may be discontinued at short notice), supports local tool use and parallel tool
calls, and needs reasoning_format "parsed" or "hidden" when tools are used. gpt-oss models do
not accept reasoning_format, so the fallback request leaves it out.

Privacy rules, enforced here and not only by the caller:
* every message is checked before it leaves: health words, contact data or any real name the
  caller declares as forbidden raise UnsafeText and nothing is sent;
* callers pass placeholders (STUDENT_1, ADULT_1) made by guard.Pseudonymizer;
* without GROQ_API_KEY make_llm returns None and Hermano uses its rules planner.
The key is read from the environment only and is never logged or written anywhere.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import USER_AGENT
from .guard import UnsafeText, contains_contact_run, contains_health

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PRIMARY_MODEL = "qwen/qwen3.8-27b"
FALLBACK_MODEL = "openai/gpt-oss-20b"
DEFAULT_MODELS = (PRIMARY_MODEL, FALLBACK_MODEL)
RETRY_STATUS = (429, 500, 502, 503, 504)
# A 400 with code tool_use_failed is a bad generation, not a bad request: gpt-oss-20b sometimes
# names a tool "propose_motivator<|channel|>commentary" (measured 2026-10-08), so the same
# model is asked again. Any other 4xx (for example a model Groq retired) moves on to the next.


def _tool_use_failed(error: urllib.error.HTTPError) -> bool:
    try:
        body = json.loads(error.read().decode("utf-8") or "{}")
    except (ValueError, OSError, AttributeError):
        return False
    return isinstance(body, dict) and (body.get("error") or {}).get("code") == "tool_use_failed"


def _retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    """Groq's Retry-After when it sends one (capped at 20 s), else 2 s, 4 s, ..."""
    try:
        value = float((error.headers or {}).get("retry-after", ""))
    except (TypeError, ValueError):
        value = 2.0 * (attempt + 1)
    return min(max(value, 0.0), 20.0)


class LLMUnavailable(Exception):
    """No model answered; the caller falls back to the rules planner."""


def model_options(model: str, with_tools: bool) -> Dict[str, str]:
    if model.startswith("qwen/"):
        # Tool calling requires parsed or hidden; hidden keeps reasoning out of reports.
        return {"reasoning_format": "hidden", "reasoning_effort": "none"} if with_tools else \
            {"reasoning_format": "hidden"}
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}
    return {}


def check_outgoing(messages: Sequence[dict], forbidden: Iterable[str] = ()) -> None:
    """Raise UnsafeText if any message carries health, contact or a forbidden name."""
    names = [n for n in forbidden if n and n.strip()]
    for m in messages:
        parts = [m.get("content") or ""]
        for call in m.get("tool_calls") or []:
            parts.append(call.get("function", {}).get("arguments") or "")
        text = "\n".join(p if isinstance(p, str) else json.dumps(p) for p in parts)
        if contains_health(text):
            raise UnsafeText("health detail in a message for the language model")
        if contains_contact_run(text):
            raise UnsafeText("contact data in a message for the language model")
        for n in names:
            if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text, re.IGNORECASE):
                raise UnsafeText("a real name in a message for the language model")


class GroqChat:
    def __init__(self, api_key: str, models: Sequence[str] = DEFAULT_MODELS,
                 opener: Callable = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 retries: int = 2, timeout: float = 30.0,
                 forbidden: Iterable[str] = ()) -> None:
        if not api_key:
            raise ValueError("GroqChat needs an API key")
        if not models:
            raise ValueError("GroqChat needs at least one model")
        self._key = api_key
        self.models = tuple(models)
        self.opener = opener
        self.sleep = sleep
        self.retries = retries
        self.timeout = timeout
        self.forbidden = tuple(forbidden)
        self.last_model: Optional[str] = None

    def __repr__(self) -> str:  # never show the key
        return f"GroqChat(models={self.models!r})"

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(GROQ_URL, data=data, method="POST", headers={
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
        with self.opener(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None,
             temperature: float = 0.2, max_tokens: int = 800) -> dict:
        """Return the assistant message of the first model that answers."""
        check_outgoing(messages, self.forbidden)
        errors = []
        for model in self.models:
            body = {"model": model, "messages": messages, "temperature": temperature,
                    "max_completion_tokens": max_tokens}
            body.update(model_options(model, bool(tools)))
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            for attempt in range(self.retries + 1):
                try:
                    out = self._post(body)
                    self.last_model = model
                    return out["choices"][0]["message"]
                except urllib.error.HTTPError as e:
                    errors.append(f"{model}: {e.code}")
                    again = e.code in RETRY_STATUS or (e.code == 400 and _tool_use_failed(e))
                    if again and attempt < self.retries:
                        self.sleep(_retry_delay(e, attempt))
                        continue
                    break  # 4xx or retries spent: next model
                except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                    errors.append(f"{model}: network")
                    if attempt < self.retries:
                        self.sleep(2.0 * (attempt + 1))
                        continue
                    break
                except (KeyError, IndexError, ValueError):
                    errors.append(f"{model}: bad answer")
                    break
        raise LLMUnavailable("; ".join(errors))


def make_llm(env: Optional[Dict[str, str]] = None, forbidden: Iterable[str] = (),
             **kw) -> Optional[GroqChat]:
    env = os.environ if env is None else env
    key = env.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    models = [m.strip() for m in env.get("HERMANO_MODELS", "").split(",") if m.strip()]
    return GroqChat(key, models or DEFAULT_MODELS, forbidden=forbidden, **kw)


def run_tools(llm: GroqChat, messages: List[dict], tools: Dict[str, tuple],
              max_steps: int = 6) -> Dict[str, object]:
    """Small agent loop. tools maps name -> (json_schema, function(**args) -> json-able).
    Unknown tools and bad arguments go back to the model as errors; the loop stops after
    max_steps model calls. Returns {text, trace, model}."""
    specs = [{"type": "function", "function": {"name": n, **schema}}
             for n, (schema, _fn) in tools.items()]
    msgs = list(messages)
    trace = []
    for _ in range(max_steps):
        reply = llm.chat(msgs, tools=specs)
        calls = reply.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": reply.get("content") or "",
                     **({"tool_calls": calls} if calls else {})})
        if not calls:
            return {"text": reply.get("content") or "", "trace": trace, "model": llm.last_model}
        for call in calls:
            fn_name = call.get("function", {}).get("name", "")
            raw = call.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw)
                if fn_name not in tools:
                    raise KeyError(fn_name)
                result = tools[fn_name][1](**args)
                ok = True
            except Exception as e:  # reported to the model, never crashes the run
                result = {"error": type(e).__name__}
                ok = False
            trace.append({"tool": fn_name, "ok": ok})
            msgs.append({"role": "tool", "tool_call_id": call.get("id", ""),
                         "content": json.dumps(result, ensure_ascii=False)})
    raise LLMUnavailable("tool loop did not finish in %d steps" % max_steps)
