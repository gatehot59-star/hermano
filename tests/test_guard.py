"""The privacy guard. Run with: python -m unittest -v"""
import unittest

from hermano.guard import (Pseudonymizer, UnsafeText, contains_contact, contains_health, scrub,
                           validate_interest)

# The 31 terms of audit 2026-10-08-11, copied verbatim: they were written before this filter.
AUDIT_TERMS = ['TEA', 'TDAH', 'TDA', 'autismo', 'autista', 'diagnóstico', 'trastorno', 'ADHD', 'ASD', 'autism',
               'Asperger', 'TGD', 'TOC', 'TEL', 'dislexia', 'discalculia', 'discapacidad', 'CUD',
               'medicación', 'metilfenidato', 'Ritalina', 'risperidona', 'psiquiatra', 'neurólogo', 'epilepsia',
               'convulsión', 'disorder', 'disability', 'dyslexia', 'medication', 'seizure']
MORE = ["síndrome de Down", "Autistic", "MEDICACION", "10 mg", "10mg", "melatonina", "pastilla"]
ORDINARY = ["teatro", "Adele", "Disney", "Medalla", "addition", "Matemática", "Toca Life World",
            "El Diego", "dismissal", "Educational psychologist (demo)", "Occupational therapist (demo)",
            "Terapista ocupacional (demo)", "Psicopedagoga (demo)", "Pokémon Scarlet"]


class TestHealthWords(unittest.TestCase):
    def test_every_audit_term_is_caught(self):
        self.assertEqual([w for w in AUDIT_TERMS + MORE if not contains_health(w)], [])

    def test_scrub_removes_them_and_keeps_the_rest(self):
        out = scrub("Tiene TEA y TDAH, toma metilfenidato 10 mg. Le gusta el teatro.", "es")
        self.assertFalse(contains_health(out))
        self.assertIn("[dato de salud omitido]", out)
        self.assertIn("teatro", out)
        self.assertEqual(scrub("ADHD", "en"), "[health detail removed]")

    def test_ordinary_words_are_not_flagged(self):
        self.assertEqual([w for w in ORDINARY if contains_health(w)], [])


class TestInterests(unittest.TestCase):
    ROSTER = ["Leo", "Sofi", "Mati"]

    def test_safe_interests_pass_cleaned(self):
        self.assertEqual(validate_interest("  Harry   Potter ", self.ROSTER), "Harry Potter")

    def test_unsafe_interests_are_refused_with_a_reason(self):
        cases = {"Leo Gomez y su TEA": "health detail", "la serie que ve sofi": "child name",
                 "Bluey 3875551234": "contact data", "mail leo.g@example.com": "contact data",
                 "": "empty", "x " * 40: "too long", "Tom y su TDAH": "health detail"}
        for text, reason in cases.items():
            with self.assertRaises(UnsafeText) as cm:
                validate_interest(text, self.ROSTER)
            self.assertEqual(cm.exception.reason, reason, text)
            self.assertNotIn(text.strip() or "-", str(cm.exception))

    def test_contact_data(self):
        self.assertTrue(contains_contact("www.example.com"))
        self.assertFalse(contains_contact("Pokémon Scarlet"))
        self.assertFalse(contains_contact("EA Sports FC 24"))


class TestPseudonymizer(unittest.TestCase):
    def test_names_become_placeholders_and_come_back(self):
        p = Pseudonymizer(students=["Leo", "Sofi"], adults=["Familia de Leo (demo)"])
        masked = p.mask("Leo y Sofi trabajaron bien; leo eligió música.")
        self.assertNotRegex(masked, r"(?i)\b(leo|sofi)\b")
        self.assertIn("STUDENT_1", masked)
        self.assertEqual(p.unmask(p.mask("Leo y Sofi")), "Leo y Sofi")


if __name__ == "__main__":
    unittest.main()
