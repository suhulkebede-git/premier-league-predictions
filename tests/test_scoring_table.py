"""Table rules, checked against the spec's worked examples A-C."""

import unittest

from plpredict.models import Prediction, TableRow
from plpredict.scoring import InvalidTableError, positions, score_table_rules
from tests.helpers import CLUBS


def table_from(order: list[str]) -> list[TableRow]:
    return [TableRow(club=c, position=i + 1, played=9, won=0, draw=0, lost=0,
                     goals_for=0, goals_against=0, points=0)
            for i, c in enumerate(order)]


def pred(order: list[str], person: str = "ED") -> Prediction:
    return Prediction(person=person, order=tuple(order), awards={})


def swap(order: list[str], a: int, b: int) -> list[str]:
    """Swap 1-based positions a and b."""
    out = list(order)
    out[a - 1], out[b - 1] = out[b - 1], out[a - 1]
    return out


BASE = list(CLUBS)


class WorkedExampleA(unittest.TestCase):
    """Top of the table: 68 points from seven spots."""

    def setUp(self):
        top = ["Arsenal", "Manchester City", "Liverpool", "Chelsea",
               "Newcastle United", "Aston Villa", "Tottenham Hotspur"]
        rest = [c for c in CLUBS if c not in top]  # actual 8th-20th
        actual = top + rest
        # Predicted: Liverpool/City swapped; rest shifted 3 places so none
        # of positions 8-20 is exact or one off (diffs of 3 or 10).
        shifted = rest[-3:] + rest[:-3]
        predicted = ["Arsenal", "Liverpool", "Manchester City"] + top[3:] + shifted
        self.result = score_table_rules(pred(predicted), positions(table_from(actual), CLUBS))

    def test_breakdown(self):
        self.assertEqual(self.result.breakdown, {
            "exact": 20, "champion": 20, "top5": 15, "relegation": 0,
            "sixth_seventh": 13, "one_off": 0,
        })
        self.assertEqual(sum(self.result.breakdown.values()), 68)

    def test_champion_replaces_exact_for_first(self):
        self.assertEqual(self.result.hits["champion"], ["Arsenal"])
        self.assertNotIn("Arsenal", self.result.hits["exact"])
        self.assertEqual(self.result.exact_count, 5)  # Arsenal still counts for the bonus

    def test_off_by_one_outside_band_scores_nothing(self):
        self.assertEqual(self.result.hits["one_off"], [])


class WorkedExampleB(unittest.TestCase):
    """One-off edges."""

    def score(self, predicted, actual=BASE):
        return score_table_rules(pred(predicted), positions(table_from(actual), CLUBS))

    def test_both_inside_band(self):  # Brighton 9th -> 8th
        r = self.score(swap(BASE, 8, 9))
        self.assertEqual(sorted(r.hits["one_off"]), sorted([BASE[7], BASE[8]]))
        self.assertEqual(r.breakdown["one_off"], 6)

    def test_seventh_and_eighth_both_count(self):  # 7th -> 8th and 8th -> 7th
        r = self.score(swap(BASE, 7, 8))
        self.assertEqual(sorted(r.hits["one_off"]), sorted([BASE[6], BASE[7]]))

    def test_seventeenth_and_eighteenth_both_count(self):
        r = self.score(swap(BASE, 17, 18))
        self.assertEqual(r.breakdown["one_off"], 6)

    def test_neither_in_band_scores_nothing(self):  # 18th -> 19th
        r = self.score(swap(BASE, 18, 19))
        self.assertEqual(r.hits["one_off"], [])
        self.assertEqual(r.breakdown["relegation"], 15)  # same three clubs still down

    def test_off_by_two_scores_nothing(self):  # 12th -> 14th
        predicted = list(BASE)
        # actual 12,13,14 -> predicted 13,14,12: club actual 14th predicted 12th
        predicted[11], predicted[12], predicted[13] = BASE[13], BASE[11], BASE[12]
        r = self.score(predicted)
        self.assertNotIn(BASE[13], r.hits["one_off"])
        self.assertEqual(sorted(r.hits["one_off"]), sorted([BASE[11], BASE[12]]))

    def test_second_and_third_off_by_one_score_nothing(self):
        r = self.score(swap(BASE, 2, 3))
        self.assertEqual(r.hits["one_off"], [])


class WorkedExampleC(unittest.TestCase):
    """Relegation: same three clubs in any order, plus exact for Ipswich."""

    def test_relegation(self):
        upper = [c for c in CLUBS if c not in ("Hull City", "Ipswich Town", "Coventry City")]
        actual = upper + ["Hull City", "Ipswich Town", "Coventry City"]
        predicted = upper + ["Coventry City", "Ipswich Town", "Hull City"]
        r = score_table_rules(pred(predicted), positions(table_from(actual), CLUBS))
        self.assertEqual(r.breakdown["relegation"], 15)
        self.assertIn("Ipswich Town", r.hits["exact"])
        self.assertNotIn("Hull City", r.hits["exact"] + r.hits["one_off"])
        self.assertNotIn("Coventry City", r.hits["exact"] + r.hits["one_off"])

    def test_two_of_three_scores_nothing(self):
        r = score_table_rules(pred(swap(BASE, 17, 18)), positions(table_from(BASE), CLUBS))
        self.assertEqual(r.breakdown["relegation"], 0)


class OtherRules(unittest.TestCase):
    def test_perfect_prediction(self):
        r = score_table_rules(pred(BASE), positions(table_from(BASE), CLUBS))
        self.assertEqual(r.breakdown, {"exact": 95, "champion": 20, "top5": 15,
                                       "relegation": 15, "sixth_seventh": 13, "one_off": 0})
        self.assertEqual(r.exact_count, 20)
        self.assertEqual(r.misses_desc, (0,) * 20)

    def test_sixth_seventh_needs_both_slots(self):
        r = score_table_rules(pred(swap(BASE, 6, 7)), positions(table_from(BASE), CLUBS))
        self.assertEqual(r.breakdown["sixth_seventh"], 0)

    def test_top5_any_order(self):
        predicted = list(reversed(BASE[:5])) + BASE[5:]
        r = score_table_rules(pred(predicted), positions(table_from(BASE), CLUBS))
        self.assertEqual(r.breakdown["top5"], 15)
        self.assertEqual(r.breakdown["champion"], 0)

    def test_misses_sorted_largest_first(self):
        r = score_table_rules(pred(swap(BASE, 3, 15)), positions(table_from(BASE), CLUBS))
        self.assertEqual(r.misses_desc[:3], (12, 12, 0))

    def test_prediction_must_match_table_clubs(self):
        bad = BASE[:-1] + [BASE[0]]
        with self.assertRaises(ValueError):
            score_table_rules(pred(bad), positions(table_from(BASE), CLUBS))


class PositionsValidation(unittest.TestCase):
    def test_wrong_size(self):
        with self.assertRaisesRegex(InvalidTableError, "19 rows"):
            positions(table_from(BASE)[:19], CLUBS)

    def test_unknown_club(self):
        rows = table_from(BASE[:-1] + ["Burnley"])
        with self.assertRaisesRegex(InvalidTableError, "unknown \\['Burnley'\\]"):
            positions(rows, CLUBS)

    def test_bad_positions(self):
        rows = table_from(BASE)
        rows[1] = TableRow(**{**rows[1].__dict__, "position": 1})
        with self.assertRaisesRegex(InvalidTableError, "not 1-20"):
            positions(rows, CLUBS)


if __name__ == "__main__":
    unittest.main()
