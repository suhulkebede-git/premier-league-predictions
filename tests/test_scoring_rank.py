"""Most-exact bonus, award points and ranking (spec example D + tiebreak)."""

import unittest

from plpredict.models import AwardPick, AwardStatus, PersonScore, Prediction
from plpredict.scoring import _rank_key, score_all, score_awards
from tests.helpers import CLUBS
from tests.test_scoring_table import swap, table_from

BASE = list(CLUBS)
TABLE = table_from(BASE)


def person(name, order, **picks):
    awards = {k: (v if isinstance(v, AwardPick) else AwardPick(raw=str(v), value=v))
              for k, v in picks.items()}
    return Prediction(person=name, order=tuple(order), awards=awards)


def status(key, *leaders, settled=False):
    return AwardStatus(key=key, leaders=tuple(leaders), settled=settled)


def derange(order, n_exact):
    """Return order with exactly n_exact clubs (positions 1..n) in place and
    the rest rotated by 10 so none are exact or one off."""
    head, tail = list(order[:n_exact]), list(order[n_exact:])
    k = 10 % len(tail) if tail else 0
    return head + tail[k:] + tail[:k]


class ScoreAwardsTests(unittest.TestCase):
    def test_pick_among_leaders_scores_ten(self):
        p = person("ED", BASE, golden_boot="Erling Haaland", most_goals="Arsenal")
        res = score_awards(p, {"golden_boot": status("golden_boot", "Erling Haaland"),
                               "most_goals": status("most_goals", "Chelsea")})
        self.assertEqual((res.points, res.hits), (10, ["golden_boot"]))

    def test_shared_leaders_all_count(self):
        p = person("ED", BASE, most_draws="Leeds United")
        res = score_awards(p, {"most_draws": status("most_draws", "Everton", "Leeds United")})
        self.assertEqual(res.points, 10)

    def test_unknown_category_scores_nothing(self):
        p = person("ED", BASE, pfa_player="Bruno Fernandes")
        self.assertEqual(score_awards(p, {"pfa_player": status("pfa_player")}).points, 0)
        self.assertEqual(score_awards(p, {}).points, 0)

    def test_title_margin_exact_only(self):
        p = person("ED", BASE, title_margin=8)
        self.assertEqual(score_awards(p, {"title_margin": status("title_margin", 8)}).points, 10)
        self.assertEqual(score_awards(p, {"title_margin": status("title_margin", 7)}).points, 0)

    def test_yes_no(self):
        p = person("ED", BASE, any_over_90=True)
        self.assertEqual(
            score_awards(p, {"any_over_90": status("any_over_90", True, settled=True)}).points, 10)
        self.assertEqual(
            score_awards(p, {"any_over_90": status("any_over_90", False, settled=True)}).points, 0)

    def test_alt_pick_is_tiebreak_only(self):
        pick = AwardPick(raw="Crystal Palace or Brighton", value="Crystal Palace",
                         alt="Brighton & Hove Albion")
        p = person("UGWE", BASE, most_draws=pick)
        res = score_awards(p, {"most_draws": status("most_draws", "Brighton & Hove Albion")})
        self.assertEqual((res.points, res.alt_hits), (0, ["most_draws"]))
        res = score_awards(p, {"most_draws": status("most_draws", "Crystal Palace")})
        self.assertEqual((res.points, res.alt_hits), (10, []))


class MostExactBonusTests(unittest.TestCase):
    """Worked example D."""

    def test_everyone_tied_for_most_gets_it(self):
        preds = [person("ED", derange(BASE, 6)), person("LUKE", derange(BASE, 6)),
                 person("JACK", derange(BASE, 4))]
        scores = {s.person: s for s in score_all(preds, TABLE, CLUBS, {})}
        self.assertEqual(scores["ED"].breakdown["most_exact"], 20)
        self.assertEqual(scores["LUKE"].breakdown["most_exact"], 20)
        self.assertEqual(scores["JACK"].breakdown["most_exact"], 0)

    def test_zero_exact_hits_everyone_gets_it(self):
        preds = [person("ED", derange(BASE, 0)), person("LUKE", derange(BASE, 0))]
        for s in score_all(preds, TABLE, CLUBS, {}):
            self.assertEqual(s.breakdown["most_exact"], 20)


class RankingTests(unittest.TestCase):
    def test_total_is_sum_of_breakdown(self):
        preds = [person("ED", BASE, golden_boot="Erling Haaland")]
        s = score_all(preds, TABLE, CLUBS, {"golden_boot": status("golden_boot", "Erling Haaland")})[0]
        self.assertEqual(s.total, sum(s.breakdown.values()))
        self.assertEqual(s.breakdown["awards"], 10)
        self.assertEqual(s.hits["awards"], ("golden_boot",))
        self.assertEqual(s.rank, 1)

    def test_higher_total_ranks_first(self):
        preds = [person("ED", swap(BASE, 1, 20)), person("LUKE", BASE)]
        ranked = score_all(preds, TABLE, CLUBS, {})
        self.assertEqual([(s.person, s.rank) for s in ranked], [("LUKE", 1), ("ED", 2)])

    def test_stinker_tiebreak_worked_example(self):
        # Spec: equal points; worst misses 10,7 / 10,8 / 12 -> Ed, Jack, Luke.
        mk = lambda name, misses: PersonScore(person=name, total=140, breakdown={},
                                              hits={}, misses_desc=misses)
        ed, jack, luke = mk("ED", (10, 7, 5)), mk("JACK", (10, 8, 2)), mk("LUKE", (12, 4, 3))
        order = sorted([luke, jack, ed], key=_rank_key)
        self.assertEqual([s.person for s in order], ["ED", "JACK", "LUKE"])

    def test_stinkers_break_a_real_points_tie(self):
        # Both swap two pairs, no one-offs (same points); ED's worst miss is smaller.
        ed = swap(swap(BASE, 9, 11), 13, 15)    # misses 2,2,2,2
        luke = swap(swap(BASE, 9, 12), 13, 15)  # misses 3,3,2,2
        ranked = score_all([person("LUKE", luke), person("ED", ed)], TABLE, CLUBS, {})
        self.assertEqual(ranked[0].total, ranked[1].total)
        self.assertEqual([(s.person, s.rank) for s in ranked], [("ED", 1), ("LUKE", 2)])

    def test_unbroken_tie_shares_rank(self):
        preds = [person("ED", BASE), person("LUKE", BASE), person("JACK", swap(BASE, 1, 20))]
        ranked = score_all(preds, TABLE, CLUBS, {})
        self.assertEqual([s.rank for s in ranked], [1, 1, 3])

    def test_alt_pick_breaks_tie_before_stinkers(self):
        alt = AwardPick(raw="Crystal Palace or Brighton", value="Crystal Palace",
                        alt="Brighton & Hove Albion")
        plain = AwardPick(raw="Leeds", value="Leeds United")
        # UGWE has the worse stinker but Brighton leads draws.
        ugwe = person("UGWE", swap(BASE, 9, 12), most_draws=alt)
        ed = person("ED", swap(BASE, 9, 10), most_draws=plain)
        draws = {"most_draws": status("most_draws", "Brighton & Hove Albion")}
        # Equalise points: ED's adjacent swap earns one-off points UGWE lacks,
        # so compare rank keys at equal totals directly.
        scores = {s.person: s for s in score_all([ugwe, ed], TABLE, CLUBS, draws)}
        tied = [PersonScore(**{**scores[n].__dict__, "total": 100}) for n in ("ED", "UGWE")]
        self.assertEqual(sorted(tied, key=_rank_key)[0].person, "UGWE")
        self.assertEqual(scores["UGWE"].hits["tiebreak"], ("most_draws",))
        self.assertEqual(scores["UGWE"].breakdown["awards"], 0)

    def test_no_predictions(self):
        with self.assertRaises(ValueError):
            score_all([], TABLE, CLUBS, {})

    def test_duplicate_person(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            score_all([person("ED", BASE), person("ED", BASE)], TABLE, CLUBS, {})

    def test_logs_leader(self):
        with self.assertLogs("plpredict.scoring", "INFO") as logs:
            score_all([person("ED", BASE)], TABLE, CLUBS, {})
        self.assertTrue(any("leader ED" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
