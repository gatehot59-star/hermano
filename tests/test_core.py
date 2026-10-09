"""Plans, reports, approvals and the age gate. Run with: python -m unittest -v"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from hermano.__main__ import main
from hermano.agent import WORK_KINDS, Rotation, build_profile, motivators, plan_day, report_for, run_day
from hermano.approvals import APPROVED, PENDING, REJECTED, Approvals
from hermano.guard import contains_health
from hermano.i18n import TEXT, q, t
from hermano.models import (EXERCISE_KINDS, EXERCISES, SUPPORTS, Block, Interest, Student, exercise_text,
                            load_school, support_text)
from hermano.qloo import MockQloo, QlooClient
from tests.helpers import DATA, ROOT, Recorder, http_error


def word_in(word, text):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE) is not None


def real_client(rec):
    return QlooClient("k-test", opener=rec, sleep=lambda s: None)


class Base(unittest.TestCase):
    def setUp(self):
        self.school = load_school(DATA / "demo_school.json")
        self.qloo = MockQloo(DATA / "mock_taste.json")
        self.approvals = Approvals.load(DATA / "demo_approvals.json")

    def day(self, lang="en", day=0, rotation=None, profiles=None):
        return run_day(self.school, self.qloo, rotation or Rotation(), self.approvals, day, profiles, lang)

    def report(self, adult_id, records, lang="en"):
        return report_for(self.school.adults[adult_id], self.school, records, lang)


class TestPrivacy(Base):
    def test_only_interest_names_reach_qloo(self):
        self.day()
        sent = "\n".join(self.qloo.sent)
        self.assertTrue(sent)
        for st in self.school.students.values():
            self.assertFalse(word_in(st.alias, sent), st.alias)
            self.assertFalse(word_in(st.id, sent), st.id)
            for code in st.supports:
                self.assertNotIn(code, sent)
                for lang in ("en", "es"):
                    self.assertNotIn(support_text(code, lang).lower(), sent.lower())
            for code in st.exercises:
                self.assertNotIn(code, sent)
                for lang in ("en", "es"):
                    self.assertNotIn(exercise_text(code, lang).lower(), sent.lower())

    def test_free_text_with_names_or_health_never_reaches_qloo(self):
        rec = Recorder()
        kid = Student(id="s1", alias="Leo", age=8, grade="3", persona="hermano", supports=[],
                      interests=[Interest("Leo Gomez y su TEA", "tv_show"), Interest("Sofi y Leo", "tv_show"),
                                 Interest("Bluey", "tv_show")])
        prof = build_profile(kid, real_client(rec), self.school.roster(), self.approvals)
        urls = rec.urls()
        self.assertTrue(any("Bluey" in u for u in urls))
        for u in urls:
            for bad in ("Leo", "Sofi", "TEA"):
                self.assertFalse(word_in(bad, u), (bad, u))
        self.assertEqual(sorted(prof.refused), ["child name", "health detail"])

    def test_supports_come_from_the_catalog_only(self):
        raw = json.loads((DATA / "demo_school.json").read_text(encoding="utf-8"))
        raw["students"][0]["supports"] = ["toma metilfenidato a las 10"]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "school.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                load_school(p)
            self.assertNotIn("metilfenidato", str(cm.exception))

    def test_unsafe_interests_stop_the_school_from_loading(self):
        raw = json.loads((DATA / "demo_school.json").read_text(encoding="utf-8"))
        raw["students"][0]["interests"].append({"name": "lo que mira Sofi", "kind": "tv_show"})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "school.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_school(p)


class TestReports(Base):
    def test_each_role_gets_a_different_report(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            self.assertEqual(len({self.report(a, records, lang) for a in ("a1", "a4", "a5", "a8")}), 4)

    def test_adults_only_see_their_own_children(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            r = self.report("a1", records, lang)
            self.assertTrue(word_in("Leo", r))
            self.assertFalse(word_in("Sofi", r))
            self.assertFalse(word_in("Mati", r))
            r = self.report("a9", records, lang)
            self.assertTrue(word_in("Sofi", r))
            self.assertFalse(word_in("Leo", r))

    def test_health_words_never_reach_a_report_even_if_typed(self):
        # An adult typed health words into a class name and a topic: no report may carry them.
        self.school.schedules["s1"][1].activity = {"en": "Language (ADHD support)", "es": "Lengua (apoyo por TEA)"}
        self.school.schedules["s2"][1].topic = {"en": "the solar system and autism", "es": "el sistema solar y el autismo"}
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            for adult in self.school.adults.values():
                self.assertFalse(contains_health(self.report(adult.id, records, lang)), (lang, adult.id))

    def test_family_report_is_plain_language(self):
        for lang, jargon in (("es", ("motivador", "autoinforme", "|")), ("en", ("motivator", "self-report", "|"))):
            records, _ = self.day(lang)
            r = self.report("a5", records, lang)
            for j in jargon:
                self.assertNotIn(j, r)
        records, _ = self.day("es")
        self.assertIn("Para charlar en casa", self.report("a5", records, "es"))

    def test_principal_report_is_aggregate(self):
        records, _ = self.day("es")
        r = self.report("a4", records, "es")
        self.assertIn("Chicos acompa\u00f1ados: 3", r)
        self.assertIn("Momentos registrados: 19", r)
        records, _ = self.day("en")
        self.assertIn("Children supported: 3", self.report("a4", records, "en"))


class TestPlan(Base):
    def test_rewards_only_after_classes_never_after_recess_or_dismissal(self):
        records, _ = self.day()
        for rec in records.values():
            steps = rec["plan"]["steps"]
            for i, s in enumerate(steps):
                self.assertTrue(s.first_then.startswith("First: "))
                if s.block.kind in WORK_KINDS:
                    self.assertTrue(s.reward, s.first_then)
                else:
                    self.assertEqual((s.reward, s.reward_item), ("", ""), s.first_then)
                    nxt = steps[i + 1].activity if i + 1 < len(steps) else "home"
                    self.assertTrue(s.first_then.endswith(f"Then: {nxt}."), s.first_then)

    def test_a_warning_before_every_change(self):
        records, _ = self.day()
        steps = records["s1"]["plan"]["steps"]
        self.assertEqual(steps[0].warning, "")
        self.assertTrue(all(s.warning for s in steps[1:]))

    def test_motivators_come_from_several_domains(self):
        rotation, profiles, domains = Rotation(), None, set()
        for day in range(2):
            records, profiles = self.day(day=day, rotation=rotation, profiles=profiles)
            domains |= {s.reward_domain for s in records["s1"]["plan"]["steps"] if s.reward_domain}
        self.assertGreaterEqual(len(domains), 3)

    def test_rotation_keeps_rewards_fresh_across_days(self):
        rotation, profiles, seen = Rotation(), None, []
        for day in range(3):
            records, profiles = self.day(day=day, rotation=rotation, profiles=profiles)
            seen += [s.reward_item for s in records["s1"]["plan"]["steps"] if s.reward_item]
        n = sum(1 for c in profiles["s1"].pool() if self.approvals.status("s1", c.domain, c.name) == APPROVED)
        self.assertGreaterEqual(n, 3)
        self.assertEqual(len(seen[:n]), len(set(seen[:n])))

    def test_a_qloo_idea_never_comes_twice_in_a_day(self):
        rotation, profiles = Rotation(), None
        for day in range(4):
            records, profiles = self.day(day=day, rotation=rotation, profiles=profiles)
            for sid, rec in records.items():
                favorites = {c.name for c in profiles[sid].own}
                items = [s.reward_item for s in rec["plan"]["steps"]
                         if s.reward_item and s.reward_item not in favorites]
                self.assertEqual(len(items), len(set(items)), (day, sid, items))

    def test_favorites_come_back_once_the_other_ideas_ran_out(self):
        # Decided by Jorge Abraham Mendieta, 2026-10-08: the child's favorites may repeat.
        records, _ = run_day(self.school, self.qloo, Rotation(), Approvals(), 0)
        leo = [s.reward_item for s in records["s1"]["plan"]["steps"] if s.block.kind in WORK_KINDS]
        self.assertEqual(leo, ["Bluey"] * len(leo))
        sofi = [s.reward_item for s in records["s2"]["plan"]["steps"] if s.block.kind in WORK_KINDS]
        self.assertEqual(sorted(sofi[:2]), ["Harry Potter", "Tini"])  # both before either repeats
        self.assertTrue(set(sofi[2:]) <= {"Harry Potter", "Tini"})

    def test_without_favorites_a_qloo_idea_is_not_repeated_the_child_chooses_with_the_teacher(self):
        st = Student(id="x", alias="Nadie", age=14, grade="2", persona="hermano", supports=[],
                     interests=[Interest("Lionel Messi", "person")])  # a person is never a reward
        approvals = Approvals({"x": {"approved": ["book:futbol\u00edsimos", "movie:kung fu panda"]}})
        prof = build_profile(st, self.qloo, ["Nadie"], approvals)
        self.assertEqual(prof.own, [])
        plan = plan_day(st, self.school.schedules["s1"], prof, Rotation(), approvals)
        got = [s.reward_item or s.reward for s in plan["steps"] if s.block.kind in WORK_KINDS]
        self.assertEqual(sorted(got[:2]), ["Futbol\u00edsimos", "Kung Fu Panda"])
        self.assertEqual(got[2:], [t("en", "reward.default")] * (len(got) - 2))

    def test_a_favorite_waits_while_new_approved_ideas_are_left(self):
        r = Rotation()
        pool = [("tv_show", "Fav"), ("book", "A"), ("movie", "B")]
        fav = {("tv_show", "Fav")}
        today = []
        for _ in range(4):
            today.append(r.pick("x", pool, avoid=[p for p in today if p not in fav]))
        self.assertEqual([n for _, n in today], ["Fav", "A", "B", "Fav"])

    def test_rewards_cycle_in_rounds_across_days_without_repeating_in_a_day(self):
        r = Rotation()
        pool = [("book", x) for x in "ABCD"]
        seq = []
        for day in range(3):  # three classes a day, four motivators
            today = []
            for _ in range(3):
                today.append(r.pick("x", pool, avoid=today))
            seq += [x for _, x in today]
        self.assertEqual("".join(seq), "ABCDABCDA")
        self.assertIsNone(r.pick("x", pool, avoid=pool))

    def test_the_child_picks_hermano_or_hermana(self):
        for lang, he, she in (("es", "Soy tu hermano", "Soy tu hermana"), ("en", "I'm your hermano", "I'm your hermana")):
            records, _ = self.day(lang)
            self.assertTrue(records["s1"]["plan"]["greeting"].startswith(he))
            self.assertTrue(records["s2"]["plan"]["greeting"].startswith(she))

    def test_a_child_without_qloo_matches_still_gets_a_plan(self):
        st = Student(id="x", alias="Nadie", age=9, grade="4", persona="hermana", supports=[],
                     interests=[Interest("Algo que no existe", "tv_show")])
        prof = build_profile(st, self.qloo, ["Nadie"], self.approvals)
        plan = plan_day(st, self.school.schedules["s1"], prof, Rotation(), self.approvals)
        self.assertTrue(all(s.reward for s in plan["steps"] if s.block.kind in WORK_KINDS))

    def test_no_health_words_in_the_demo_plans(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            for rec in records.values():
                plan = rec["plan"]
                text = " ".join([plan["greeting"]] + [s.first_then + s.warning + s.reading for s in plan["steps"]])
                self.assertFalse(contains_health(text))


class TestSafety(Base):
    def test_nothing_unapproved_reaches_the_child(self):
        for lang in ("en", "es"):
            records, profiles = self.day(lang)
            for sid, rec in records.items():
                steps = rec["plan"]["steps"]
                own = {(c.domain, c.name) for c in profiles[sid].own}
                for s in steps:
                    if s.reward_item and (s.reward_domain, s.reward_item) not in own:
                        self.assertEqual(self.approvals.status(sid, s.reward_domain, s.reward_item), APPROVED)
                child = " ".join(s.first_then + " " + " ".join(s.calm) for s in steps)
                pending = [c for c in profiles[sid].pool() if self.approvals.status(sid, c.domain, c.name) == PENDING]
                self.assertTrue(pending)
                for c in pending:
                    shown = c.name if c.domain == "artist" else q(lang, c.name)
                    self.assertFalse(word_in(shown, child), (sid, c.name))

    def test_new_ideas_wait_for_the_family_or_the_teacher(self):
        records, _ = self.day("en")
        first = records["s1"]["plan"]["pending"][0].name
        family, teacher, specialist = (self.report(a, records) for a in ("a5", "a1", "a8"))
        self.assertIn("waiting for your OK", family)
        self.assertIn(first, family)
        self.assertIn(first, teacher)
        self.assertNotIn("waiting for your OK", specialist)

    def test_only_the_childs_family_or_teacher_can_approve(self):
        a = self.school.adults
        for adult_id in ("a4", "a8", "a2", "a6"):  # principal, specialist, other teacher, other family
            with self.assertRaises(PermissionError):
                self.approvals.decide(a[adult_id], "s1", "book", "Elmer", True)
        self.approvals.decide(a["a1"], "s1", "book", "Elmer", True)
        self.assertEqual(self.approvals.status("s1", "book", "Elmer"), APPROVED)
        self.approvals.decide(a["a5"], "s1", "book", "Elmer", False, "E-elmer")
        self.assertEqual(self.approvals.status("s1", "book", "Elmer"), REJECTED)

    def test_a_no_is_sent_to_qloo_as_an_exclusion(self):
        self.approvals.decide(self.school.adults["a5"], "s1", "book", "Elmer", False, "E-elmer")
        rec = Recorder()
        build_profile(self.school.students["s1"], real_client(rec), self.school.roster(), self.approvals)
        insights = [u for u in rec.urls() if "/v2/insights" in u]
        self.assertTrue(insights)
        self.assertTrue(all("filter.exclude.entities=E-elmer" in u for u in insights))

    def test_games_rated_above_the_childs_age_are_never_proposed(self):
        prof = build_profile(self.school.students["s1"], self.qloo, self.school.roster(), self.approvals)  # Leo, 8
        names = {c.name for c in prof.pool()}
        for game in ("Terraria", "Roblox"):  # ESRB T, found by audit 2026-10-08-11
            self.assertNotIn(game, names)
            self.assertIn(("video_game", game), prof.blocked)
        self.assertIn("Animal Crossing: New Horizons", names)

    def test_movies_and_shows_are_asked_for_the_childs_age(self):
        rec = Recorder()
        build_profile(self.school.students["s1"], real_client(rec), self.school.roster(), self.approvals)
        by_type = {}
        for i in range(len(rec.requests)):
            p = rec.params(i)
            if "filter.type" in p:
                by_type[p["filter.type"][0]] = p
        self.assertEqual(by_type["urn:entity:movie"]["filter.content_rating"],
                         ["G,TV-Y,TV-G,PG,TV-Y7,TV-Y7-FV,TV-PG"])
        self.assertNotIn("PG-13", by_type["urn:entity:tv_show"]["filter.content_rating"][0].split(","))
        self.assertNotIn("filter.content_rating", by_type["urn:entity:videogame"])
        mati = build_profile(self.school.students["s3"], self.qloo, self.school.roster(), self.approvals)  # 14
        shows = {c.name for c in mati.pool() if c.domain == "tv_show"}
        self.assertNotIn("Ted Lasso", shows)  # TV-MA in the mock
        self.assertIn("One Piece", shows)  # TV-14 in the mock


def ideas_reply(by_kind, search=None):
    """A fake Qloo: /search finds every interest; insights answers by_kind[kind] (entity dicts)."""
    def reply(req):
        url = req.full_url
        if "/search" in url:
            name = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
            return {"results": [dict({"name": name, "entity_id": "E-" + name}, **(search or {}).get(name, {}))]}
        kind = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["filter.type"][0].rsplit(":", 1)[1]
        return {"results": {"entities": by_kind.get(kind, [])}}
    return reply


def ent(name, rating="", *tags):
    return {"name": name, "entity_id": "E-" + name, "properties": {"content_rating": rating},
            "tags": [{"name": t, "type": "urn:tag:genre"} for t in tags]}


class TestAgeGate(Base):
    """The age gate with answers shaped like the ones seen live on 2026-10-08."""

    def profile(self, sid, by_kind, ratings=None, search=None):
        st = self.school.students[sid]
        return build_profile(st, real_client(Recorder(ideas_reply(by_kind, search))), self.school.roster(),
                             self.approvals, game_ratings=ratings or {})

    def names(self, prof, domain):
        return [c.name for c in prof.by_domain[domain]]

    def test_qloos_game_rating_blocks_games_above_the_childs_age(self):
        games = {"videogame": [ent("Genshin Impact", "T"), ent("Stardew Valley", "E10+"), ent("Goat Simulator", "M")]}
        sofi = self.profile("s2", games)  # 11: Genshin Impact reached her plan live on 2026-10-08
        self.assertEqual(self.names(sofi, "video_game"), ["Stardew Valley"])
        self.assertEqual(sofi.why_blocked["video_game:Genshin Impact"], "rating")
        mati = self.profile("s3", games)  # 14
        self.assertEqual(self.names(mati, "video_game"), ["Genshin Impact", "Stardew Valley"])

    def test_unrated_games_are_not_proposed_under_13(self):
        games = {"videogame": [ent("Grim Dawn"), ent("Among Us", "E10+")]}
        sofi = self.profile("s2", games)
        self.assertEqual(self.names(sofi, "video_game"), ["Among Us"])
        self.assertEqual(sofi.why_blocked["video_game:Grim Dawn"], "unrated")
        self.assertIn("Grim Dawn", self.names(self.profile("s3", games), "video_game"))  # 14: an adult decides

    def test_the_stricter_of_qloo_and_the_team_table_wins(self):
        games = {"videogame": [ent("ROBLOX", "E10+")]}
        self.assertEqual(self.names(self.profile("s2", games, {"roblox": "T"}), "video_game"), [])
        self.assertEqual(self.names(self.profile("s2", {"videogame": [ent("ROBLOX", "T")]}, {"roblox": "E10+"}),
                                    "video_game"), [])
        self.assertEqual(self.names(self.profile("s2", games, {}), "video_game"), ["ROBLOX"])

    def test_music_and_books_under_13_need_a_childrens_tag(self):
        # Live 2026-10-08: ChillinIT reached Leo (8); Raffi carries Family-Oriented, ChillinIT does not.
        ideas = {"artist": [ent("ChillinIT", "", "Rap", "Youthful"), ent("Raffi", "", "Childrens", "Family-Oriented")],
                 "book": [ent("No Tags"), ent("Elmer", "", "Picture Books"), ent("Teen Novel", "", "Young Adult")]}
        leo = self.profile("s1", ideas)
        self.assertEqual(self.names(leo, "artist"), ["Raffi"])
        self.assertEqual(self.names(leo, "book"), ["Elmer"])
        self.assertEqual(leo.why_blocked["artist:ChillinIT"], "not_for_children")
        mati = self.profile("s3", ideas)  # 14: they go to an adult for approval
        self.assertEqual(self.names(mati, "artist"), ["ChillinIT", "Raffi"])
        self.assertEqual(self.names(mati, "book"), ["No Tags", "Elmer", "Teen Novel"])

    def test_adult_tags_keep_a_book_away_from_every_child(self):
        mati = self.profile("s3", {"book": [ent("Grown Up", "", "Erotica")]})
        self.assertEqual(self.names(mati, "book"), [])

    def test_a_movie_outside_the_childs_ratings_is_dropped_even_if_qloo_sends_it(self):
        ideas = {"movie": [ent("Toy Story 4", "G"), ent("Grown Up Film", "R"), ent("One Day")],
                 "tv_show": [ent("Skins", "TV-MA"), ent("Arthur", "TV-Y")]}
        leo = self.profile("s1", ideas)
        self.assertEqual(self.names(leo, "movie"), ["Toy Story 4"])
        self.assertEqual(self.names(leo, "tv_show"), ["Arthur"])
        self.assertEqual(leo.why_blocked["movie:One Day"], "unrated")


class TestMovement(Base):
    """Movement breaks with the child's own music and shows, only where the adults agreed them."""

    def moves(self, plan):
        return [s.movement for s in plan["steps"] if s.movement]

    def test_movement_breaks_only_in_classes_and_only_with_the_agreed_support(self):
        records, _ = self.day()
        sofi = records["s2"]["plan"]["steps"]  # Sofi's adults agreed movement_break
        for s in sofi:
            self.assertEqual(bool(s.movement), s.block.kind == "class", (s.activity, s.movement))
        for sid in ("s1", "s3"):  # Leo and Mati did not
            self.assertEqual(self.moves(records[sid]["plan"]), [], sid)

    def test_she_dances_to_her_own_music_first(self):
        records, profiles = self.day()
        moves = self.moves(records["s2"]["plan"])
        self.assertEqual(moves[0], t("en", "move.dance", x="Tini"))  # her own favorite
        self.assertTrue(moves[1].startswith("Movement break: move like a character from "))
        self.assertEqual(len(moves), len(set(moves)))

    def test_only_approved_or_favorite_titles_go_into_a_movement_break(self):
        records, profiles = self.day()
        allowed = {c.name for c in motivators(self.school.students["s2"], profiles["s2"], self.approvals)}
        text = " ".join(self.moves(records["s2"]["plan"]))
        for c in records["s2"]["plan"]["pending"]:
            self.assertFalse(word_in(c.name, text), c.name)
        self.assertTrue(any(word_in(n, text) for n in allowed))

    def test_without_tastes_the_moves_are_simple_classroom_moves(self):
        st = Student(id="x", alias="Nadie", age=9, grade="4", persona="hermana",
                     supports=["movement_break"], interests=[])
        prof = build_profile(st, self.qloo, ["Nadie"], Approvals())
        plan = plan_day(st, self.school.schedules["s1"], prof, Rotation(), Approvals(), "es")
        self.assertEqual(self.moves(plan)[0], t("es", "move.stretch"))
        self.assertTrue(all(m.startswith("Pausa para moverte: ") for m in self.moves(plan)))

    def test_a_show_becomes_a_way_to_move_when_there_is_no_music(self):
        st = Student(id="x", alias="Nadie", age=8, grade="3", persona="hermano",
                     supports=["movement_break"], interests=[Interest("Bluey", "tv_show")])
        prof = build_profile(st, self.qloo, ["Nadie"], Approvals())
        plan = plan_day(st, self.school.schedules["s1"], prof, Rotation(), Approvals())
        self.assertEqual(self.moves(plan)[0], t("en", "move.like", x=q("en", "Bluey")))

    def test_the_teacher_and_the_specialist_see_the_movement_breaks(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            teacher, therapist = self.report("a2", records, lang), self.report("a9", records, lang)
            self.assertIn(t(lang, "teacher.movement", items="").rstrip(), teacher)
            n = len(self.moves(records["s2"]["plan"]))
            self.assertIn(t(lang, "specialist.movement", n=n), therapist)
            self.assertNotIn(t(lang, "teacher.movement", items="").rstrip(), self.report("a1", records, lang))
            self.assertFalse(contains_health(teacher + therapist))


class TestReinforcement(Base):
    """Reinforcement activities for the teacher, built on the class topic and the child's tastes."""

    def ideas(self, plan):
        return [s.reinforce for s in plan["steps"] if s.reinforce]

    def test_every_class_gets_one_and_nothing_else_does(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            for sid, rec in records.items():
                for s in rec["plan"]["steps"]:
                    self.assertEqual(bool(s.reinforce), s.block.kind == "class", (sid, s.activity))
                    if s.reinforce:
                        self.assertIn(s.block.topic_name(lang), s.reinforce)

    def test_only_approved_or_favorite_titles_and_a_different_one_each_class(self):
        records, profiles = self.day()
        for sid, rec in records.items():
            st = self.school.students[sid]
            allowed = [c.name for c in motivators(st, profiles[sid], self.approvals)]
            ideas = self.ideas(rec["plan"])
            used = [[n for n in allowed if word_in(n, idea)] for idea in ideas]
            self.assertTrue(all(len(u) == 1 for u in used), (sid, ideas))
            self.assertEqual(len({u[0] for u in used}), len(used), sid)
            for c in rec["plan"]["pending"]:
                self.assertFalse(any(word_in(c.name, idea) for idea in ideas), (sid, c.name))

    def test_it_is_not_the_reward_of_the_same_class(self):
        records, _ = self.day()
        for sid, rec in records.items():
            for s in rec["plan"]["steps"]:
                if s.reinforce and s.reward_item:
                    self.assertFalse(word_in(s.reward_item, s.reinforce), (sid, s.activity))

    def test_it_follows_the_agreed_supports(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            leo, sofi, mati = (self.ideas(records[s]["plan"]) for s in ("s1", "s2", "s3"))
            one, split = t(lang, "reinforce.one_instruction"), t(lang, "reinforce.split_tasks")
            self.assertTrue(all(i.endswith(one) for i in leo))  # one instruction at a time
            self.assertTrue(all(i.endswith(split) for i in sofi))  # long tasks split into parts
            self.assertFalse(any(one in i or split in i for i in mati))

    def test_without_tastes_it_is_a_drawing_on_the_topic(self):
        st = Student(id="x", alias="Nadie", age=9, grade="4", persona="hermana", supports=[], interests=[])
        prof = build_profile(st, self.qloo, ["Nadie"], Approvals())
        plan = plan_day(st, self.school.schedules["s1"], prof, Rotation(), Approvals(), "es")
        self.assertEqual(self.ideas(plan)[0], t("es", "reinforce.line", activity="Lengua", topic="los animales",
                                                idea=t("es", "reinforce.default", topic="los animales")))

    def test_with_one_taste_the_next_class_gets_a_drawing_not_the_same_idea(self):
        # Real Qloo, 2026-10-09: Leo had only Bluey on day 1 and both classes got the same idea.
        st = Student(id="x", alias="Nadie", age=8, grade="3", persona="hermano", supports=[],
                     interests=[Interest("Bluey", "tv_show")])
        prof = build_profile(st, self.qloo, ["Nadie"], Approvals())
        ideas = self.ideas(plan_day(st, self.school.schedules["s1"], prof, Rotation(), Approvals()))
        self.assertIn(q("en", "Bluey"), ideas[0])
        self.assertEqual(ideas[1], t("en", "reinforce.line", activity="Math", topic="addition",
                                     idea=t("en", "reinforce.default", topic="addition")))

    def test_only_the_childs_teacher_reads_it(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            leo = self.ideas(records["s1"]["plan"])
            for idea in leo:
                self.assertIn(idea, self.report("a1", records, lang))
            for aid in ("a2", "a4", "a5", "a8"):  # another teacher, principal, family, specialist
                self.assertFalse(any(idea in self.report(aid, records, lang) for idea in leo), aid)


class TestExercises(Base):
    """What the child's specialist asked the child to practice: a closed catalog, once a day each,
    where it fits; the child says if she did it, and the specialist reads it."""

    def placed(self, plan):
        return [(s.block.kind, s.exercise) for s in plan["steps"] if s.exercise]

    def test_each_agreed_exercise_comes_once_a_day_where_it_fits(self):
        records, _ = self.day()
        for sid, rec in records.items():
            st = self.school.students[sid]
            got = self.placed(rec["plan"])
            self.assertTrue(st.exercises, sid)
            self.assertEqual(sorted(code for _, code in got), sorted(st.exercises), sid)
            for kind, code in got:
                self.assertIn(kind, EXERCISE_KINDS[code], (sid, code))

    def test_one_with_no_fitting_moment_is_left_out(self):
        st = Student(id="x", alias="Nadie", age=9, grade="4", persona="hermana", supports=[], interests=[],
                     exercises=["balance_one_foot", "hand_squeeze"])  # break only, class only
        blocks = [Block("08:00", "08:40", "Math", "class", "addition")]
        plan = plan_day(st, blocks, build_profile(st, self.qloo, ["Nadie"], Approvals()), Rotation(), Approvals())
        self.assertEqual(self.placed(plan), [("class", "hand_squeeze")])

    def test_the_catalog_is_closed_and_free_text_is_not_echoed(self):
        with self.assertRaises(ValueError) as cm:
            Student(id="x", alias="Nadie", age=9, grade="4", persona="hermana", supports=[], interests=[],
                    exercises=["tomar la pastilla a las 10"])
        self.assertNotIn("pastilla", str(cm.exception))
        raw = json.loads((DATA / "demo_school.json").read_text(encoding="utf-8"))
        raw["students"][0]["exercises"] = ["sesion de terapia con la doctora"]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "school.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_school(p)

    def test_the_catalog_has_no_health_words_and_both_languages(self):
        self.assertEqual(set(EXERCISES), set(EXERCISE_KINDS))
        for code, text in EXERCISES.items():
            self.assertEqual(set(text), {"en", "es"}, code)
            for lang in ("en", "es"):
                self.assertFalse(contains_health(text[lang]), (code, lang))

    def test_the_specialist_reads_what_the_child_said(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            for aid, sid in (("a8", "s1"), ("a8", "s3"), ("a9", "s2")):
                report = self.report(aid, records, lang)
                for s, c in zip(records[sid]["plan"]["steps"], records[sid]["checkins"]):
                    if s.exercise:
                        result = "done" if c.exercise_done else "not_done"
                        self.assertIn(t(lang, "specialist.ex_item", exercise=exercise_text(s.exercise, lang),
                                        activity=c.activity, result=t(lang, "specialist." + result)), report)
            both = [c.exercise_done for r in records.values() for c in r["checkins"] if c.exercise_done is not None]
            self.assertEqual(len(both), 8)
            self.assertIn(True, both)
            self.assertIn(False, both)

    def test_the_teacher_and_the_family_get_the_reminders_the_principal_does_not(self):
        for lang in ("en", "es"):
            records, _ = self.day(lang)
            texts = [exercise_text(c, lang) for c in self.school.students["s1"].exercises]
            teacher, family, principal = (self.report(a, records, lang) for a in ("a1", "a5", "a4"))
            for x in texts:
                self.assertIn(x, teacher)
                self.assertIn(x, family)
            self.assertFalse(any(exercise_text(c, lang) in principal for c in EXERCISES))
            self.assertFalse(any(x in self.report("a2", records, lang) for x in texts))  # another teacher


class TestOwnFavorites(Base):
    def test_the_childs_own_favorites_are_rewards_from_day_one(self):
        records, profiles = run_day(self.school, self.qloo, Rotation(), Approvals(), 0)
        sofi = [s.reward_item for s in records["s2"]["plan"]["steps"] if s.reward_item]
        self.assertTrue(sofi)
        self.assertTrue(set(sofi) <= {"Tini", "Harry Potter"})
        self.assertIn("Tini", " ".join(c for s in records["s2"]["plan"]["steps"] for c in s.calm))

    def test_own_favorites_pass_the_age_gate_too(self):
        leo = build_profile(self.school.students["s1"], self.qloo, self.school.roster(), self.approvals)
        self.assertIn(("tv_show", "Bluey"), [(c.domain, c.name) for c in leo.own])
        self.assertNotIn("Minecraft", [c.name for c in leo.own])  # E10+ and Leo is 8
        self.assertIn("Minecraft", leo.signals)  # still used to understand his taste
        rated = self.profile_with_search("s1", {"Bluey": {"properties": {"content_rating": "TV-MA"}}})
        self.assertNotIn("Bluey", [c.name for c in rated.own])

    def profile_with_search(self, sid, search):
        st = self.school.students[sid]
        return build_profile(st, real_client(Recorder(ideas_reply({}, search))), self.school.roster(),
                             self.approvals, game_ratings={})

    def test_a_rejected_favorite_stops_being_a_reward(self):
        self.approvals.decide(self.school.adults["a6"], "s2", "artist", "Tini", False)
        prof = build_profile(self.school.students["s2"], self.qloo, self.school.roster(), self.approvals)
        self.assertNotIn("Tini", [c.name for c in motivators(self.school.students["s2"], prof, self.approvals)])

    def test_with_qloo_down_the_favorites_still_reward(self):
        rec = Recorder(lambda r: http_error(r.full_url, 429))
        prof = build_profile(self.school.students["s2"], real_client(rec), self.school.roster(), self.approvals)
        self.assertTrue(prof.degraded)
        self.assertEqual(len(rec.requests), 3)  # one search and its retries, then Hermano stops asking
        self.assertEqual([c.name for c in prof.own], ["Tini", "Harry Potter"])


class TestRobustness(Base):
    def test_a_type_qloo_refuses_is_skipped_not_the_whole_day(self):
        # Seen live 2026-10-08: one refused type (400) used to empty the whole profile.
        def reply_with(code):
            def reply(req):
                url = req.full_url
                if "/search" in url:
                    return {"results": [{"name": "x", "entity_id": "E-x"}]}
                kind = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["filter.type"][0].rsplit(":", 1)[1]
                if kind == "book":
                    return http_error(url, code)
                return {"results": {"entities": [{"name": "Idea " + kind, "entity_id": "E-" + kind,
                                                   "properties": {"content_rating": "G" if kind == "movie" else "TV-Y"},
                                                   "tags": [{"name": "Family-Oriented"}]}]}}
            return reply
        leo = self.school.students["s1"]
        prof = build_profile(leo, real_client(Recorder(reply_with(400))), self.school.roster(), self.approvals)
        self.assertFalse(prof.degraded)
        self.assertEqual(prof.by_domain["book"], [])
        for d in ("artist", "tv_show", "movie"):
            self.assertEqual([c.name for c in prof.by_domain[d]], ["Idea " + d])
        bad_key = build_profile(leo, real_client(Recorder(reply_with(401))), self.school.roster(), self.approvals)
        self.assertEqual(bad_key.degraded, "Qloo 401")

    def test_qloo_down_means_a_plan_with_the_childs_favorites_not_a_crash(self):
        rec = Recorder(lambda r: http_error(r.full_url, 429))
        records, profiles = run_day(self.school, real_client(rec), Rotation(), self.approvals)
        for sid, rec_ in records.items():
            self.assertTrue(profiles[sid].degraded)
            own = {c.name for c in profiles[sid].own}
            for s in rec_["plan"]["steps"]:
                if s.block.kind in WORK_KINDS:
                    self.assertTrue(s.reward_item in own if s.reward_item else
                                    s.reward == t("en", "reward.default"), (sid, s.reward))
        self.assertEqual({c.name for c in profiles["s1"].own}, {"Bluey"})  # Minecraft is E10+
        self.assertEqual({c.name for c in profiles["s3"].own}, {"Dragon Ball"})  # Messi is a person
        self.assertEqual(records["s3"]["plan"]["steps"][1].reward_item, "Dragon Ball")


class TestLanguages(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("QLOO_API_KEY", None)
        # A real GROQ_API_KEY in the shell must not turn the CLI tests into network calls.
        os.environ.pop("GROQ_API_KEY", None)
        os.environ.pop("HERMANO_MODELS", None)

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(args))
        return rc, buf.getvalue()

    def test_english_is_the_default_and_spanish_is_one_flag_away(self):
        rc, out = self.run_main("demo")
        self.assertEqual(rc, 0)
        self.assertIn("First: ", out)
        self.assertIn(t("en", "cli.planner.rules"), out)
        self.assertNotIn("Primero: ", out)
        rc, out = self.run_main("demo", "--lang", "es")
        self.assertEqual(rc, 0)
        self.assertIn("Primero: ", out)
        self.assertNotIn("First: ", out)

    def test_planner_flag(self):
        rc, out = self.run_main("demo", "--planner", "rules")
        self.assertEqual(rc, 0)
        self.assertIn(t("en", "cli.planner.rules"), out)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, out = self.run_main("demo", "--planner", "llm")
        self.assertEqual(rc, 2)
        self.assertIn("GROQ_API_KEY", err.getvalue())

    def test_every_text_exists_in_both_languages(self):
        self.assertEqual(set(TEXT["en"]), set(TEXT["es"]))
        for code, text in SUPPORTS.items():
            self.assertEqual(set(text), {"en", "es"}, code)

    def test_three_day_demo_with_simulated_approvals(self):
        rc, out = self.run_main("demo", "--days", "3")
        self.assertEqual(rc, 0)
        self.assertIn("(simulated)", out)
        self.assertIn("=== Day 3", out)


class TestRepo(unittest.TestCase):
    def test_gitignore_keeps_qloo_answers_and_secrets_out(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn("cache/", lines)
        self.assertIn(".env", lines)

    def test_code_uses_only_documented_endpoints_on_the_hackathon_host(self):
        for p in (ROOT / "hermano").glob("*.py"):
            src = p.read_text(encoding="utf-8")
            self.assertNotRegex(src, r"/recs\b|/recommendations", p.name)
            self.assertNotRegex(src, r"(?<!hackathon\.)api\.qloo\.com", p.name)

    def test_data_ships_inside_the_package(self):
        for f in ("demo_school.json", "mock_taste.json", "demo_approvals.json", "game_ratings.json"):
            self.assertTrue((DATA / f).is_file(), f)

    def test_the_mock_says_it_is_not_qloo_data(self):
        raw = json.loads((DATA / "mock_taste.json").read_text(encoding="utf-8"))
        self.assertIn("NOT Qloo data", raw["_note"])


if __name__ == "__main__":
    unittest.main()
