"""Web views: the child sees only approved ideas, no request plans again, every visitor has
their own demo, and only the child's family or teacher can decide."""
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from hermano.agent import _show, plan_day
from hermano.i18n import t
from hermano.models import SUPPORTS, load_school
from hermano.qloo import make_client
from hermano.web import CSP, SESSION_COOKIE, SESSION_TTL, App, esc, make_app, make_server
from tests.helpers import DATA

TODAY = "2026-10-08"
MARK = "REASON-MARK fits the child's love of animals"


class Planner:
    """The rule-based plan; with suggest=True it acts like the agent and suggests one idea."""

    def __init__(self, suggest=False, fail=False, gate=None):
        self.calls = 0
        self.suggest = suggest
        self.fail = fail
        self.gate = gate

    def __call__(self, student, blocks, profile, rotation, approvals, lang="en"):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError("model exploded")
        plan = plan_day(student, blocks, profile, rotation, approvals, lang)
        plan["planner"], plan["suggested"] = "rules", []
        if self.suggest:
            plan["planner"], plan["model"] = "llm", "fake-model"
            if plan["pending"]:
                plan["suggested"] = [{"candidate": plan["pending"][0], "reason": MARK}]
        return plan


class Browser:
    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def go(self, method, path, query=None, form=None):
        q = {k: [str(v)] for k, v in (query or {}).items()}
        f = {k: [str(v)] for k, v in (form or {}).items()}
        status, headers, body = self.app.handle(method, path, q, f, self.cookie)
        for k, v in headers:
            if k == "Set-Cookie":
                self.cookie = v.split(";")[0]
        return status, headers, body.decode("utf-8")

    def get(self, path, **query):
        return self.go("GET", path, query=query)

    def post(self, path, **form):
        return self.go("POST", path, form=form)


def form_for(c):
    return f"name='name' value='{esc(c.name)}'"


class WebCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("QLOO_API_KEY", "GROQ_API_KEY", "HERMANO_MODELS", "HERMANO_PLAN_DIR"):
            os.environ.pop(k, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = tmp.name
        self.school = load_school(DATA / "demo_school.json")
        self.raw = json.loads((DATA / "demo_approvals.json").read_text(encoding="utf-8"))
        self.day = TODAY

    def app(self, *planners, background=False, clock=time.time):
        planners = planners or (("rules", Planner()),)
        return App(self.school, self.raw, lambda: make_client(str(DATA)), list(planners),
                   self.cache, tag="test", background=background, today=lambda: self.day,
                   clock=clock)

    def plan(self, app, sid, lang="en"):
        return app.plans(lang)["records"][sid]["plan"]


class TestPages(WebCase):
    def test_home_lists_every_child_and_adult_in_both_languages(self):
        b = Browser(self.app())
        status, _, body = b.get("/")
        self.assertEqual(status, 200)
        for sid in self.school.students:
            self.assertIn(f"/child/{sid}?lang=en", body)
        for aid in self.school.adults:
            self.assertIn(f"/adult/{aid}?lang=en", body)
        self.assertIn(esc(t("en", "web.children")), body)
        status, _, body = b.get("/", lang="es")
        self.assertEqual(status, 200)
        self.assertIn(esc(t("es", "web.children")), body)
        self.assertIn("/child/s1?lang=es", body)

    def test_child_view_shows_the_day_and_only_approved_ideas(self):
        app = self.app(("rules", Planner(suggest=True)))
        status, _, body = Browser(app).get("/child/s1")
        self.assertEqual(status, 200)
        plan = self.plan(app, "s1")
        self.assertIn(esc(plan["greeting"]), body)
        for s in plan["steps"]:
            self.assertIn(esc(s.first_then), body)
        self.assertTrue(plan["pending"])
        for c in plan["pending"]:
            self.assertNotIn(esc(c.name), body)
        self.assertNotIn("REASON-MARK", body)
        self.assertNotIn("STUDENT_1", body)
        for code in self.school.students["s1"].supports:
            self.assertNotIn(esc(SUPPORTS[code]["en"]), body)

    def test_the_child_view_shows_the_movement_breaks_and_the_cache_keeps_them(self):
        app = self.app()
        plan = self.plan(app, "s2")
        moves = [s.movement for s in plan["steps"] if s.movement]
        self.assertTrue(moves)
        _, _, body = Browser(app).get("/child/s2")
        for m in moves:
            self.assertIn(esc(m), body)
        again = self.plan(self.app(), "s2")  # a new app reads the plans from the cache
        self.assertEqual([s.movement for s in again["steps"]], [s.movement for s in plan["steps"]])
        _, _, leo = Browser(app).get("/child/s1")
        self.assertNotIn("class='move'", leo)

    def test_family_and_teacher_see_the_agents_suggestion_with_its_reason(self):
        app = self.app(("rules", Planner(suggest=True)))
        b = Browser(app)
        c = self.plan(app, "s1")["suggested"][0]["candidate"]
        for aid in ("a1", "a5"):  # Leo's teacher and Leo's family
            status, _, body = b.get(f"/adult/{aid}")
            self.assertEqual(status, 200)
            self.assertIn("REASON-MARK", body)
            self.assertIn(form_for(c), body)
        for aid in ("a4", "a8"):  # principal and specialist: no decisions, no reasons
            status, _, body = b.get(f"/adult/{aid}")
            self.assertEqual(status, 200)
            self.assertNotIn("REASON-MARK", body)
            self.assertNotIn("/decide", body)

    def test_each_adult_sees_only_their_children(self):
        _, _, body = Browser(self.app()).get("/adult/a6")
        self.assertIn("Sofi", body)
        self.assertNotRegex(body, r"\bLeo\b|\bMati\b")


class TestPlansAreCached(WebCase):
    def test_requests_never_plan_again(self):
        p = Planner(suggest=True)
        app = self.app(("rules", p))
        b = Browser(app)
        b.get("/")
        self.assertEqual(p.calls, len(self.school.students))
        c = self.plan(app, "s1")["pending"][0]
        for path in ("/", "/child/s1", "/child/s2", "/adult/a1", "/adult/a4", "/adult/a5",
                     "/adult/a8", "/privacy", "/healthz"):
            self.assertEqual(b.get(path)[0], 200, path)
        b.post("/child/s1/checkin", step=1, face="bien")
        b.post("/adult/a5/decide", sid="s1", domain=c.domain, name=c.name, ok=1)
        self.assertEqual(p.calls, len(self.school.students))

    def test_the_cache_file_survives_a_restart(self):
        before = [s.first_then for s in self.plan(self.app(), "s2")["steps"]]
        p = Planner()
        after = [s.first_then for s in self.plan(self.app(("rules", p)), "s2")["steps"]]
        self.assertEqual(p.calls, 0)
        self.assertEqual(before, after)
        files = os.listdir(self.cache)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith(f"plans-{TODAY}-en-"))

    def test_a_new_day_gets_new_plans(self):
        p = Planner()
        app = self.app(("rules", p))
        app.plans("en")
        app.plans("en")
        self.day = "2026-10-09"
        app.plans("en")
        self.assertEqual(p.calls, 2 * len(self.school.students))

    def test_the_agents_plan_replaces_the_rule_plan_and_is_cached(self):
        rules, agent = Planner(), Planner(suggest=True)
        app = self.app(("rules", rules), ("llm", agent))
        app.plans("en")
        self.assertEqual(app.plans("en")["planner"], "llm")
        _, _, body = Browser(app).get("/")
        self.assertIn(esc(t("en", "cli.planner.llm", model="fake-model")), body)
        again = Planner(suggest=True)
        restarted = self.app(("rules", Planner()), ("llm", again))
        self.assertEqual(restarted.plans("en")["planner"], "llm")
        self.assertEqual(again.calls, 0)

    def test_while_the_agent_plans_the_rule_plan_is_served(self):
        gate = threading.Event()
        app = self.app(("rules", Planner()), ("llm", Planner(suggest=True, gate=gate)),
                       background=True)
        _, _, body = Browser(app).get("/")
        self.assertIn(esc(t("en", "web.building")), body)
        self.assertIn("http-equiv='refresh' content='20'", body)
        gate.set()
        for _ in range(100):
            if app.plans("en")["planner"] == "llm":
                break
            time.sleep(0.05)
        self.assertEqual(app.plans("en")["planner"], "llm")
        self.assertNotIn("http-equiv='refresh'", Browser(app).get("/")[2])

    def test_another_language_is_not_kept_waiting_by_the_agent(self):
        # Found against the real API: the first Spanish visitor waited for the English agent plan.
        gate = threading.Event()
        app = self.app(("rules", Planner()), ("llm", Planner(suggest=True, gate=gate)),
                       background=True)

        def finish():  # let both agent plans end before the cache folder is removed
            gate.set()
            for _ in range(250):
                if app.builds["llm"] >= 2:
                    break
                time.sleep(0.02)
            with app.agent_lock:
                pass
        self.addCleanup(finish)
        Browser(app).get("/")
        start = time.monotonic()
        status, _, body = Browser(app).get("/", lang="es")
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(status, 200)
        self.assertIn(esc(t("es", "web.building")), body)

    def test_if_the_agent_fails_the_rule_plan_stays(self):
        app = self.app(("rules", Planner()), ("llm", Planner(fail=True)))
        app.plans("en")
        self.assertEqual(app.plans("en")["planner"], "rules")
        _, _, body = Browser(app).get("/")
        self.assertIn(esc(t("en", "cli.planner.rules")), body)
        self.assertNotIn(esc(t("en", "web.building")), body)
        self.assertEqual(app.errors, ["RuntimeError"])


class TestDecisions(WebCase):
    def test_an_approval_belongs_to_the_visitor_who_made_it(self):
        app = self.app()
        one, two = Browser(app), Browser(app)
        c = self.plan(app, "s1")["pending"][0]
        self.assertIn(form_for(c), one.get("/adult/a5")[2])
        two.get("/adult/a5")
        self.assertNotEqual(one.cookie, two.cookie)
        status, headers, _ = one.post("/adult/a5/decide", sid="s1", domain=c.domain, name=c.name, ok=1)
        self.assertEqual(status, 303)
        self.assertIn(("Location", "/adult/a5?lang=en"), headers)
        done = esc(t("en", "web.approved", item=_show("en", c.domain, c.name)))
        _, _, body = one.get("/adult/a5")
        self.assertIn(done, body)
        self.assertNotIn(form_for(c), body)
        self.assertNotIn(done, one.get("/adult/a5")[2])  # the note shows once
        self.assertIn(form_for(c), two.get("/adult/a5")[2])

    def test_only_the_childs_family_or_teacher_can_decide(self):
        app = self.app()
        b = Browser(app)
        c = self.plan(app, "s1")["pending"][0]
        ok = dict(sid="s1", domain=c.domain, name=c.name, ok=1)
        for aid in ("a4", "a8", "a6", "a2"):  # principal, specialist, Sofi's family and teacher
            self.assertEqual(b.post(f"/adult/{aid}/decide", **ok)[0], 403, aid)
        self.assertEqual(b.post("/adult/a5/decide", **dict(ok, name="Nobody suggested this"))[0], 400)
        self.assertEqual(b.post("/adult/a5/decide", **dict(ok, domain="place"))[0], 400)
        self.assertEqual(b.post("/adult/a5/decide", **dict(ok, ok=2))[0], 400)
        self.assertEqual(b.post("/adult/zz/decide", **ok)[0], 404)
        self.assertIn(form_for(c), b.get("/adult/a5")[2])  # nothing was recorded
        self.assertEqual(b.post("/adult/a1/decide", **ok)[0], 303)  # Leo's teacher may
        self.assertNotIn(form_for(c), b.get("/adult/a5")[2])

    def test_a_no_is_remembered_for_the_next_qloo_request(self):
        app = self.app()
        b = Browser(app)
        c = self.plan(app, "s1")["pending"][0]
        b.post("/adult/a5/decide", sid="s1", domain=c.domain, name=c.name, ok=0)
        (session,) = app.sessions.values()
        self.assertEqual(session.approvals.status("s1", c.domain, c.name), "rejected")
        if c.entity_id:
            self.assertIn(c.entity_id, session.approvals.excluded_ids("s1"))
        self.assertEqual(self.raw["s1"]["rejected"], [])


class TestCheckIns(WebCase):
    def test_the_childs_faces_reach_the_teachers_report(self):
        app = self.app()
        b = Browser(app)
        n = len(self.plan(app, "s1")["steps"])
        for i in range(n):
            status, headers, _ = b.post("/child/s1/checkin", step=i, face="dificil")
            self.assertEqual(status, 303)
        self.assertEqual(b.get("/child/s1")[2].count("class='on'"), n)
        _, _, body = b.get("/adult/a1")
        self.assertIn("What was hard:", body)
        self.assertNotIn("What worked:", body)
        self.assertIn(esc(t("en", "web.checkins", alias="Leo", told=n, n=n)), body)
        other = Browser(app).get("/adult/a1")[2]
        self.assertIn(esc(t("en", "web.checkins", alias="Leo", told=0, n=n)), other)

    def test_bad_check_ins_are_refused(self):
        app = self.app()
        b = Browser(app)
        n = len(self.plan(app, "s1")["steps"])
        for form in ({"step": n, "face": "bien"}, {"step": "x", "face": "bien"},
                     {"step": "-1", "face": "bien"}, {"step": "\u00b2", "face": "bien"},
                     {"step": 0, "face": "feliz"}, {"face": "bien"}):
            self.assertEqual(b.post("/child/s1/checkin", **form)[0], 400, form)
        self.assertEqual(b.post("/child/zz/checkin", step=0, face="bien")[0], 404)
        self.assertNotIn("class='on'", b.get("/child/s1")[2])

    def test_reset_starts_the_visitor_over(self):
        app = self.app()
        b = Browser(app)
        c = self.plan(app, "s1")["pending"][0]
        b.post("/child/s1/checkin", step=0, face="bien")
        b.post("/adult/a5/decide", sid="s1", domain=c.domain, name=c.name, ok=1)
        self.assertEqual(b.post("/reset")[0], 303)
        self.assertIn(form_for(c), b.get("/adult/a5")[2])
        self.assertNotIn("class='on'", b.get("/child/s1")[2])


class TestSafety(WebCase):
    def test_text_is_escaped(self):
        app = self.app()
        self.plan(app, "s1")["steps"][1].first_then = "<script>alert(1)</script>"
        body = Browser(app).get("/child/s1")[2]
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        body = Browser(app).get("/", lang="<b>x</b>")[2]
        self.assertNotIn("<b>x</b>", body)
        self.assertIn("<html lang='en'>", body)

    def test_headers_cookie_and_no_scripts(self):
        _, headers, body = Browser(self.app()).get("/child/s1")
        h = dict(headers)
        self.assertEqual(h["Content-Security-Policy"], CSP)
        self.assertIn("default-src 'none'", CSP)
        self.assertNotIn("script-src", CSP)
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertTrue(h["Set-Cookie"].startswith(SESSION_COOKIE + "="))
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Lax", h["Set-Cookie"])
        self.assertNotIn("<script", body.lower())

    def test_sessions_expire_and_forged_ones_are_replaced(self):
        now = [0.0]
        app = self.app(clock=lambda: now[0])
        b = Browser(app)
        b.get("/")
        first = b.cookie
        now[0] += 60
        b.get("/")
        self.assertEqual(b.cookie, first)
        now[0] += SESSION_TTL + 1
        b.get("/")
        self.assertNotEqual(b.cookie, first)
        b.cookie = SESSION_COOKIE + "=forged-token"
        b.get("/")
        self.assertNotIn("forged-token", b.cookie)

    def test_memory_is_capped(self):
        app = self.app()
        with mock.patch("hermano.web.MAX_SESSIONS", 3):
            for _ in range(5):
                Browser(app).get("/")
        self.assertEqual(len(app.sessions), 3)

    def test_health_checks_do_not_fill_memory(self):
        app = self.app()
        for _ in range(5):
            status, headers, body = app.handle("GET", "/healthz", {}, {}, "")
            self.assertEqual(status, 200)
            self.assertNotIn("Set-Cookie", dict(headers))
        self.assertEqual(len(app.sessions), 0)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_privacy_page_shows_what_went_to_qloo_and_no_child(self):
        app = self.app()
        body = Browser(app).get("/privacy")[2]
        sent = app.plans("en")["sent"]
        self.assertTrue(sent)
        for line in sent:
            self.assertIn(esc(line), body)
        for alias in self.school.roster():
            self.assertNotRegex(body, rf"\b{alias}\b")

    def test_unknown_paths_and_wrong_methods(self):
        b = Browser(self.app())
        self.assertEqual(b.get("/nope")[0], 404)
        self.assertEqual(b.get("/child/zz")[0], 404)
        self.assertEqual(b.get("/adult/zz")[0], 404)
        self.assertEqual(b.get("/child/s1/other")[0], 404)
        self.assertEqual(b.post("/")[0], 405)
        self.assertEqual(b.get("/child/s1/checkin")[0], 405)
        self.assertEqual(b.get("/adult/a5/decide")[0], 405)
        self.assertEqual(b.get("/reset")[0], 405)


class TestServer(WebCase):
    def test_the_real_server_answers(self):
        httpd = make_server(self.app(), "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + "/child/s1", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("Hermano", r.read().decode("utf-8"))
            self.assertIn(SESSION_COOKIE, r.headers.get("Set-Cookie", ""))
        req = urllib.request.Request(base + "/reset", data=b"x" * 5000, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(req, timeout=5)
        self.assertEqual(cm.exception.code, 413)
        cm.exception.close()


class TestCli(WebCase):
    def test_serve_with_the_agent_needs_a_key(self):
        from hermano.__main__ import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch("hermano.web.serve") as srv:
            rc = main(["serve", "--planner", "llm"])
        self.assertEqual(rc, 2)
        self.assertIn("GROQ_API_KEY", err.getvalue())
        srv.assert_not_called()

    def test_make_app_picks_the_planners_from_the_environment(self):
        rules = make_app(DATA, cache_dir=self.cache)
        self.assertEqual([n for n, _ in rules.planners], ["rules"])
        os.environ["GROQ_API_KEY"] = "test-not-a-real-key"
        agent = make_app(DATA, cache_dir=self.cache)
        self.assertEqual([n for n, _ in agent.planners], ["rules", "llm"])
        self.assertNotEqual(rules.tag, agent.tag)
        forced = make_app(DATA, planner="rules", cache_dir=self.cache)
        self.assertEqual([n for n, _ in forced.planners], ["rules"])


class TestDeploy(unittest.TestCase):
    """The Render Blueprint: free plan, the real start command, no secret values."""

    def test_blueprint(self):
        from tests.helpers import ROOT
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("plan: free", text)
        self.assertIn("startCommand: python -m hermano serve --host 0.0.0.0", text)
        self.assertIn("healthCheckPath: /healthz", text)
        key = text.index("key: GROQ_API_KEY")
        self.assertTrue(text[key:].split("\n")[1].strip() == "sync: false")
        self.assertNotRegex(text, r"gsk_[A-Za-z0-9]|QLOO_API_KEY\s*\n\s*value")
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.12")

    def test_the_start_command_parses(self):
        import argparse
        from hermano import __main__ as cli
        seen = {}
        with mock.patch.dict(os.environ, {"PORT": "10000"}), \
                mock.patch("hermano.web.serve", lambda app, host, port: seen.update(host=host, port=port)), \
                mock.patch("hermano.web.make_app", lambda data, planner: mock.Mock(has_llm=False)):
            rc = cli.main(["serve", "--host", "0.0.0.0"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen, {"host": "0.0.0.0", "port": 10000})
