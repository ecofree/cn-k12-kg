"""Independent counterexamples for recovered authoring and answer contracts."""
from fractions import Fraction
import unittest

from cnkg.answers import grade_answer
from cnkg.catalog import load_catalog


class RecoveredContentRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def assert_grade(self, identity, value, status):
        self.assertEqual(grade_answer(self.catalog.task(identity), value)["status"], status)

    def test_route_distance_cannot_accept_the_former_column_label(self):
        # Between rows 5 and 1 there are four unit intervals, not three columns.
        distance = abs(5 - 1)
        self.assert_grade("m6a.1.1-q3", str(distance), "correct")
        self.assert_grade("m6a.1.1-q3", "3", "wrong")
        for obsolete in ("第三列", "3列"):
            self.assert_grade("m6a.1.1-q3", obsolete, "invalid")

    def test_length_estimate_accepts_its_integer_answer(self):
        self.assert_grade("m2a.4.4-q3", str(20 * 3), "correct")
        self.assert_grade("m2a.4.4-q3", "0.7", "wrong")

    def test_requested_representation_is_enforced_without_changing_value(self):
        self.assert_grade("m6a.3.3-q90", "1.75", "correct")
        self.assert_grade("m6a.3.3-q90", "7/4", "wrong")
        self.assert_grade("m9b.34.3-q90", "0.26", "correct")
        self.assert_grade("m9b.34.3-q90", "26%", "invalid")
        self.assert_grade("m4b.1.1-q92", str(196 // 23), "correct")
        self.assert_grade("m4b.1.1-q92", "196/23", "wrong")

    def test_set_complement_uses_actual_membership(self):
        all_people = set(range(50))
        choir, dance = set(range(25)), set(range(17, 37))
        self.assertEqual((len(choir), len(dance), len(choir & dance)), (25, 20, 8))
        self.assert_grade("mh1.1.3-q102", str(len(all_people - (choir | dance))), "correct")
        self.assert_grade("mh1.1.3-q102", "25", "wrong")

    def test_rational_division_keeps_the_original_domain(self):
        for x in (-5, -3, -1, 1, 2, 4, 7):
            result = Fraction(x * x - 9, x) / Fraction(x - 3, x)
            self.assertEqual(result, x + 3)
        self.assert_grade("m8a.18.2-q301", "3+x", "correct")
        self.assert_grade("m8a.18.2-q302", "{0,3}", "correct")
        for extra_or_missing in ("{3}", "{0}", "{0,-3,3}"):
            self.assert_grade("m8a.18.2-q302", extra_or_missing, "wrong")

    def test_actual_polynomial_coefficients_are_not_binomial_coefficients(self):
        coefficients = [1]
        for _ in range(4):
            product = [0] * (len(coefficients) + 1)
            for index, coefficient in enumerate(coefficients):
                product[index] -= 2 * coefficient
                product[index + 1] += 3 * coefficient
            coefficients = product
        self.assertEqual(coefficients, [16, -96, 216, -216, 81])
        self.assert_grade("mh5.6.3.2-q203", str(sum(coefficients)), "correct")
        self.assert_grade("mh5.6.3.2-q203", "16", "wrong")


if __name__ == "__main__":
    unittest.main()
