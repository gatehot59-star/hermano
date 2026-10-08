"""Core tests. Run with: python -m unittest -v"""
import json
import re
import unittest
from pathlib import Path

from hermano.agent import (FORBIDDEN_RE, REDACTED, Rotation, build_profile, plan_day,
                           report_for, run_day, scrub)
from hermano.models import Interest, Student, load_school
from hermano.qloo import BASE_URL, MockQloo, QlooClient, urn

DATA = Path(__file__).resolve().parent.parent / "data"


def word_in(word, text):
    return re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE) is not None


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.school = load_school(DATA / "demo_school.json")
        self.qloo = MockQloo(DATA / "mock_taste.json")


class TestQlooClient(unittest.TestCase):
    def test_urn_rejects_unsupported_type(self):
        with self.assertRaises(ValueError):
            urn("music")

    def test_client_needs_a_key(self):
        with self.assertRaises(ValueError):
            QlooClient("")

    def test_insights_is_a_get_with_key_header_on_hackathon_url(self):
        seen = {}

        def opener(req, timeout):
            seen["req"] = req
            return FakeResp({"success": True, "results": {"entities": [{"name": "Matilda", "entity_id": "E1"}]}})

        out = QlooClient("k-test", opener=opener).insights("book", ["A", "B"], take=3)
        req = seen["req"]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("X-api-key"), "k-test")
        self.assertTrue(req.full_url.startswith(BASE_URL + "/v2/insights?"))
        self.assertIn("filter.type=urn%3Aentity%3Abook", req.full_url)
        self.assertIn("signal.interests.entities=A%2CB", req.full_url)
        self.assertEqual(out, [{"name": "Matilda", "kind": "book", "id": "E1"}])

    def test_search_returns_first_entity_id(self):
        def opener(req, timeout):
            return FakeResp({"results": [{"name": "Bluey", "entity_id": "Q1"}]})

        self.assertEqual(QlooClient("k", opener=opener).search("Bluey", "tv_show"), "Q1")


class TestPrivacy(Base):
    def test_only_interest_names_reach_qloo(self):
        for st in self.school.students.values():
            build_profile(st, self.qloo)
        sent = "\n".join(self.qloo.sent)
        self.assertTrue(sent)
        for st in self.school.students.values():
            self.assertFalse(word_in(st.alias, sent), st.alias)
            self.assertFalse(word_in(st.id, sent), st.id)
            for sup in st.supports:
                self.assertNotIn(sup.lower(), sent.lower())


class TestPlan(Base):
    def test_every_block_has_a_first_then_cue_and_a_reward(self):
        records, _ = run_day(self.school, self.qloo, Rotation())
        for rec in records.values():
            for s in rec["plan"]["steps"]:
                self.assertTrue(s.first_then.startswith("Primero: "))
                self.assertTrue(s.reward)
                self.assertTrue(s.calm)

    def test_a_warning_before_every_change(self):
        records, _ = run_day(self.school, self.qloo, Rotation())
        steps = records["s1"]["plan"]["steps"]
        self.assertEqual(steps[0].warning, "")
        self.assertTrue(all(s.warning for s in steps[1:]))

    def test_motivators_come_from_several_domains(self):
        records, _ = run_day(self.school, self.qloo, Rotation())
        domains = {s.reward_domain for s in records["s1"]["plan"]["steps"]}
        self.assertGreaterEqual(len(domains), 3)

    def test_rotation_keeps_rewards_fresh_across_days(self):
        rotation, profiles, seen = Rotation(), None, []
        for day in range(3):
            records, profiles = run_day(self.school, self.qloo, rotation, day, profiles)
            seen += [s.reward for s in records["s1"]["plan"]["steps"]]
        first_cycle = seen[:len(profiles["s1"].pool())]
        self.assertEqual(len(first_cycle), len(set(first_cycle)))

    def test_the_child_picks_hermano_or_hermana(self):
        for sid, expected in (("s1", "Soy tu hermano"), ("s2", "Soy tu hermana")):
            st = self.school.students[sid]
            plan = plan_day(st, self.school.schedules[sid], build_profile(st, self.qloo), Rotation())
            self.assertTrue(plan["greeting"].startswith(expected))

    def test_a_child_without_qloo_matches_still_gets_a_plan(self):
        st = Student(id="x", alias="Nadie", age=9, grade="4°", persona="hermana", supports=[],
                     interests=[Interest("Algo que no existe", "tv_show")])
        plan = plan_day(st, self.school.schedules["s1"], build_profile(st, self.qloo), Rotation())
        self.assertTrue(all(s.reward for s in plan["steps"]))


class TestReports(Base):
    def setUp(self):
        super().setUp()
        self.records, _ = run_day(self.school, self.qloo, Rotation())

    def report(self, adult_id):
        return report_for(self.school.adults[adult_id], self.school, self.records)

    def test_each_role_gets_a_different_report(self):
        texts = {self.report(a) for a in ("a1", "a4", "a5", "a8")}
        self.assertEqual(len(texts), 4)

    def test_adults_only_see_their_own_children(self):
        t = self.report("a1")
        self.assertTrue(word_in("Leo", t))
        self.assertFalse(word_in("Sofi", t))
        self.assertFalse(word_in("Mati", t))
        t = self.report("a9")
        self.assertTrue(word_in("Sofi", t))
        self.assertFalse(word_in("Leo", t))

    def test_no_health_or_diagnosis_words_anywhere(self):
        for adult in self.school.adults.values():
            self.assertIsNone(FORBIDDEN_RE.search(report_for(adult, self.school, self.records)), adult.id)
        for rec in self.records.values():
            plan = rec["plan"]
            text = " ".join([plan["greeting"]] + [s.first_then + s.warning + s.reading for s in plan["steps"]])
            self.assertIsNone(FORBIDDEN_RE.search(text))

    def test_scrub_removes_health_words(self):
        out = scrub("Tiene TEA y TDAH, con diagnóstico de autismo. Le gusta el teatro.")
        self.assertIsNone(FORBIDDEN_RE.search(out))
        self.assertIn(REDACTED, out)
        self.assertIn("teatro", out)

    def test_family_report_is_plain_language(self):
        t = self.report("a5")
        for jargon in ("motivador", "autoinforme", "|"):
            self.assertNotIn(jargon, t)
        self.assertIn("Para charlar en casa", t)

    def test_principal_report_is_aggregate(self):
        t = self.report("a4")
        self.assertIn("Chicos acompañados: 3", t)
        self.assertIn("Momentos registrados: 19", t)


if __name__ == "__main__":
    unittest.main()
