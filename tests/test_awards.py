import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from plpredict.awards import (
    ManualAwardsError,
    christmas_table,
    load_manual_awards,
    resolve_awards,
)
from plpredict.fetch import Match, Scorer
from plpredict.models import AWARD_CATEGORIES, PredictionSet, TableRow
from plpredict.names import NameResolver
from tests.helpers import CLUBS, test_aliases

PROMOTED = ("Coventry City", "Hull City", "Ipswich Town")
LAST = {c: 50 for c in CLUBS if c not in PROMOTED}
PSET = PredictionSet(season_clubs=tuple(CLUBS), promoted_clubs=PROMOTED,
                     last_season_points=LAST, predictions=())


def table(**overrides):
    """20 rows in CLUBS order; overrides: club -> dict of field changes."""
    rows = []
    for i, club in enumerate(CLUBS):
        base = dict(club=club, position=i + 1, played=10, won=3, draw=2, lost=5,
                    goals_for=12, goals_against=12, points=40 - i)
        base.update(overrides.get(club, {}))
        rows.append(TableRow(**base))
    return rows


def m(home, away, hg, ag, when="2026-09-01", status="FINISHED"):
    return Match(datetime.fromisoformat(when).replace(tzinfo=timezone.utc), status,
                 home, away, hg, ag)


def resolve(tbl=None, matches=(), scorers=(), manual=None, today=date(2026, 10, 25)):
    return resolve_awards(tbl or table(), list(matches), list(scorers), PSET,
                          manual or {}, test_aliases(), today)


class TableAwardTests(unittest.TestCase):
    def test_goal_and_defense_leaders_with_ties(self):
        s = resolve(table(Arsenal={"goals_for": 30, "goals_against": 3},
                          Chelsea={"goals_for": 30},
                          **{"Hull City": {"goals_for": 2, "goals_against": 31}}))
        self.assertEqual(s["most_goals"].leaders, ("Arsenal", "Chelsea"))
        self.assertEqual(s["fewest_goals"].leaders, ("Hull City",))
        self.assertEqual(s["best_defense"].leaders, ("Arsenal",))
        self.assertEqual(s["worst_defense"].leaders, ("Hull City",))
        self.assertFalse(s["most_goals"].settled)

    def test_most_draws(self):
        s = resolve(table(Everton={"draw": 6}))
        self.assertEqual(s["most_draws"].leaders, ("Everton",))

    def test_highest_promoted(self):
        s = resolve()  # Coventry 7th, Hull 11th, Ipswich 12th in CLUBS order
        self.assertEqual(s["highest_promoted"].leaders, ("Coventry City",))

    def test_title_margin(self):
        # 1st is AFC Bournemouth (first in CLUBS), 2nd Arsenal on 39.
        s = resolve(table(**{"AFC Bournemouth": {"points": 45}}))
        self.assertEqual(s["title_margin"].leaders, (45 - 39,))

    def test_increase_and_dropoff_on_pace(self):
        # 10 played: 40 pts -> pace 152; 1 pt -> pace 3.8 vs last season 50.
        s = resolve(table(Arsenal={"points": 45},
                          Fulham={"points": 1},
                          **{"Coventry City": {"points": 60}}))  # promoted: excluded
        self.assertEqual(s["biggest_increase"].leaders, ("Arsenal",))
        self.assertEqual(s["biggest_dropoff"].leaders, ("Fulham",))

    def test_no_games_played_means_no_leaders(self):
        tbl = table(**{c: {"played": 0, "points": 0} for c in CLUBS})
        s = resolve(tbl)
        for key in ("most_goals", "title_margin", "biggest_increase", "highest_promoted"):
            self.assertEqual(s[key].leaders, (), key)

    def test_settled_when_season_over(self):
        tbl = table(**{c: {"played": 38} for c in CLUBS})
        self.assertTrue(resolve(tbl)["most_goals"].settled)


class ThresholdTests(unittest.TestCase):
    def test_over_90_reached(self):
        s = resolve(table(Arsenal={"points": 91}))
        self.assertEqual((s["any_over_90"].leaders, s["any_over_90"].settled), ((True,), True))

    def test_over_90_impossible(self):
        tbl = table(**{c: {"played": 37, "points": 80} for c in CLUBS})
        self.assertEqual(resolve(tbl)["any_over_90"].leaders, (False,))

    def test_over_90_undecided(self):
        self.assertEqual(resolve()["any_over_90"].leaders, ())

    def test_under_25_every_club_safe(self):
        tbl = table(**{c: {"points": 25} for c in CLUBS})
        self.assertEqual(resolve(tbl)["any_under_25"].leaders, (False,))

    def test_under_25_locked_yes(self):
        tbl = table(**{"Hull City": {"played": 36, "points": 18}})  # max 24
        s = resolve(tbl)["any_under_25"]
        self.assertEqual((s.leaders, s.settled), ((True,), True))

    def test_under_25_undecided(self):
        tbl = table(**{"Hull City": {"points": 5}})
        self.assertEqual(resolve(tbl)["any_under_25"].leaders, ())


class MatchAwardTests(unittest.TestCase):
    def test_clean_sheets_counts_both_sides(self):
        matches = [m("Arsenal", "Hull City", 2, 0), m("Chelsea", "Arsenal", 0, 0),
                   m("Leeds United", "Fulham", 1, 1), m("Everton", "Brentford", 0, 3, status="TIMED")]
        s = resolve(matches=matches)["most_clean_sheets"]
        self.assertEqual(s.leaders, ("Arsenal",))

    def test_clean_sheets_no_results(self):
        self.assertEqual(resolve()["most_clean_sheets"].leaders, ())

    def test_christmas_pending_before_25_dec(self):
        s = resolve(matches=[m("Arsenal", "Hull City", 2, 0)], today=date(2026, 12, 24))
        self.assertEqual(s["top_at_christmas"].leaders, ())
        self.assertIn("25 Dec 2026", s["top_at_christmas"].note)

    def test_christmas_table_ignores_later_results(self):
        matches = [
            m("Arsenal", "Hull City", 2, 0, "2026-09-01"),
            m("Chelsea", "Ipswich Town", 5, 0, "2026-12-20"),
            m("Hull City", "Chelsea", 9, 0, "2026-12-27"),  # after Christmas: ignored
            m("Fulham", "Leeds United", None, None, "2026-12-20", "POSTPONED"),
        ]
        s = resolve(matches=matches, today=date(2026, 12, 25))
        self.assertEqual(s["top_at_christmas"].leaders, ("Chelsea",))
        self.assertEqual(s["bottom_at_christmas"].leaders, ("Ipswich Town",))
        self.assertTrue(s["top_at_christmas"].settled)

    def test_christmas_table_tiebreaks(self):
        matches = [m("Arsenal", "Hull City", 2, 0), m("Chelsea", "Fulham", 3, 1),
                   m("Leeds United", "Everton", None, None, status="TIMED")]
        rows = christmas_table(matches)
        # Equal points and GD, so goals scored decide: Chelsea > Arsenal, Fulham > Hull.
        self.assertEqual([r[0] for r in rows[:2]], ["Chelsea", "Arsenal"])
        self.assertEqual([r[0] for r in rows[-2:]], ["Fulham", "Hull City"])
        # Clubs with no results yet still appear, on zero.
        self.assertIn(("Leeds United", 0, 0, 0), rows)


class ScorerAwardTests(unittest.TestCase):
    def test_leaders_use_picked_spelling(self):
        scorers = [Scorer("Erling Haaland", 12, 1), Scorer("Bruno Fernandes", 3, 8),
                   Scorer("Martin Ødegaard", 1, 8)]
        s = resolve(scorers=scorers)
        self.assertEqual(s["golden_boot"].leaders, ("Erling Haaland",))
        self.assertEqual(s["top_assister"].leaders, ("Bruno Fernandes", "Martin Ødegaard"))

    def test_no_assists_no_leader(self):
        s = resolve(scorers=[Scorer("Erling Haaland", 2, 0)])
        self.assertEqual(s["top_assister"].leaders, ())


class ResolveTests(unittest.TestCase):
    def test_every_category_present(self):
        s = resolve()
        self.assertEqual(set(s), {c.key for c in AWARD_CATEGORIES})
        self.assertEqual(s["pfa_player"].note, "hand entry pending")

    def test_manual_entries_override(self):
        from plpredict.models import AwardStatus
        manual = {"pfa_player": AwardStatus("pfa_player", ("Bruno Fernandes",), True)}
        self.assertEqual(resolve(manual=manual)["pfa_player"].leaders, ("Bruno Fernandes",))

    def test_empty_table_rejected(self):
        with self.assertRaises(ValueError):
            resolve_awards([], [], [], PSET, {}, test_aliases(), date(2026, 10, 25))


class ManualAwardsFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "manual.json"
        self.names = NameResolver(test_aliases(), CLUBS)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_normalises_each_kind(self):
        self.write({"_comment": "x",
                    "most_red_cards": {"leaders": ["Spurs"], "settled": False},
                    "first_sacked_manager": {"leaders": ["Jakirovich"], "settled": True},
                    "relegation_final_day": {"leaders": ["Hell nah"], "settled": True},
                    "pfa_player": {"leaders": [], "settled": False}})
        s = load_manual_awards(self.path, self.names)
        self.assertEqual(s["most_red_cards"].leaders, ("Tottenham Hotspur",))
        self.assertEqual(s["first_sacked_manager"].leaders, ("Sergej Jakirovic",))
        self.assertEqual(s["relegation_final_day"].leaders, (False,))
        self.assertEqual(s["pfa_player"].note, "hand entry pending")

    def test_missing_file_is_empty(self):
        with self.assertLogs("plpredict.awards", "WARNING"):
            self.assertEqual(load_manual_awards(self.path, self.names), {})

    def test_bad_json(self):
        self.path.write_text("{")
        with self.assertRaisesRegex(ManualAwardsError, "not valid JSON"):
            load_manual_awards(self.path, self.names)

    def test_non_manual_key_rejected(self):
        self.write({"golden_boot": {"leaders": ["Erling Haaland"]}})
        with self.assertRaisesRegex(ManualAwardsError, "not a hand-entry award"):
            load_manual_awards(self.path, self.names)

    def test_bad_entry_shape(self):
        self.write({"pfa_player": ["Bruno"]})
        with self.assertRaisesRegex(ManualAwardsError, "must be"):
            load_manual_awards(self.path, self.names)

    def test_unknown_name(self):
        self.write({"pfa_player": {"leaders": ["Messi"]}})
        with self.assertLogs("plpredict.names", "WARNING"):
            with self.assertRaisesRegex(ManualAwardsError, "Messi"):
                load_manual_awards(self.path, self.names)

    def test_repo_file_loads(self):
        repo = Path(__file__).resolve().parents[1] / "data" / "manual_awards.json"
        s = load_manual_awards(repo, self.names)
        self.assertEqual(len(s), 4)


if __name__ == "__main__":
    unittest.main()


class GameweekAwardTests(unittest.TestCase):
    def test_scorers_not_recorded(self):
        s = resolve_awards(table(), [], None, PSET, {}, test_aliases(), date(2026, 10, 25), gameweek=3)
        for key in ("golden_boot", "top_assister"):
            self.assertEqual((s[key].leaders, s[key].note), ((), "not recorded for this gameweek"))

    def test_hand_entry_counts_from_its_gameweek(self):
        from plpredict.models import AwardStatus
        manual = {"first_sacked_manager": AwardStatus("first_sacked_manager", ("Enzo Maresca",),
                                                      True, "", from_gameweek=9)}
        args = (table(), [], [], PSET, manual, test_aliases(), date(2026, 10, 25))
        self.assertEqual(resolve_awards(*args, gameweek=8)["first_sacked_manager"].leaders, ())
        self.assertEqual(resolve_awards(*args, gameweek=9)["first_sacked_manager"].leaders,
                         ("Enzo Maresca",))
        self.assertEqual(resolve_awards(*args)["first_sacked_manager"].leaders, ("Enzo Maresca",))


class ManualFromGameweekTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "manual.json"
        self.names = NameResolver(test_aliases(), CLUBS)

    def tearDown(self):
        self.dir.cleanup()

    def test_reads_from_gameweek(self):
        self.path.write_text(json.dumps({"pfa_player": {"leaders": ["Bruno F"], "from_gameweek": 38}}))
        self.assertEqual(load_manual_awards(self.path, self.names)["pfa_player"].from_gameweek, 38)

    def test_rejects_bad_from_gameweek(self):
        for bad in (0, 39, "9", True):
            self.path.write_text(json.dumps({"pfa_player": {"leaders": [], "from_gameweek": bad}}))
            with self.assertRaisesRegex(ManualAwardsError, "from_gameweek"):
                load_manual_awards(self.path, self.names)


from plpredict.awards import _race  # noqa: E402
from plpredict.models import RaceEntry  # noqa: E402


class RaceHelperTests(unittest.TestCase):
    def test_top_three_with_competition_ranks(self):
        items = [("a", 10), ("b", 8), ("c", 8), ("d", 7), ("e", 5)]
        race = _race(items, lambda kv: kv[1], lambda kv: kv[0])
        self.assertEqual(race, (RaceEntry(1, "a", 10), RaceEntry(2, "b", 8), RaceEntry(2, "c", 8)))

    def test_tie_for_third_keeps_everyone(self):
        items = [("a", 9), ("b", 8), ("c", 7), ("d", 7), ("e", 6)]
        race = _race(items, lambda kv: kv[1], lambda kv: kv[0])
        self.assertEqual([e.name for e in race], ["a", "b", "c", "d"])
        self.assertEqual([e.rank for e in race], [1, 2, 3, 3])

    def test_lowest_first_and_shown_value(self):
        items = [("a", 3.6), ("b", -2.4)]
        race = _race(items, lambda kv: kv[1], lambda kv: kv[0], best=min)
        self.assertEqual(race, (RaceEntry(1, "b", -2), RaceEntry(2, "a", 4)))

    def test_empty(self):
        self.assertEqual(_race([], lambda x: x, lambda x: x), ())


class AwardValueTests(unittest.TestCase):
    def test_golden_boot_value_and_race(self):
        # Spec example: Haaland 10, Joao Pedro 8, Saka 7.
        scorers = [Scorer("Bukayo Saka", 7, 3), Scorer("Erling Haaland", 10, 1),
                   Scorer("João Pedro", 8, 0), Scorer("Cole Palmer", 2, 4)]
        boot = resolve(scorers=scorers)["golden_boot"]
        self.assertEqual(boot.value, 10)
        self.assertEqual([(e.rank, e.name, e.value) for e in boot.race],
                         [(1, "Erling Haaland", 10), (2, "João Pedro", 8), (3, "Bukayo Saka", 7)])
        assists = resolve(scorers=scorers)["top_assister"]
        self.assertEqual((assists.value, [e.name for e in assists.race]),
                         (4, ["Cole Palmer", "Bukayo Saka", "Erling Haaland"]))

    def test_best_defense_shows_goals_conceded(self):
        s = resolve(table(Arsenal={"goals_against": 3}, Chelsea={"goals_against": 5},
                          Everton={"goals_against": 6}))["best_defense"]
        self.assertEqual(s.value, 3)
        self.assertEqual([(e.name, e.value) for e in s.race],
                         [("Arsenal", 3), ("Chelsea", 5), ("Everton", 6)])

    def test_clean_sheets_value(self):
        matches = [m("Arsenal", "Hull City", 2, 0), m("Chelsea", "Arsenal", 0, 0),
                   m("Leeds United", "Fulham", 1, 0)]
        s = resolve(matches=matches)["most_clean_sheets"]
        self.assertEqual((s.leaders, s.value), (("Arsenal",), 2))
        self.assertEqual([(e.rank, e.name) for e in s.race],
                         [(1, "Arsenal"), (2, "Chelsea"), (2, "Leeds United")])

    def test_title_margin_value_and_points_race(self):
        s = resolve(table(**{"AFC Bournemouth": {"points": 45}}))["title_margin"]
        self.assertEqual((s.leaders, s.value), ((6,), 6))
        self.assertEqual([(e.name, e.value) for e in s.race],
                         [("AFC Bournemouth", 45), ("Arsenal", 39), ("Aston Villa", 38)])

    def test_pace_change_rounded(self):
        s = resolve(table(Arsenal={"points": 45}))["biggest_increase"]
        self.assertEqual(s.race[0].name, "Arsenal")
        self.assertEqual(s.value, round(45 * 38 / 10 - 50))

    def test_promoted_position(self):
        self.assertEqual(resolve()["highest_promoted"].value, 7)

    def test_threshold_race_shows_nearest_clubs(self):
        s = resolve()
        self.assertEqual([e.name for e in s["any_over_90"].race][:1], ["AFC Bournemouth"])
        self.assertEqual(s["any_over_90"].value, None)
        self.assertEqual([e.value for e in s["any_under_25"].race], [21, 22, 23])

    def test_christmas_values_are_points(self):
        matches = [m("Arsenal", "Hull City", 2, 0, "2026-12-20"), m("Chelsea", "Fulham", 1, 1, "2026-12-20")]
        s = resolve(matches=matches, today=date(2026, 12, 25))
        self.assertEqual((s["top_at_christmas"].value, s["top_at_christmas"].race[0].name), (3, "Arsenal"))
        self.assertEqual(s["bottom_at_christmas"].value, 0)

    def test_hand_entries_have_no_value(self):
        self.assertIsNone(resolve()["pfa_player"].value)
        self.assertEqual(resolve()["pfa_player"].race, ())


class RedCardAwardTests(unittest.TestCase):
    CARDS = {"Chelsea": 4, "Everton": 4, "Leeds United": 2, "Arsenal": 1, "Fulham": 0}

    def test_automatic_from_counts(self):
        s = resolve_awards(table(), [], [], PSET, {}, test_aliases(), date(2026, 10, 25),
                           red_cards=self.CARDS)["most_red_cards"]
        self.assertEqual((s.leaders, s.value), (("Chelsea", "Everton"), 4))
        self.assertEqual([(e.rank, e.name) for e in s.race],
                         [(1, "Chelsea"), (1, "Everton"), (3, "Leeds United")])

    def test_empty_hand_entry_does_not_hide_it(self):
        from plpredict.models import AwardStatus
        manual = {"most_red_cards": AwardStatus("most_red_cards", (), False, "hand entry pending")}
        s = resolve_awards(table(), [], [], PSET, manual, test_aliases(), date(2026, 10, 25),
                           red_cards=self.CARDS)["most_red_cards"]
        self.assertEqual(s.leaders, ("Chelsea", "Everton"))

    def test_filled_hand_entry_overrides(self):
        from plpredict.models import AwardStatus
        manual = {"most_red_cards": AwardStatus("most_red_cards", ("Leeds United",), True)}
        s = resolve_awards(table(), [], [], PSET, manual, test_aliases(), date(2026, 10, 25),
                           red_cards=self.CARDS)["most_red_cards"]
        self.assertEqual(s.leaders, ("Leeds United",))

    def test_no_counts_falls_back_to_hand_entry(self):
        s = resolve_awards(table(), [], [], PSET, {}, test_aliases(), date(2026, 10, 25))["most_red_cards"]
        self.assertEqual((s.leaders, s.note), ((), "hand entry pending"))
