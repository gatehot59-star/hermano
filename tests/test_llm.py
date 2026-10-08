"""Language model client and tool loop, against a fake Groq: no network, no key."""
import io
import json
import socket
import unittest
import urllib.error

from hermano import llm
from hermano.guard import UnsafeText
from tests.helpers import Recorder, http_error

KEY = "gsk_test_not_a_real_key"


def text_reply(content="ok"):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def call_reply(name, args, cid="c1"):
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def script(*answers):
    seq = list(answers)
    return lambda req: seq.pop(0) if len(seq) > 1 else seq[0]


def body(req):
    return json.loads(req.data.decode("utf-8"))


MSG = [{"role": "user", "content": "STUDENT_1 likes dinosaurs. Suggest a reward."}]


class ClientTest(unittest.TestCase):
    def make(self, reply, **kw):
        self.net = Recorder(reply)
        self.sleeps = []
        return llm.GroqChat(KEY, opener=self.net, sleep=self.sleeps.append, **kw)

    def test_no_key_means_rules_planner(self):
        self.assertIsNone(llm.make_llm({}))
        self.assertIsNone(llm.make_llm({"GROQ_API_KEY": "  "}))

    def test_request_shape_for_qwen_with_tools(self):
        c = self.make(lambda r: text_reply())
        c.chat(MSG, tools=[{"type": "function", "function": {"name": "t"}}])
        req = self.net.requests[0]
        self.assertEqual(req.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer " + KEY)
        # Groq blocks the default Python-urllib user agent with a 403 (error 1010).
        self.assertTrue(req.get_header("User-agent", "").startswith("hermano/"))
        b = body(req)
        self.assertEqual(b["model"], "qwen/qwen3.8-27b")
        self.assertIn(b["reasoning_format"], ("parsed", "hidden"))
        self.assertEqual(b["tool_choice"], "auto")

    def test_fallback_model_gets_no_reasoning_format(self):
        c = self.make(script(http_error(llm.GROQ_URL, 404), text_reply()))
        c.chat(MSG, tools=[{"type": "function", "function": {"name": "t"}}])
        b = body(self.net.requests[1])
        self.assertEqual(b["model"], "openai/gpt-oss-20b")
        self.assertNotIn("reasoning_format", b)
        self.assertEqual(c.last_model, "openai/gpt-oss-20b")

    def test_429_is_retried_on_same_model(self):
        c = self.make(script(http_error(llm.GROQ_URL, 429), text_reply("fine")))
        self.assertEqual(c.chat(MSG)["content"], "fine")
        self.assertEqual([body(r)["model"] for r in self.net.requests], [llm.PRIMARY_MODEL] * 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_bad_tool_generation_is_retried_other_400_is_not(self):
        # Groq answers 400 tool_use_failed when the model misnames a tool (seen with gpt-oss-20b).
        bad = urllib.error.HTTPError(llm.GROQ_URL, 400, "bad", {}, io.BytesIO(json.dumps(
            {"error": {"code": "tool_use_failed", "message": "tool call validation failed"}}).encode()))
        c = self.make(script(bad, text_reply("fine")), models=(llm.FALLBACK_MODEL,))
        self.assertEqual(c.chat(MSG)["content"], "fine")
        self.assertEqual(len(self.net.requests), 2)
        c = self.make(script(http_error(llm.GROQ_URL, 400), text_reply()), models=(llm.FALLBACK_MODEL,))
        with self.assertRaises(llm.LLMUnavailable):
            c.chat(MSG)
        self.assertEqual(len(self.net.requests), 1)

    def test_timeout_is_retried(self):
        c = self.make(script(socket.timeout("slow"), text_reply("fine")))
        self.assertEqual(c.chat(MSG)["content"], "fine")
        self.assertEqual(len(self.net.requests), 2)

    def test_all_models_down_raises_unavailable(self):
        c = self.make(lambda r: http_error(llm.GROQ_URL, 503))
        with self.assertRaises(llm.LLMUnavailable):
            c.chat(MSG)
        self.assertEqual(len(self.net.requests), 2 * 3)

    def test_health_word_never_leaves(self):
        c = self.make(lambda r: text_reply())
        with self.assertRaises(UnsafeText):
            c.chat([{"role": "user", "content": "STUDENT_1 takes metilfenidato"}])
        self.assertEqual(self.net.requests, [])

    def test_contact_never_leaves(self):
        c = self.make(lambda r: text_reply())
        with self.assertRaises(UnsafeText):
            c.chat([{"role": "user", "content": "write to mom@example.com"}])
        self.assertEqual(self.net.requests, [])

    def test_real_name_never_leaves_placeholder_does(self):
        c = self.make(lambda r: text_reply(), forbidden=["Leo Gomez", "Leo"])
        with self.assertRaises(UnsafeText):
            c.chat([{"role": "user", "content": "leo likes dinosaurs"}])
        self.assertEqual(self.net.requests, [])
        c.chat(MSG)
        self.assertEqual(len(self.net.requests), 1)

    def test_tool_call_arguments_are_checked(self):
        c = self.make(lambda r: text_reply())
        msgs = MSG + [{"role": "assistant", "content": "", "tool_calls": [{"id": "c9",
            "type": "function", "function": {"name": "note",
            "arguments": json.dumps({"text": "needs metilfenidato"})}}]}]
        with self.assertRaises(UnsafeText):
            c.chat(msgs)
        self.assertEqual(self.net.requests, [])

    def test_key_not_in_repr(self):
        c = self.make(lambda r: text_reply())
        self.assertNotIn(KEY, repr(c))

    def test_models_from_env(self):
        c = llm.make_llm({"GROQ_API_KEY": KEY, "HERMANO_MODELS": "a/b, c/d"})
        self.assertEqual(c.models, ("a/b", "c/d"))


class LoopTest(unittest.TestCase):
    def tools(self):
        self.calls = []

        def schedule(day):
            self.calls.append(day)
            return {"blocks": ["math", "recess", "art"]}
        return {"get_schedule": ({"description": "school day", "parameters": {
            "type": "object", "properties": {"day": {"type": "integer"}}}}, schedule)}

    def test_tool_is_called_and_result_sent_back(self):
        net = Recorder(script(call_reply("get_schedule", {"day": 1}), text_reply("after art")))
        c = llm.GroqChat(KEY, opener=net, sleep=lambda s: None)
        out = llm.run_tools(c, MSG, self.tools())
        self.assertEqual(out["text"], "after art")
        self.assertEqual(self.calls, [1])
        self.assertEqual(out["trace"], [{"tool": "get_schedule", "ok": True}])
        last = body(net.requests[1])["messages"][-1]
        self.assertEqual(last["role"], "tool")
        self.assertEqual(last["tool_call_id"], "c1")
        self.assertIn("recess", last["content"])

    def test_unknown_tool_goes_back_as_error(self):
        net = Recorder(script(call_reply("read_phone", {}), text_reply("done")))
        c = llm.GroqChat(KEY, opener=net, sleep=lambda s: None)
        out = llm.run_tools(c, MSG, self.tools())
        self.assertEqual(out["trace"], [{"tool": "read_phone", "ok": False}])
        self.assertIn("error", body(net.requests[1])["messages"][-1]["content"])

    def test_loop_has_a_step_limit(self):
        net = Recorder(lambda r: call_reply("get_schedule", {"day": 1}))
        c = llm.GroqChat(KEY, opener=net, sleep=lambda s: None)
        with self.assertRaises(llm.LLMUnavailable):
            llm.run_tools(c, MSG, self.tools(), max_steps=3)
        self.assertEqual(len(net.requests), 3)

    def test_tool_result_with_real_name_is_blocked(self):
        def leaky(day):
            return {"student": "Leo Gomez"}
        tools = {"get_schedule": ({"description": "x", "parameters": {"type": "object"}}, leaky)}
        net = Recorder(script(call_reply("get_schedule", {"day": 1}), text_reply()))
        c = llm.GroqChat(KEY, opener=net, sleep=lambda s: None, forbidden=["Leo Gomez"])
        with self.assertRaises(UnsafeText):
            llm.run_tools(c, MSG, tools)
        self.assertEqual(len(net.requests), 1)


if __name__ == "__main__":
    unittest.main()
