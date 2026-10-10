"""Fixes after the independent audit of main at c81a34b (TITAN Tao, 2026-10-09), issues #1 to #4."""
import json
import os
import time
import unittest
from unittest import mock

from hermano import llm
from hermano.agent import Rotation, _show, build_profile
from hermano.approvals import Approvals
from hermano.i18n import LANGS, TEXT, note_words, t
from hermano.models import load_school
from hermano.planner import forbidden_names, llm_plan_day
from hermano.qloo import MockQloo, make_client
from hermano.web import CSP, App, esc, make_app
from tests.helpers import DATA, Recorder
from tests.test_planner import GOOD, body, calls, done, script
from tests.test_web import TODAY, Browser, Planner, WebCase


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


class AuditCase(WebCase):
    """The web app with a counter of Qloo clients: a Yes must not ask Qloo again."""

    def app(self, *planners, background=False, clock=time.time):
        planners = planners or (("rules", Planner()),)
        self.clients = 0

        def make_qloo():
            self.clients += 1
            return make_client(str(DATA))
        return App(self.school, self.raw, make_qloo, list(planners), self.cache, tag="test",
                   background=background, today=lambda: self.day, clock=clock)

    def first_idea(self, app, sid="s1"):
        return self.plan(app, sid)["pending"][0]

    def yes(self, browser, c, ok=1):
        return browser.post("/adult/a5/decide", sid="s1", domain=c.domain, name=c.name, ok=ok)


class TestAYesReachesTheChild(AuditCase):
    """Issue #4: after a Yes, the idea is in that visitor's child view, as the note says."""

    def test_the_approved_idea_is_in_the_childs_day_for_that_visitor_only(self):
        app = self.app()
        one, two = Browser(app), Browser(app)
        c = self.first_idea(app)
        shown = esc(_show("en", c.domain, c.name))
        self.assertNotIn(shown, one.get("/child/s1")[2])
        two.get("/child/s1")
        builds, clients = dict(app.builds), self.clients
        self.assertEqual(self.yes(one, c)[0], 303)
        self.assertIn(esc(t("en", "web.approved", item=_show("en", c.domain, c.name))),
                      one.get("/adult/a5")[2])
        self.assertIn(shown, one.get("/child/s1")[2])
        self.assertNotIn(shown, two.get("/child/s1")[2])
        self.assertEqual(app.builds, builds)  # no plan was built again for everyone
        self.assertEqual(self.clients, clients)  # and Qloo was not asked again

    def test_a_yes_for_one_child_leaves_the_other_childrens_day_alone(self):
        app = self.app()
        b = Browser(app)
        c = self.first_idea(app)
        sofi = b.get("/child/s2")[2]
        self.yes(b, c)
        b.get("/adult/a5")  # the one-time note is read there
        self.assertEqual(b.get("/child/s2")[2], sofi)
        note = esc(t("en", "web.replanned", names=self.school.students["s2"].alias))
        self.assertNotIn(note, b.get("/adult/a6")[2])
        self.assertNotIn("replanned", self.plan(app, "s2"))

    def test_reset_takes_the_yes_back(self):
        app = self.app()
        b = Browser(app)
        c = self.first_idea(app)
        self.yes(b, c)
        b.post("/reset")
        self.assertNotIn(esc(_show("en", c.domain, c.name)), b.get("/child/s1")[2])

    def test_the_family_and_the_teacher_are_told_the_day_was_planned_again(self):
        app = self.app()
        one, two = Browser(app), Browser(app)
        c = self.first_idea(app)
        note = esc(t("en", "web.replanned", names=self.school.students["s1"].alias))
        self.assertNotIn(note, one.get("/adult/a5")[2])
        self.yes(one, c)
        self.assertIn(note, one.get("/adult/a5")[2])
        self.assertIn(note, one.get("/adult/a1")[2])
        self.assertNotIn(note, two.get("/adult/a5")[2])

    def test_a_no_changes_nothing_in_the_childs_day(self):
        app = self.app()
        b = Browser(app)
        c = self.first_idea(app)
        before = b.get("/child/s1")[2]
        self.yes(b, c, ok=0)
        self.assertIn(esc(t("en", "web.rejected", item=_show("en", c.domain, c.name))),
                      b.get("/adult/a5")[2])
        self.assertEqual(b.get("/child/s1")[2], before)

    def test_with_plans_from_the_cache_file_the_yes_is_saved_not_promised(self):
        self.app().plans("en")  # writes today's plans to the cache file
        app = self.app()  # a restart: the plans come from the file, the Qloo answers do not
        b = Browser(app)
        c = self.first_idea(app)
        before = b.get("/child/s1")[2]
        self.yes(b, c)
        self.assertIn(esc(t("en", "web.approved_saved", item=_show("en", c.domain, c.name))),
                      b.get("/adult/a5")[2])
        self.assertEqual(b.get("/child/s1")[2], before)
        self.assertEqual(self.clients, 0)


class Fallback(Planner):
    """An agent that fell back to the rules for every child, with `note` as the reason."""

    def __init__(self, note="no valid plan", only=None):
        super().__init__()
        self.note, self.only = note, only

    def __call__(self, student, *args, **kw):
        plan = super().__call__(student, *args, **kw)
        if student.id == self.only:  # this child got the model's plan
            plan["planner"], plan["model"] = "llm", "fake-model"
        else:
            plan["planner_note"] = self.note
        return plan


class TestStatusSaysTheTruth(AuditCase):
    """Issue #2: /healthz and the pages say who really planned each child's day."""

    def health(self, app):
        return json.loads(app.handle("GET", "/healthz", {}, {}, "")[2])

    def test_healthz_says_rules_when_every_child_fell_back(self):
        app = self.app(("rules", Planner()), ("llm", Fallback()))
        app.plans("en")
        self.assertEqual(app.builds["llm"], 1)
        h = self.health(app)
        self.assertEqual(h["ready"]["en"], [TODAY, "rules"])
        self.assertEqual(h["llm_children"]["en"], 0)
        body = Browser(app).get("/")[2]
        self.assertIn(esc(t("en", "cli.planner.fallback", reason=t("en", "note.no_valid_plan"))), body)

    def test_healthz_says_llm_when_the_model_planned_every_child(self):
        app = self.app(("rules", Planner()), ("llm", Planner(suggest=True)))
        app.plans("en")
        h = self.health(app)
        self.assertEqual(h["ready"]["en"], [TODAY, "llm"])
        self.assertEqual(h["llm_children"]["en"], len(self.school.students))

    def test_a_day_planned_by_both_is_called_mixed(self):
        app = self.app(("rules", Planner()), ("llm", Fallback("LLMUnavailable", only="s1")))
        b = Browser(app)
        b.get("/")
        body = b.get("/")[2]
        h = self.health(app)
        self.assertEqual(h["ready"]["en"], [TODAY, "mixed"])
        self.assertEqual(h["llm_children"]["en"], 1)
        self.assertIn(esc(t("en", "cli.planner.mixed", model="fake-model", n=1,
                            total=len(self.school.students))), body)
        self.assertNotIn(esc(t("en", "cli.planner.llm", model="fake-model")), body)

    def test_the_fallback_reason_is_in_words(self):
        app = self.app(("rules", Planner()), ("llm", Fallback("LLMUnavailable")))
        app.plans("es")  # the rule plan, then the agent's day
        body = Browser(app).get("/", lang="es")[2]
        self.assertIn(esc(t("es", "cli.planner.fallback", reason=t("es", "note.LLMUnavailable"))), body)
        self.assertNotIn("LLMUnavailable", body)
        self.assertEqual(note_words("en", "something new"), "something new")

    def test_the_cookie_is_secure_only_behind_https(self):
        for value, secure in (("1", True), ("", False)):
            with mock.patch.dict(os.environ, {"HERMANO_HTTPS": value}):
                app = make_app(DATA, planner="rules", cache_dir=self.cache, background=False)
            cookie = dict(Browser(app).get("/child/s1")[1])["Set-Cookie"]
            self.assertEqual("; Secure" in cookie, secure, value)

    def test_no_page_may_be_framed(self):
        self.assertIn("frame-ancestors 'none'", CSP)
        self.assertEqual(dict(Browser(self.app()).get("/")[1])["Content-Security-Policy"], CSP)

    def test_the_principal_never_sees_which_child_felt_nervous(self):
        app = self.app()
        b = Browser(app)
        b.post("/child/s1/checkin", step=0, nervous=1)
        page = b.get("/adult/a4")[2]
        report = page[page.index("class='card report'>"):]
        report = report[:report.index("</div>")]
        self.assertIn(esc("\u201cI feel nervous\u201d"), report)  # the principal gets the count only
        for st in self.school.students.values():
            self.assertNotIn(esc(st.alias) + ": ", report)

    def test_a_visitors_reset_keeps_the_days_plans(self):
        app = self.app()
        b = Browser(app)
        b.get("/")
        day, builds = app.ready["en"], dict(app.builds)
        b.post("/reset")
        self.assertIs(app.ready.get("en"), day)
        b.get("/")
        self.assertEqual(app.builds, builds)


class TestNoPlanNoLabel(unittest.TestCase):
    """Issue #2: a model that never submits a plan does not get the model's label."""

    def test_a_model_that_never_submits_leaves_the_rules_label(self):
        school = load_school(DATA / "demo_school.json")
        approvals = Approvals.load(DATA / "demo_approvals.json")
        leo = school.students["s1"]
        profile = build_profile(leo, MockQloo(DATA / "mock_taste.json"), school.roster(), approvals)
        net = Recorder(script(calls(("get_day", {})), done()))
        model = llm.GroqChat("k", opener=net, sleep=lambda s: None, forbidden=forbidden_names(school))
        plan = llm_plan_day(leo, school.schedules["s1"], profile, Rotation(), approvals, "es", llm=model)
        self.assertEqual(plan["planner"], "rules")
        self.assertEqual(plan["planner_note"], "no valid plan")
        self.assertEqual(note_words("es", plan["planner_note"]), t("es", "note.no_valid_plan"))


if __name__ == "__main__":
    unittest.main()
