"""Fixes after the independent audit of main at c81a34b (TITAN Tao, 2026-10-09), issues #1 to #4."""
import json
import unittest

from hermano import llm
from hermano.agent import Rotation, build_profile
from hermano.approvals import Approvals
from hermano.i18n import LANGS, TEXT, t
from hermano.models import load_school
from hermano.planner import forbidden_names, llm_plan_day
from hermano.qloo import MockQloo
from tests.helpers import DATA, Recorder
from tests.test_planner import GOOD, body, calls, done, script


class TestNoPromiseOfAnAlert(unittest.TestCase):
    """Issue #3: after "I feel nervous", Hermano does not tell the child the teacher was alerted."""

    def test_the_child_is_told_the_truth_and_what_to_do_now(self):
        en = t("en", "nervous.told")
        self.assertNotIn("I told", en)
        self.assertIn("today's report", en)
        self.assertIn("raise your hand", en)
        es = t("es", "nervous.told")
        self.assertNotIn("avis\u00e9", es)
        self.assertIn("informe de hoy", es)
        self.assertIn("levant\u00e1 la mano", es)


class TestDecisionNotes(unittest.TestCase):
    """Issue #4: the note after a Yes or a No promises nothing the demo does not do."""

    def test_no_note_promises_tomorrow_or_a_new_qloo_request(self):
        for lang in LANGS:
            for key in ("web.approved", "web.approved_saved", "web.rejected"):
                text = t(lang, key, item="X")
                for word in ("tomorrow", "ma\u00f1ana", "Qloo"):
                    self.assertNotIn(word, text, (lang, key))

    def test_both_languages_have_the_new_texts(self):
        for key in ("web.approved_saved", "web.replanned", "cli.planner.mixed",
                    "note.LLMUnavailable", "note.UnsafeText", "note.no_valid_plan"):
            for lang in LANGS:
                self.assertIn(key, TEXT[lang], (lang, key))


class TestReasonsNameTheChildsTastes(unittest.TestCase):
    """Issue #1: the agent's reason for a new idea names the child's taste it comes from."""

    def setUp(self):
        self.school = load_school(DATA / "demo_school.json")
        self.approvals = Approvals.load(DATA / "demo_approvals.json")
        self.leo = self.school.students["s1"]
        self.profile = build_profile(self.leo, MockQloo(DATA / "mock_taste.json"),
                                     self.school.roster(), self.approvals)

    def run_with(self, reason):
        self.net = Recorder(script(
            calls(("get_day", {}), ("get_motivators", {})),
            calls(("submit_plan", {"rewards": GOOD, "suggestions": [
                {"candidate_id": "C3", "reason": reason}]})),
            done()))
        model = llm.GroqChat("k", opener=self.net, sleep=lambda s: None,
                             forbidden=forbidden_names(self.school))
        return llm_plan_day(self.leo, self.school.schedules["s1"], self.profile, Rotation(),
                            self.approvals, "en", llm=model)

    def last_tool_answer(self, i):
        tools = [m for m in body(self.net.requests[i])["messages"] if m["role"] == "tool"]
        return json.loads(tools[-1]["content"])

    def test_the_model_is_told_the_childs_tastes(self):
        self.run_with("STUDENT_1 loves Bluey, and Puffin Rock is a calm show about animals.")
        self.assertEqual(self.last_tool_answer(1)["child_tastes"], ["Bluey", "Minecraft"])
        self.assertIn("child_tastes", body(self.net.requests[0])["messages"][0]["content"])

    def test_a_reason_without_a_taste_is_refused(self):
        plan = self.run_with("STUDENT_1 loves animals and dragon stories.")
        answer = self.last_tool_answer(2)
        self.assertEqual(answer["accepted_suggestions"], 0)
        self.assertIn("Bluey", answer["problems"][0])
        self.assertEqual(plan["suggested"], [])
        self.assertEqual(plan["planner"], "llm")  # the rewards were still accepted

    def test_a_reason_naming_a_taste_keeps_the_suggestion(self):
        plan = self.run_with("STUDENT_1 loves Bluey, and Puffin Rock is a calm show about animals.")
        self.assertEqual([x["candidate"].name for x in plan["suggested"]], ["Puffin Rock"])
        self.assertIn("Bluey", plan["suggested"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
