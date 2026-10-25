import unittest
from datetime import date, datetime, timedelta, timezone

from plpredict.fetch import Match
from plpredict.history import Adjustment
from plpredict.impact import FixtureImpact, fixture_swings, totals_for
from tests.helpers import CLUBS, test_aliases
from tests.test_simulate import ARSENAL_FIRST, ARSENAL_LAST, person, pset_of, season

TODAY = date(2026, 9, 10)


def fixture(home, away, gw=4):
    return Match(datetime(2026, 9, 12, 14, tzinfo=timezone.utc) + timedelta(hours=gw), "TIMED",
                 home, away, None, None, matchday=gw)


class SwingTests(unittest.TestCase):
    def setUp(self):
        self.pset = pset_of(person("ED", ARSENAL_FIRST), person("LUKE", ARSENAL_LAST))
        self.done = season(played_gws=3, total_gws=3, strong="Chelsea")  # Arsenal not top yet

    def swings(self, fixtures, **kw):
        with self.assertLogs("plpredict.impact", "INFO"):
            return fixture_swings(self.pset, self.done, fixtures, [], {}, test_aliases(),
                                  kw.get("adjustments", []), TODAY)

    def test_deltas_match_rescoring_directly(self):
        f = fixture("Arsenal", "Everton")
        (impact,) = self.swings([f])
        base = totals_for(self.pset, self.done, [], {}, test_aliases(), [], TODAY)
        win = totals_for(self.pset, self.done + [Match(f.utc_date, "FINISHED", "Arsenal", "Everton", 1, 0,
                                                       matchday=4)], [], {}, test_aliases(), [], TODAY)
        expected = {p: win[p] - base[p] for p in base if win[p] != base[p]}
        self.assertEqual(impact.swings["home"], expected)
        self.assertEqual(set(impact.swings), {"home", "draw", "away"})

    def test_unaffected_people_left_out_and_size(self):
        (impact,) = self.swings([fixture("Arsenal", "Everton")])
        for deltas in impact.swings.values():
            self.assertNotIn(0, deltas.values())
        self.assertEqual(impact.size, max(sum(abs(d) for d in s.values()) for s in impact.swings.values()))

    def test_kickoff_order(self):
        late, early = fixture("Leeds United", "Fulham", gw=6), fixture("Arsenal", "Everton", gw=4)
        out = self.swings([late, early])
        self.assertEqual([(i.home, i.away) for i in out], [("Arsenal", "Everton"), ("Leeds United", "Fulham")])

    def test_finished_fixture_rejected(self):
        with self.assertRaises(ValueError):
            fixture_swings(self.pset, self.done, [self.done[0]], [], {}, test_aliases(), [], TODAY)

    def test_no_fixtures(self):
        self.assertEqual(fixture_swings(self.pset, self.done, [], [], {}, test_aliases(), [], TODAY), [])

    def test_deductions_count(self):
        adj = [Adjustment("Chelsea", -30, 2)]
        base = totals_for(self.pset, self.done, [], {}, test_aliases(), adj, TODAY)
        plain = totals_for(self.pset, self.done, [], {}, test_aliases(), [], TODAY)
        self.assertNotEqual(base, plain)

    def test_empty_impact_size(self):
        self.assertEqual(FixtureImpact("A", "B", datetime.now(timezone.utc)).size, 0)


if __name__ == "__main__":
    unittest.main()
