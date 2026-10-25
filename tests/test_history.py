import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plpredict.fetch import Match
from plpredict.history import (
    Adjustment,
    HistoryError,
    assign_gameweeks,
    expected_gameweek,
    final_gameweeks,
    gameweek_end,
    gameweek_windows,
    live_gameweek,
    load_adjustments,
    table_after,
)
from plpredict.names import NameResolver
from tests.helpers import CLUBS, test_aliases

SAT = datetime(2026, 8, 15, 14, tzinfo=timezone.utc)  # GW 1; one gameweek a week after


def m(home, away, hg, ag, gw, days=0.0, status="FINISHED"):
    when = SAT + timedelta(weeks=gw - 1, days=days)
    if status != "FINISHED":
        hg = ag = None
    return Match(when, status, home, away, hg, ag, matchday=gw)


def round_of(gw, results=None, status="FINISHED"):
    """Ten matches for a gameweek: CLUBS paired in order; results default 1-0 home."""
    pairs = list(zip(CLUBS[0::2], CLUBS[1::2]))
    results = results or {}
    return [m(h, a, *results.get(h, (1, 0)), gw, days=(i % 3) * 1.0, status=status)
            for i, (h, a) in enumerate(pairs)]


class WindowTests(unittest.TestCase):
    def test_window_spans_friday_to_monday(self):
        matches = [m("Arsenal", "Chelsea", 1, 0, 1, days=-1), m("Everton", "Fulham", 0, 0, 1),
                   m("Leeds United", "Hull City", 2, 1, 1, days=2)]
        w = gameweek_windows(matches)[1]
        self.assertEqual((w.start, w.end), (SAT - timedelta(days=1), SAT + timedelta(days=2)))

    def test_postponed_game_excluded_from_window(self):
        late = m("Arsenal", "Chelsea", 1, 0, 1, days=60)  # played two months late
        w = gameweek_windows(round_of(1)[1:] + [late])[1]
        self.assertLess(w.end, SAT + timedelta(days=5))


class ExpectedGameweekTests(unittest.TestCase):
    def setUp(self):
        self.matches = round_of(1) + round_of(2) + round_of(3) + round_of(4)
        self.windows = gameweek_windows(self.matches)

    def test_match_in_own_window_stays(self):
        self.assertEqual(expected_gameweek(self.matches[3], self.windows), 1)

    def test_monday_game_next_to_midweek_round_stays(self):
        # GW 2 on a Tuesday, 1 day after a Monday GW 1 match: GW 1 match stays put.
        monday = m("Arsenal", "Chelsea", 1, 0, 1, days=2)
        windows = gameweek_windows(round_of(1) + [monday] + [
            m("Everton", "Fulham", 1, 1, 2, days=-4)])
        self.assertEqual(expected_gameweek(monday, windows), 1)

    def test_postponed_game_counts_where_played(self):
        # Matchday 1 game played in GW 3's week.
        late = Match(SAT + timedelta(weeks=2, days=1), "FINISHED", "Arsenal", "Chelsea", 2, 2,
                     matchday=1)
        with self.assertLogs("plpredict.history", "INFO"):
            self.assertEqual(expected_gameweek(late, self.windows), 3)

    def test_rescheduled_but_unplayed_game_expected_in_new_week(self):
        later = Match(SAT + timedelta(weeks=3), "TIMED", "Arsenal", "Chelsea", None, None,
                      matchday=1)
        with self.assertLogs("plpredict.history", "INFO"):
            self.assertEqual(expected_gameweek(later, self.windows), 4)

    def test_postponed_without_date_is_not_expected(self):
        p = m("Arsenal", "Chelsea", 0, 0, 1, status="POSTPONED")
        self.assertIsNone(expected_gameweek(p, self.windows))

    def test_no_windows(self):
        self.assertIsNone(expected_gameweek(self.matches[0], {}))


class FinalityTests(unittest.TestCase):
    def test_final_and_live(self):
        matches = round_of(1) + round_of(2)
        matches += round_of(3)[:4] + round_of(3, status="TIMED")[4:]
        assigned = assign_gameweeks(matches)
        final = final_gameweeks(assigned)
        self.assertEqual(final, [1, 2])
        self.assertEqual(live_gameweek(assigned, final), 3)

    def test_between_gameweeks_no_live(self):
        assigned = assign_gameweeks(round_of(1) + round_of(2, status="TIMED"))
        final = final_gameweeks(assigned)
        self.assertEqual(final, [1])
        self.assertIsNone(live_gameweek(assigned, final))

    def test_postponed_game_does_not_block(self):
        matches = round_of(1)[1:] + [m("AFC Bournemouth", "Arsenal", 0, 0, 1, status="POSTPONED")]
        self.assertEqual(final_gameweeks(assign_gameweeks(matches)), [1])

    def test_unplayed_earlier_gameweek_stops_the_run(self):
        matches = round_of(1, status="TIMED") + round_of(2)
        self.assertEqual(final_gameweeks(assign_gameweeks(matches)), [])

    def test_nothing_started(self):
        assigned = assign_gameweeks(round_of(1, status="TIMED"))
        self.assertIsNone(live_gameweek(assigned, []))

    def test_gameweek_end(self):
        assigned = assign_gameweeks(round_of(1))
        self.assertEqual(gameweek_end(assigned, 1), SAT + timedelta(days=2))
        with self.assertRaises(HistoryError):
            gameweek_end(assigned, 5)


class TableAfterTests(unittest.TestCase):
    def setUp(self):
        # GW 1: every home side wins 1-0. GW 2: Arsenal (away in GW1) wins 5-0.
        self.matches = round_of(1) + [m("Arsenal", "AFC Bournemouth", 5, 0, 2)]
        self.assigned = assign_gameweeks(self.matches)

    def test_after_gameweek_one(self):
        rows = table_after(self.assigned, 1, CLUBS)
        self.assertEqual(len(rows), 20)
        self.assertEqual([r.position for r in rows], list(range(1, 21)))
        top = rows[0]
        self.assertEqual((top.points, top.played, top.won, top.goals_for), (3, 1, 1, 1))
        arsenal = next(r for r in rows if r.club == "Arsenal")
        self.assertEqual((arsenal.points, arsenal.lost, arsenal.goals_against), (0, 1, 1))

    def test_later_gameweek_included(self):
        rows = table_after(self.assigned, 2, CLUBS)
        arsenal = next(r for r in rows if r.club == "Arsenal")
        self.assertEqual((arsenal.points, arsenal.played, arsenal.goals_for), (3, 2, 5))
        self.assertEqual(rows[0].club, "Arsenal")  # +4 goal difference beats +1

    def test_tiebreak_by_name_when_all_equal(self):
        rows = table_after([], 1, CLUBS)
        self.assertEqual([r.club for r in rows], sorted(CLUBS))

    def test_deduction_applies_from_its_gameweek(self):
        adj = [Adjustment("Arsenal", -10, 2, "financial rules")]
        before = next(r for r in table_after(self.assigned, 1, CLUBS, adj) if r.club == "Arsenal")
        after = next(r for r in table_after(self.assigned, 2, CLUBS, adj) if r.club == "Arsenal")
        self.assertEqual(before.points, 0)
        self.assertEqual(after.points, 3 - 10)

    def test_unknown_club_in_match(self):
        bad = [(1, Match(SAT, "FINISHED", "Burnley", "Arsenal", 1, 0, matchday=1))]
        with self.assertRaisesRegex(HistoryError, "Burnley"):
            table_after(bad, 1, CLUBS)

    def test_gameweek_must_be_positive(self):
        with self.assertRaises(HistoryError):
            table_after(self.assigned, 0, CLUBS)


class LoadAdjustmentsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "adjustments.json"
        self.names = NameResolver(test_aliases(), CLUBS)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_loads_and_normalises(self):
        self.write({"adjustments": [{"club": "Spurs", "points": -6, "from_gameweek": 12,
                                     "reason": "fined"}]})
        self.assertEqual(load_adjustments(self.path, self.names),
                         [Adjustment("Tottenham Hotspur", -6, 12, "fined")])

    def test_missing_file_is_empty(self):
        with self.assertLogs("plpredict.history", "INFO"):
            self.assertEqual(load_adjustments(self.path, self.names), [])

    def test_bad_json(self):
        self.path.write_text("{")
        with self.assertRaisesRegex(HistoryError, "not valid JSON"):
            load_adjustments(self.path, self.names)

    def test_wrong_shape(self):
        self.write([{"club": "Arsenal"}])
        with self.assertRaisesRegex(HistoryError, "must be"):
            load_adjustments(self.path, self.names)

    def test_bad_values(self):
        for entry, msg in (({"club": "Arsenal", "points": "-6", "from_gameweek": 3}, "whole number"),
                           ({"club": "Arsenal", "points": -6, "from_gameweek": 40}, "1-38"),
                           ({"club": "Arsenal", "from_gameweek": 3}, "points")):
            self.write({"adjustments": [entry]})
            with self.assertRaisesRegex(HistoryError, msg):
                load_adjustments(self.path, self.names)

    def test_unknown_club(self):
        self.write({"adjustments": [{"club": "Wolves", "points": -3, "from_gameweek": 3}]})
        with self.assertLogs("plpredict.names", "WARNING"):
            with self.assertRaisesRegex(HistoryError, "Wolves"):
                load_adjustments(self.path, self.names)

    def test_repo_file_is_valid(self):
        repo = Path(__file__).resolve().parents[1] / "data" / "adjustments.json"
        self.assertEqual(load_adjustments(repo, self.names), [])


if __name__ == "__main__":
    unittest.main()


from plpredict.fetch import Scorer  # noqa: E402
from plpredict.history import (  # noqa: E402
    backfill_scorers,
    goals_in_gameweek,
    load_history,
    record_snapshot,
    save_history,
    scorers_through,
)


def fresh():
    return {"snapshots": {}, "backfill": {}, "backfill_mode": None, "red_cards": {}}


class HistoryFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "history.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_starts_fresh(self):
        with self.assertLogs("plpredict.history", "INFO"):
            self.assertEqual(load_history(self.path), fresh())

    def test_round_trip_and_only_writes_on_change(self):
        h = fresh()
        h["snapshots"]["3"] = [{"player": "Erling Haaland", "goals": 4, "assists": 0}]
        with self.assertLogs("plpredict.history", "INFO"):
            self.assertTrue(save_history(h, self.path))
        self.assertFalse(save_history(h, self.path))
        self.assertEqual(load_history(self.path), h)

    def test_bad_json(self):
        self.path.write_text("{")
        with self.assertRaisesRegex(HistoryError, "not valid JSON"):
            load_history(self.path)

    def test_bad_shapes(self):
        for data, msg in (([], "JSON object"), ({"snapshots": []}, "keyed by gameweek"),
                          ({"backfill_mode": "weekly"}, "unknown backfill_mode")):
            self.path.write_text(json.dumps(data))
            with self.assertRaisesRegex(HistoryError, msg):
                load_history(self.path)

    def test_repo_file_loads(self):
        repo = Path(__file__).resolve().parents[1] / "data" / "history.json"
        load_history(repo)


class SnapshotTests(unittest.TestCase):
    SCORERS = [Scorer("Erling Haaland", 5, 1)]

    def test_recorded_between_gameweeks(self):
        h = fresh()
        with self.assertLogs("plpredict.history", "INFO"):
            record_snapshot(h, self.SCORERS, [1, 2], None)
        self.assertEqual(h["snapshots"], {"2": [{"player": "Erling Haaland", "goals": 5, "assists": 1}]})

    def test_not_recorded_mid_gameweek_or_twice(self):
        h = fresh()
        record_snapshot(h, self.SCORERS, [1, 2], 3)
        self.assertEqual(h["snapshots"], {})
        h["snapshots"]["2"] = []
        record_snapshot(h, self.SCORERS, [1, 2], None)
        self.assertEqual(h["snapshots"]["2"], [])

    def test_nothing_final(self):
        h = fresh()
        record_snapshot(h, self.SCORERS, [], None)
        self.assertEqual(h["snapshots"], {})


class BackfillTests(unittest.TestCase):
    """Three gameweeks: Haaland scores 2, 1, 3; Saka 0, 2, 0 (8 goals total)."""

    PER_GW = {1: [Scorer("Erling Haaland", 2, 0)],
              2: [Scorer("Bukayo Saka", 2, 1), Scorer("Erling Haaland", 1, 0)],
              3: [Scorer("Erling Haaland", 3, 0)]}
    CUMULATIVE = {1: [Scorer("Erling Haaland", 2, 0)],
                  2: [Scorer("Erling Haaland", 3, 0), Scorer("Bukayo Saka", 2, 1)],
                  3: [Scorer("Erling Haaland", 6, 0), Scorer("Bukayo Saka", 2, 1)]}

    def setUp(self):
        self.assigned = [(1, m("Arsenal", "Chelsea", 2, 0, 1)), (2, m("Arsenal", "Chelsea", 3, 0, 2)),
                         (3, m("Arsenal", "Chelsea", 3, 0, 3))]

    def run_backfill(self, source, final=(1, 2, 3), history=None, max_calls=5):
        h = history or fresh()
        calls = []

        def fetch(client, gw):
            calls.append(gw)
            return source[gw]

        with self.assertLogs("plpredict.history", "INFO"):
            backfill_scorers(None, h, self.assigned, list(final), fetch, max_calls=max_calls)
        return h, calls

    def test_goals_in_gameweek(self):
        self.assertEqual(goals_in_gameweek(self.assigned, 2), 3)

    def test_detects_per_gameweek_and_sums(self):
        h, calls = self.run_backfill(self.PER_GW)
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(h["backfill_mode"], "per_gameweek")
        totals = {s.player: s.goals for s in scorers_through(h, 3)}
        self.assertEqual(totals, {"Erling Haaland": 6, "Bukayo Saka": 2})

    def test_detects_cumulative(self):
        h, _ = self.run_backfill(self.CUMULATIVE)
        self.assertEqual(h["backfill_mode"], "cumulative")
        totals = {s.player: s.goals for s in scorers_through(h, 2)}
        self.assertEqual(totals, {"Erling Haaland": 3, "Bukayo Saka": 2})

    def test_gameweek_one_same_either_way(self):
        h, _ = self.run_backfill(self.PER_GW, final=(1,))
        self.assertIsNone(h["backfill_mode"])
        self.assertEqual(scorers_through(h, 1), [Scorer("Erling Haaland", 2, 0)])

    def test_call_limit_and_resume(self):
        h, calls = self.run_backfill(self.PER_GW, max_calls=2)
        self.assertEqual(calls, [1, 2])
        self.assertIsNone(scorers_through(h, 3))  # not recorded yet
        h, calls = self.run_backfill(self.PER_GW, history=h)
        self.assertEqual(calls, [3])

    def test_snapshot_wins_and_is_not_refetched(self):
        h = fresh()
        h["snapshots"]["2"] = [{"player": "Cole Palmer", "goals": 4, "assists": 2}]
        h, calls = self.run_backfill(self.PER_GW, history=h)
        self.assertNotIn(2, calls)
        self.assertEqual(scorers_through(h, 2), [Scorer("Cole Palmer", 4, 2)])

    def test_unrecorded_gameweek(self):
        self.assertIsNone(scorers_through(fresh(), 4))


from plpredict.history import goals_through, validate_backfill  # noqa: E402


class IgnoredFilterTests(unittest.TestCase):
    """football-data.org ignores scorers?matchday=N and returns today's totals."""

    TODAY = [Scorer("Erling Haaland", 9, 1), Scorer("Alexander Isak", 6, 0)]  # 15 goals listed

    def setUp(self):
        # Gameweeks of 3 goals each: 3, 6, 9 scored through GW 1-3.
        self.assigned = [(gw, m("Arsenal", "Chelsea", 2, 1, gw)) for gw in (1, 2, 3)]

    def test_goals_through(self):
        self.assertEqual([goals_through(self.assigned, g) for g in (1, 2, 3)], [3, 6, 9])

    def test_detected_on_first_call_and_backfill_stops(self):
        h = fresh()
        calls = []

        def fetch(client, gw):
            calls.append(gw)
            return self.TODAY

        with self.assertLogs("plpredict.history", "WARNING") as logs:
            backfill_scorers(None, h, self.assigned, [1, 2, 3], fetch)
        self.assertEqual(calls, [1])
        self.assertEqual((h["backfill_mode"], h["backfill"]), ("ignored", {}))
        self.assertIn("ignores the matchday filter", logs.output[0])
        self.assertEqual(backfill_scorers(None, h, self.assigned, [1, 2, 3], fetch), 0)
        self.assertEqual(calls, [1])  # never asks again
        self.assertIsNone(scorers_through(h, 2))

    def test_existing_bad_history_is_repaired(self):
        # What the first live runs saved: today's list stored for every gameweek.
        rows = [{"player": s.player, "goals": s.goals, "assists": s.assists} for s in self.TODAY]
        h = {"snapshots": {}, "backfill": {"1": rows, "2": rows, "3": rows}, "backfill_mode": "cumulative"}
        with self.assertLogs("plpredict.history", "WARNING"):
            self.assertTrue(validate_backfill(h, self.assigned))
        self.assertEqual((h["backfill_mode"], h["backfill"]), ("ignored", {}))
        self.assertIsNone(scorers_through(h, 1))
        self.assertFalse(validate_backfill(h, self.assigned))

    def test_valid_history_left_alone(self):
        h = {"snapshots": {}, "backfill": {"1": [{"player": "Erling Haaland", "goals": 2, "assists": 0}]},
             "backfill_mode": None}
        self.assertFalse(validate_backfill(h, self.assigned))
        self.assertIn("1", h["backfill"])

    def test_snapshots_still_used_when_filter_ignored(self):
        h = {"snapshots": {"3": [{"player": "Erling Haaland", "goals": 5, "assists": 0}]},
             "backfill": {}, "backfill_mode": "ignored"}
        self.assertEqual(scorers_through(h, 3), [Scorer("Erling Haaland", 5, 0)])

    def test_ignored_mode_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            path.write_text(json.dumps({"snapshots": {}, "backfill": {}, "backfill_mode": "ignored"}))
            self.assertEqual(load_history(path)["backfill_mode"], "ignored")


from plpredict.history import record_red_cards, red_cards_through  # noqa: E402


class RedCardHistoryTests(unittest.TestCase):
    def test_snapshot_between_gameweeks_only(self):
        h = fresh()
        record_red_cards(h, {"Chelsea": 2}, [1, 2], 3)  # mid-gameweek: skip
        self.assertEqual(h["red_cards"], {})
        with self.assertLogs("plpredict.history", "INFO"):
            record_red_cards(h, {"Chelsea": 2, "Arsenal": 1}, [1, 2], None)
        self.assertEqual(red_cards_through(h, 2), {"Arsenal": 1, "Chelsea": 2})
        record_red_cards(h, {"Chelsea": 9}, [1, 2], None)  # never overwritten
        self.assertEqual(red_cards_through(h, 2)["Chelsea"], 2)

    def test_missing_counts_or_gameweeks(self):
        h = {"snapshots": {}, "backfill": {}, "backfill_mode": None}  # older file without red_cards
        record_red_cards(h, None, [1], None)
        record_red_cards(h, {"Chelsea": 1}, [], None)
        self.assertIsNone(red_cards_through(h, 1))
