"""Fixes after the independent audit of main at c81a34b (TITAN Tao, 2026-10-09), issues #1 to #4."""
import unittest

from hermano.i18n import LANGS, TEXT, t


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


if __name__ == "__main__":
    unittest.main()
