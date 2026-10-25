import json
import unittest
from datetime import datetime, timedelta, timezone

from plpredict.fetch import Match
from plpredict.models import AwardPick, AwardStatus, Prediction, PredictionSet
from plpredict.simulate import SimulationError, results_seed, simulate_season, team_ratings
from tests.helpers import CLUBS, test_aliases

START = datetime(2026, 8, 15, 14, tzinfo=timezone.utc)
PROMOTED = ("Coventry City", "Hull City", "Ipswich Town")


def season(played_gws=3, total_gws=6, strong="Arsenal"):
    """Round-robin-ish fixtures; the first ``played_gws`` gameweeks finished.
    ``strong`` wins every match it plays 3-0; others draw 1-1."""
    matches = []
    for gw in range(1, total_gws + 1):
        order = CLUBS[gw % 20:] + CLUBS[:gw % 20]
        when = START + timedelta(weeks=gw - 1)
        for i in range(10):
            h, a = order[i], order[19 - i]
            if gw <= played_gws:
                hg, ag = (3, 0) if h == strong else (0, 3) if a == strong else (1, 1)
                matches.append(Match(when, "FINISHED", h, a, hg, ag, matchday=gw))
            else:
                matches.append(Match(when, "TIMED", h, a, None, None, matchday=gw))
    return matches


def pset_of(*people):
    return PredictionSet(season_clubs=tuple(CLUBS), promoted_clubs=PROMOTED,
                         last_season_points={c: 50 for c in CLUBS if c not in PROMOTED},
                         predictions=tuple(people))


def person(name, order, **picks):
    return Prediction(name, tuple(order), {k: AwardPick(str(v), v) for k, v in picks.items()})


ARSENAL_FIRST = ["Arsenal"] + [c for c in CLUBS if c != "Arsenal"]
ARSENAL_LAST = [c for c in CLUBS if c != "Arsenal"] + ["Arsenal"]


class RatingTests(unittest.TestCase):
    def test_strong_attack_and_defence_shrunk_toward_average(self):
        model = team_ratings(season(), CLUBS)
        arsenal, other = model.ratings["Arsenal"], model.ratings["Everton"]
        self.assertGreater(arsenal.attack, 1.5)
        self.assertLess(arsenal.defence, other.defence)
        # 3 games of 3-0 shrunk by 5 average games: well below the raw 3x rate.
        self.assertLess(arsenal.attack, 3.0)

    def test_defaults_before_any_match(self):
        model = team_ratings(season(played_gws=0), CLUBS)
        self.assertEqual((model.home_goals, model.away_goals), (1.5, 1.2))
        self.assertAlmostEqual(model.ratings["Arsenal"].attack, 1.0)

    def test_rates(self):
        model = team_ratings(season(), CLUBS)
        home, away = model.rates("Arsenal", "Everton")
        self.assertGreater(home, away)

    def test_no_clubs(self):
        with self.assertRaises(SimulationError):
            team_ratings([], [])


class SeedTests(unittest.TestCase):
    def test_stable_until_a_result_changes(self):
        a, b = season(), season()
        self.assertEqual(results_seed(a), results_seed(b))
        b[0] = Match(b[0].utc_date, "FINISHED", b[0].home, b[0].away, 5, 5, matchday=1)
        self.assertNotEqual(results_seed(a), results_seed(b))

    def test_unplayed_fixtures_do_not_matter(self):
        a = season()
        self.assertEqual(results_seed(a), results_seed(a[:-5]))


class SimulateTests(unittest.TestCase):
    def setUp(self):
        self.pset = pset_of(person("ED", ARSENAL_FIRST), person("LUKE", ARSENAL_LAST),
                            person("JACK", list(reversed(CLUBS))))

    def sim(self, matches, n=300, **kw):
        with self.assertLogs("plpredict.simulate", "INFO"):
            return simulate_season(self.pset, matches, {}, {}, test_aliases(), n=n, **kw)

    def test_probabilities_are_consistent(self):
        r = self.sim(season())
        self.assertAlmostEqual(sum(p["win"] for p in r.people.values()), 1.0, places=3)
        self.assertAlmostEqual(sum(c["title"] for c in r.clubs.values()), 1.0, places=3)
        self.assertAlmostEqual(sum(c["top5"] for c in r.clubs.values()), 5.0, places=3)
        self.assertAlmostEqual(sum(c["relegation"] for c in r.clubs.values()), 3.0, places=3)
        for f in r.fixtures.values():
            self.assertAlmostEqual(f["p_home"] + f["p_draw"] + f["p_away"], 1.0, places=3)
        self.assertEqual(len(r.fixtures), 30)
        json.dumps(r.people)  # plain numbers, JSON-ready

    def test_strong_team_and_its_backer_favoured(self):
        r = self.sim(season())
        self.assertEqual(max(r.clubs, key=lambda c: r.clubs[c]["title"]), "Arsenal")
        self.assertGreater(r.people["ED"]["win"], r.people["LUKE"]["win"])
        p = r.people["ED"]
        self.assertLessEqual(p["points_p10"], p["points_p50"])
        self.assertLessEqual(p["points_p50"], p["points_p90"])

    def test_deterministic_for_the_same_results(self):
        a, b = self.sim(season()), self.sim(season())
        self.assertEqual((a.seed, a.people), (b.seed, b.people))

    def test_season_over_is_certain(self):
        r = self.sim(season(played_gws=6, total_gws=6), n=20)
        self.assertEqual(r.fixtures, {})
        self.assertEqual(sorted(p["win"] for p in r.people.values()), [0.0, 0.0, 1.0])
        self.assertEqual(r.people["ED"]["points_p10"], r.people["ED"]["points_p90"])

    def test_held_award_counts_in_every_season(self):
        pset = pset_of(person("ED", ARSENAL_LAST, golden_boot="Erling Haaland"),
                       person("LUKE", ARSENAL_LAST, golden_boot="Bukayo Saka"))
        boot = AwardStatus("golden_boot", ("Erling Haaland",), False)
        with self.assertLogs("plpredict.simulate", "INFO"):
            r = simulate_season(pset, season(), {"golden_boot": boot}, {}, test_aliases(), n=50)
        self.assertEqual(r.people["ED"]["win"], 1.0)  # same table picks, +10 every time
        self.assertEqual(r.people["ED"]["points_p10"] - r.people["LUKE"]["points_p10"], 10)

    def test_entered_hand_award_counts(self):
        pset = pset_of(person("ED", ARSENAL_LAST, pfa_player="Cole Palmer"),
                       person("LUKE", ARSENAL_LAST, pfa_player="Bukayo Saka"))
        pfa = AwardStatus("pfa_player", ("Cole Palmer",), True)
        with self.assertLogs("plpredict.simulate", "INFO"):
            r = simulate_season(pset, season(), {}, {"pfa_player": pfa}, test_aliases(), n=20)
        self.assertEqual(r.people["ED"]["win"], 1.0)

    def test_bad_inputs(self):
        with self.assertRaises(SimulationError):
            simulate_season(self.pset, season(), {}, {}, test_aliases(), n=0)
        with self.assertRaises(SimulationError):
            simulate_season(pset_of(), season(), {}, {}, test_aliases(), n=10)


if __name__ == "__main__":
    unittest.main()
