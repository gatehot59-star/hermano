"""The Qloo client against the hackathon rules. Run with: python -m unittest -v"""
import tempfile
import unittest
import urllib.error
from pathlib import Path

from hermano.guard import UnsafeText
from hermano.qloo import (MockQloo, QlooClient, QlooError, QlooUnavailable, ResponseCache,
                          UnsupportedType, search_urn, urn)
from tests.helpers import DATA, Recorder, http_error

# Written here on purpose, not imported: a test that reads the constant it checks cannot fail.
HACKATHON = "https://hackathon.api.qloo.com"


def client(rec, **kw):
    kw.setdefault("sleep", lambda s: None)
    return QlooClient("k-test", opener=rec, **kw)


class TestTypes(unittest.TestCase):
    def test_insights_urn_rejects_unsupported_type(self):
        with self.assertRaises(ValueError):
            urn("music")

    def test_search_uses_its_own_type_names(self):
        self.assertEqual(search_urn("video_game"), "urn:entity:videogame")
        self.assertEqual(urn("video_game"), "urn:entity:videogame")  # video_game: 400 live, 2026-10-08
        self.assertEqual(search_urn("tv_show"), "urn:entity:tv_show")
        with self.assertRaises(ValueError):
            search_urn("destination")


class TestHackathonRules(unittest.TestCase):
    def test_client_needs_a_key(self):
        with self.assertRaises(ValueError):
            QlooClient("")

    def test_client_only_talks_to_the_hackathon_api(self):
        self.assertEqual(QlooClient("k").base_url, HACKATHON)
        for other in ("https://api.qloo.com", "https://staging.api.qloo.com", "http://hackathon.api.qloo.com"):
            with self.assertRaises(ValueError):
                QlooClient("k", base_url=other)

    def test_insights_is_a_get_with_the_key_in_a_header(self):
        rec = Recorder(lambda r: {"success": True, "results": {"entities": [{"name": "Matilda", "entity_id": "E1"}]}})
        out = client(rec).insights("book", ["A", "B"], take=3)
        req = rec.requests[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("X-api-key"), "k-test")
        self.assertTrue(req.get_header("User-agent", "").startswith("hermano/"))
        self.assertTrue(req.full_url.startswith(HACKATHON + "/v2/insights?"))
        self.assertNotIn("k-test", req.full_url)
        self.assertEqual(rec.params(), {"filter.type": ["urn:entity:book"],
                                        "signal.interests.entities": ["A,B"], "take": ["3"]})
        self.assertEqual(out, [{"name": "Matilda", "kind": "book", "id": "E1", "rating": "", "tags": []}])

    def test_insights_keeps_the_rating_and_the_tags_for_the_age_gate(self):
        # Shape seen live 2026-10-08: games carry the ESRB rating in properties.content_rating.
        game = {"name": "Genshin Impact", "entity_id": "E9", "properties": {"content_rating": "T"},
                "tags": [{"id": "t1", "name": "Anime-style", "type": "urn:tag:style:qloo"}, {"id": "t2"}]}
        rec = Recorder(lambda r: {"results": {"entities": [game, {"name": "Unrated", "entity_id": "E8",
                                                                   "properties": {"content_rating": ""}}]}})
        out = client(rec).insights("video_game", ["A"])
        self.assertEqual(out[0]["rating"], "T")
        self.assertEqual(out[0]["tags"], ["Anime-style"])
        self.assertEqual(out[1]["rating"], "")

    def test_search_entity_keeps_the_rating_too(self):
        rec = Recorder(lambda r: {"results": [{"name": "Minecraft", "entity_id": "E-m",
                                               "properties": {"content_rating": "E10+"}}]})
        c = client(rec)
        self.assertEqual(c.search_entity("Minecraft", "video_game"), {"id": "E-m", "rating": "E10+", "tags": []})
        self.assertEqual(c.search("Minecraft", "video_game"), "E-m")

    def test_search_request_shape(self):
        rec = Recorder()
        client(rec).search("Minecraft", "video_game")
        self.assertTrue(rec.requests[0].full_url.startswith(HACKATHON + "/search?"))
        self.assertEqual(rec.params(), {"query": ["Minecraft"], "types": ["urn:entity:videogame"], "take": ["3"]})

    def test_content_rating_goes_only_with_movies_and_shows(self):
        rec = Recorder()
        c = client(rec)
        c.insights("movie", ["A"], content_ratings=("G", "PG"))
        self.assertEqual(rec.params()["filter.content_rating"], ["G,PG"])
        c.insights("tv_show", ["A"], content_ratings=("TV-Y",))
        self.assertEqual(rec.params()["filter.content_rating"], ["TV-Y"])
        c.insights("video_game", ["A"], content_ratings=("G",))
        self.assertNotIn("filter.content_rating", rec.params())
        self.assertEqual(rec.params()["filter.type"], ["urn:entity:videogame"])

    def test_rejected_entities_are_excluded(self):
        rec = Recorder()
        client(rec).insights("book", ["A"], exclude_ids=["E-no", "E-no2"])
        self.assertEqual(rec.params()["filter.exclude.entities"], ["E-no,E-no2"])


class TestAnswers(unittest.TestCase):
    def test_search_returns_the_first_entity_id(self):
        rec = Recorder(lambda r: {"results": [{"name": "Bluey", "entity_id": "Q1"}]})
        self.assertEqual(client(rec).search("Bluey", "tv_show"), "Q1")

    def test_search_404_means_no_results(self):
        rec = Recorder(lambda r: http_error(r.full_url, 404))
        self.assertIsNone(client(rec).search("Nothing like this", "tv_show"))
        self.assertEqual(len(rec.requests), 1)

    def test_search_skips_entities_of_another_type(self):
        rec = Recorder(lambda r: {"results": [{"entity_id": "M1", "types": ["urn:entity:movie"]},
                                              {"entity_id": "G1", "types": ["urn:entity:videogame"]}]})
        self.assertEqual(client(rec).search("Minecraft", "video_game"), "G1")

    def test_403_means_an_unsupported_type_and_is_not_retried(self):
        rec = Recorder(lambda r: http_error(r.full_url, 403))
        with self.assertRaises(UnsupportedType):
            client(rec).insights("podcast", ["A"])
        self.assertEqual(len(rec.requests), 1)


class TestRobustness(unittest.TestCase):
    def test_429_is_retried_then_succeeds(self):
        answers = [http_error("u", 429), {"results": [{"entity_id": "Q1"}]}]
        waits = []
        rec = Recorder(lambda r: answers.pop(0))
        self.assertEqual(client(rec, sleep=waits.append).search("Bluey", "tv_show"), "Q1")
        self.assertEqual(len(rec.requests), 2)
        self.assertEqual(len(waits), 1)

    def test_gives_up_after_the_retries(self):
        rec = Recorder(lambda r: http_error(r.full_url, 429))
        with self.assertRaises(QlooUnavailable):
            client(rec, retries=2).search("Bluey", "tv_show")
        self.assertEqual(len(rec.requests), 3)

    def test_timeouts_are_retried_then_reported(self):
        rec = Recorder(lambda r: urllib.error.URLError("timed out"))
        with self.assertRaises(QlooUnavailable):
            client(rec).insights("book", ["A"])
        self.assertEqual(len(rec.requests), 3)

    def test_a_bad_key_is_not_retried(self):
        rec = Recorder(lambda r: http_error(r.full_url, 401))
        with self.assertRaises(QlooError) as cm:
            client(rec).search("Bluey", "tv_show")
        self.assertNotIsInstance(cm.exception, QlooUnavailable)
        self.assertEqual(len(rec.requests), 1)

    def test_cache_saves_a_call_and_never_stores_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Recorder()
            c = client(rec, cache=ResponseCache(d))
            c.search("Bluey", "tv_show")
            c.search("Bluey", "tv_show")
            self.assertEqual(len(rec.requests), 1)
            files = list(Path(d).iterdir())
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^[0-9a-f]{64}\.json$")
            self.assertNotIn("k-test", files[0].read_text(encoding="utf-8"))


class TestPrivacyInsideTheClient(unittest.TestCase):
    def test_client_refuses_health_or_contact_text(self):
        rec = Recorder()
        c = client(rec)
        for text in ("Leo y su TEA", "toma metilfenidato", "mama: 3875551234", "leo@example.com"):
            with self.assertRaises(UnsafeText):
                c.search(text, "tv_show")
        self.assertEqual(rec.requests, [])

    def test_entity_ids_with_digits_are_not_taken_for_contact_data(self):
        rec = Recorder()
        client(rec).insights("book", ["39458DC0-F91D-4591-80A8-E254E66F84A2"])
        self.assertEqual(len(rec.requests), 1)


class TestMock(unittest.TestCase):
    def test_mock_imitates_the_rating_filter_and_the_exclusions(self):
        m = MockQloo(DATA / "mock_taste.json")
        names = [x["name"] for x in m.insights("movie", ["mock:bluey"], content_ratings=("G",))]
        self.assertEqual(names, ["Toy Story"])
        names = [x["name"] for x in m.insights("book", ["mock:bluey"], exclude_ids=["mock:elmer"])]
        self.assertNotIn("Elmer", names)
        self.assertIn("The Little Prince", names)

    def test_mock_refuses_unsafe_text_too(self):
        with self.assertRaises(UnsafeText):
            MockQloo(DATA / "mock_taste.json").search("Leo y su TEA", "tv_show")


if __name__ == "__main__":
    unittest.main()
