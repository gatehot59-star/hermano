"""The agent planner against a fake Groq: the model chooses, Hermano's rules decide."""
import json
import unittest

from hermano import llm
from hermano.agent import Rotation, build_profile, plan_day, report_for, run_day
from hermano.approvals import Approvals
from hermano.models import SUPPORTS, load_school, support_text
from hermano.planner import MAX_SUGGESTIONS, forbidden_names, llm_plan_day
from hermano.qloo import MockQloo
from tests.helpers import DATA, Recorder, http_error


def calls(*pairs):
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(pairs)]}}]}


def done():
    return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def script(*answers):
    seq = list(answers)
    return lambda req: seq.pop(0) if len(seq) > 1 else seq[0]


def body(req):
    return json.loads(req.data.decode("utf-8"))


REASON = "STUDENT_1 loves Peppa Pig, and Puffin Rock is a calm show about animals."
# Leo's day: B1 arrival, B2 Language, B3 recess, B4 Math, B5 move to Music, B6 Music, B7 dismissal.
# Approved, in pool order: M1 Where the Wild Things Are, M2 Canticuenticos, M3 Peppa Pig,
# M4 Animal Crossing, M5 Toy Story. Candidates: C1 The Little Prince, C2 Magdalena Fleitas,
# C3 Puffin Rock, ...
GOOD = [{"block_id": "B2", "motivator_id": "M5"}, {"block_id": "B4", "motivator_id": "M2"},
        {"block_id": "B6", "motivator_id": "M1"}]


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.school = load_school(DATA / "demo_school.json")
        self.qloo = MockQloo(DATA / "mock_taste.json")
        self.approvals = Approvals.load(DATA / "demo_approvals.json")
        self.leo = self.school.students["s1"]
        self.blocks = self.school.schedules["s1"]
        self.profile = build_profile(self.leo, self.qloo, self.school.roster(), self.approvals)

    def run_with(self, *answers, lang="en", rotation=None):
        self.net = Recorder(script(*answers))
        model = llm.GroqChat("k", opener=self.net, sleep=lambda s: None,
                             forbidden=forbidden_names(self.school))
        self.rotation = rotation or Rotation()
        return llm_plan_day(self.leo, self.blocks, self.profile, self.rotation, self.approvals,
                            lang, llm=model)

    def good_run(self, **kw):
        return self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": GOOD, "suggestions": [
                                 {"candidate_id": "C3", "reason": REASON}]})),
                             done(), **kw)

    def test_without_a_model_the_rule_plan_runs(self):
        plan = llm_plan_day(self.leo, self.blocks, self.profile, Rotation(), self.approvals)
        self.assertEqual(plan["planner"], "rules")
        self.assertEqual(plan["suggested"], [])

    def test_the_childs_own_favorites_are_motivators_for_the_model_too(self):
        # M6 is Bluey: Leo's own favorite, typed by an adult, after the five approved ideas.
        plan = self.run_with(calls(("get_motivators", {})),
                             calls(("submit_plan", {"rewards": [{"block_id": "B2", "motivator_id": "M6"}]})),
                             done())
        self.assertEqual(plan["planner"], "llm")
        self.assertEqual(plan["steps"][1].reward_item, "Bluey")
        tool = [m for m in body(self.net.requests[-1])["messages"] if m["role"] == "tool"][0]
        self.assertIn("Bluey", [m["title"] for m in json.loads(tool["content"])["approved"]])

    def test_the_model_cannot_give_the_same_reward_twice_in_a_day(self):
        twice = [{"block_id": "B2", "motivator_id": "M5"}, {"block_id": "B4", "motivator_id": "M5"}]
        plan = self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": twice})), done())
        answer = json.loads(body(self.net.requests[2])["messages"][-1]["content"])
        self.assertEqual(answer["accepted_rewards"], 1)
        self.assertIn("already used today", answer["problems"][0])
        self.assertIn("same motivator twice in one day", body(self.net.requests[0])["messages"][0]["content"])
        items = [s.reward_item for s in plan["steps"] if s.reward_item]
        self.assertEqual(plan["steps"][1].reward_item, "Toy Story")
        self.assertEqual(len(items), len(set(items)), items)

    def test_a_block_the_model_left_out_does_not_repeat_its_choice(self):
        # The rule plan puts Canticuenticos after B4; the model puts it after B2 and stops there.
        plan = self.run_with(calls(("get_motivators", {})),
                             calls(("submit_plan", {"rewards": [{"block_id": "B2", "motivator_id": "M2"}]})),
                             done())
        got = [s.reward_item for s in plan["steps"] if s.reward_item]
        self.assertEqual(got[0], "Canticu\u00e9nticos")
        self.assertEqual(len(got), len(set(got)), got)
        self.assertEqual(plan["steps"][3].reward_item, "Where the Wild Things Are")
        self.assertIn("Where the Wild Things Are", plan["steps"][3].first_then)

    def test_the_model_chooses_among_approved_ideas(self):
        plan = self.good_run()
        self.assertEqual(plan["planner"], "llm")
        got = {i: s.reward_item for i, s in enumerate(plan["steps"]) if s.reward_item}
        self.assertEqual(got, {1: "Toy Story", 3: "Canticu\u00e9nticos", 5: "Where the Wild Things Are"})
        self.assertIn("Toy Story", plan["steps"][1].first_then)

    def test_rewards_after_recess_or_unapproved_ideas_are_refused(self):
        bad = [{"block_id": "B3", "motivator_id": "M1"}, {"block_id": "B2", "motivator_id": "C1"},
               {"block_id": "B4", "motivator_id": "M2"}]
        plan = self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": bad})), done())
        answer = json.loads(body(self.net.requests[2])["messages"][-1]["content"])
        self.assertEqual(answer["accepted_rewards"], 1)
        self.assertEqual(len(answer["problems"]), 2)
        self.assertEqual(plan["steps"][2].reward_item, "")  # recess
        self.assertEqual(plan["steps"][3].reward_item, "Canticu\u00e9nticos")
        approved = {c.name for c in self.profile.pool()
                    if self.approvals.status("s1", c.domain, c.name) == "approved"}
        for s in plan["steps"]:
            if s.reward_item:
                self.assertIn(s.reward_item, approved)

    def test_suggestions_reach_adults_not_the_child(self):
        plan = self.good_run()
        self.assertEqual([s["candidate"].name for s in plan["suggested"]], ["Puffin Rock"])
        self.assertTrue(plan["suggested"][0]["reason"].startswith("Leo loves"))
        records = {"s1": {"plan": plan, "checkins": []}}
        teacher = report_for(self.school.adults["a1"], self.school, records)
        family = report_for(self.school.adults["a5"], self.school, records)
        for text in (teacher, family):
            self.assertIn("Puffin Rock", text)
            self.assertIn("calm show about animals", text)
        child = " ".join([plan["greeting"]] + [s.first_then + s.warning for s in plan["steps"]])
        self.assertNotIn("Puffin Rock", child)
        self.assertNotIn("calm show", child)

    def test_no_name_id_or_support_goes_to_the_model(self):
        self.good_run()
        sent = " ".join(r.data.decode("utf-8") for r in self.net.requests)
        self.assertIn("STUDENT_1", sent)
        for name in forbidden_names(self.school):
            self.assertNotIn(name, sent)
        self.assertNotIn('"s1"', sent)
        for code in SUPPORTS:
            self.assertNotIn(code, sent)
            self.assertNotIn(support_text(code, "en"), sent)

    def test_model_down_keeps_the_rule_plan(self):
        plan = self.run_with(http_error(llm.GROQ_URL, 503))
        rules = plan_day(self.leo, self.blocks, self.profile, Rotation(), self.approvals)
        self.assertEqual(plan["planner"], "rules")
        self.assertEqual(plan["planner_note"], "LLMUnavailable")
        self.assertEqual([s.reward_item for s in plan["steps"]], [s.reward_item for s in rules["steps"]])

    def test_a_health_word_from_the_model_stops_it(self):
        plan = self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": GOOD, "suggestions": [
                                 {"candidate_id": "C3", "reason": "good for his ADHD"}]})), done())
        self.assertEqual(plan["planner"], "rules")
        self.assertEqual(plan["planner_note"], "UnsafeText")
        self.assertEqual(plan["suggested"], [])
        self.assertEqual(len(self.net.requests), 2)  # nothing left after the reason was written

    def test_a_reason_with_contact_digits_is_refused_and_the_plan_goes_on(self):
        plan = self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": GOOD, "suggestions": [
                                 {"candidate_id": "C3", "reason": "ask at 555 123"}]})), done())
        answer = json.loads(body(self.net.requests[2])["messages"][-1]["content"])
        self.assertEqual(len(answer["problems"]), 1)
        self.assertEqual(plan["planner"], "llm")
        self.assertEqual(plan["suggested"], [])

    def test_at_most_two_suggestions(self):
        many = [{"candidate_id": c, "reason": "fits the tastes"} for c in ("C1", "C2", "C3")]
        plan = self.run_with(calls(("get_day", {}), ("get_motivators", {})),
                             calls(("submit_plan", {"rewards": GOOD, "suggestions": many})), done())
        self.assertEqual(len(plan["suggested"]), MAX_SUGGESTIONS)

    def test_rotation_remembers_what_the_model_chose(self):
        self.good_run()
        self.assertEqual(self.rotation.used["s1"], [("movie", "Toy Story"), ("artist", "Canticu\u00e9nticos"),
                                                    ("book", "Where the Wild Things Are")])

    def test_spanish_reports_get_the_reason_too(self):
        plan = self.good_run(lang="es")
        self.assertIn("Spanish", body(self.net.requests[0])["messages"][0]["content"])
        records = {"s1": {"plan": plan, "checkins": []}}
        self.assertIn("Hermano sugiere", report_for(self.school.adults["a1"], self.school, records, "es"))

    def test_run_day_takes_the_planner(self):
        seen = []

        def fake(student, blocks, profile, rotation, approvals, lang):
            seen.append(student.id)
            return plan_day(student, blocks, profile, rotation, approvals, lang)
        run_day(self.school, self.qloo, Rotation(), self.approvals, planner=fake)
        self.assertEqual(sorted(seen), ["s1", "s2", "s3"])


if __name__ == "__main__":
    unittest.main()
