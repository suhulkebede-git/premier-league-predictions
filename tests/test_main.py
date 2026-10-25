import json
import logging
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from plpredict.fetch import ApiError, Client
from plpredict.main import cli, describe_token, run, write_json_atomic
from tests.fakes import FakeResponse, FakeSession, match, standings_body
from tests.helpers import CLUBS, write_sheet

REPO_DATA = Path(__file__).resolve().parents[1] / "data"
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def api_responses():
    scorers = {"scorers": [{"player": {"name": "Erling Haaland"}, "goals": 11, "assists": 2}]}
    return (FakeResponse(body=standings_body()),
            FakeResponse(body={"matches": [match("Arsenal", "Hull City", 2, 0)]}),
            FakeResponse(body=scorers))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        shutil.copytree(REPO_DATA, self.data)
        self.out = Path(self.dir.name) / "site" / "standings.json"

    def tearDown(self):
        self.dir.cleanup()

    def client(self, *responses):
        return Client("t", session=FakeSession(*responses), retries=0, sleep=lambda s: None)

    def test_writes_complete_standings(self):
        with self.assertLogs("plpredict", "INFO"):
            run(data_dir=self.data, out_path=self.out, client=self.client(*api_responses()),
                today=date(2026, 10, 25), now=NOW)
        data = json.loads(self.out.read_text())
        self.assertEqual(data["updated_at"], "2026-10-25T12:00:00+00:00")
        self.assertEqual(data["games_played"], 10)
        self.assertEqual(len(data["table"]), 20)
        self.assertEqual(len(data["awards"]), 20)
        self.assertEqual(len(data["scores"]), 9)
        self.assertEqual(set(data["predictions"]),
                         {"ED", "LUKE", "OREVA", "IYIN", "PEDRO", "SUHUL", "TINASHE", "UGWE", "JACK"})
        self.assertEqual([s["rank"] for s in data["scores"]],
                         sorted(s["rank"] for s in data["scores"]))
        boot = next(a for a in data["awards"] if a["key"] == "golden_boot")
        self.assertEqual((boot["label"], boot["leaders"]), ("Golden Boot Winner", ["Erling Haaland"]))
        # Everyone who picked Haaland got the 10.
        for s in data["scores"]:
            picked = data["predictions"][s["person"]]["awards"]["golden_boot"]["value"]
            self.assertEqual("golden_boot" in s["hits"]["awards"], picked == "Erling Haaland")
            self.assertEqual(s["total"], sum(s["breakdown"].values()))

    def test_api_failure_leaves_previous_output(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_text('{"previous": true}')
        with self.assertRaises(ApiError):
            run(data_dir=self.data, out_path=self.out,
                client=self.client(FakeResponse(500, reason="Down")), now=NOW)
        self.assertEqual(json.loads(self.out.read_text()), {"previous": True})


class WriteJsonAtomicTests(unittest.TestCase):
    def test_failed_write_keeps_old_file_and_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.json"
            path.write_text('{"old": 1}')
            with mock.patch("plpredict.main.os.replace", side_effect=OSError("disk full")):
                with self.assertLogs("plpredict.main", "ERROR"):
                    with self.assertRaises(OSError):
                        write_json_atomic({"new": 1}, path)
            self.assertEqual(json.loads(path.read_text()), {"old": 1})
            self.assertEqual([p.name for p in Path(d).iterdir()], ["out.json"])


class DescribeTokenTests(unittest.TestCase):
    GOOD = "0123456789abcdef0123456789abcdef"

    def test_valid_token(self):
        ok, desc = describe_token(self.GOOD)
        self.assertTrue(ok)
        self.assertRegex(desc, r"^32 characters, sha256 fingerprint [0-9a-f]{8}$")
        self.assertNotIn(self.GOOD[:6], desc)

    def test_fingerprint_is_stable_and_distinct(self):
        self.assertEqual(describe_token(self.GOOD)[1], describe_token(self.GOOD)[1])
        self.assertNotEqual(describe_token(self.GOOD)[1], describe_token("f" * 32)[1])

    def test_quotes(self):
        for token in (f'"{self.GOOD}"', f"\u201c{self.GOOD}\u201d"):
            ok, desc = describe_token(token)
            self.assertFalse(ok)
            self.assertIn("quote marks", desc)

    def test_header_prefix(self):
        ok, desc = describe_token("X-Auth-Token:" + self.GOOD)
        self.assertFalse(ok)
        self.assertIn("not 'X-Auth-Token:'", desc)

    def test_inner_whitespace(self):
        ok, desc = describe_token(self.GOOD[:16] + "\n" + self.GOOD[16:])
        self.assertIn("spaces or line breaks", desc)

    def test_wrong_shape(self):
        ok, desc = describe_token("abc123")
        self.assertFalse(ok)
        self.assertIn("6 characters", desc)
        self.assertIn("not 32 lowercase hex", desc)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        shutil.copytree(REPO_DATA, self.data)
        root = logging.getLogger()
        self._saved = (root.level, list(root.handlers))

    def tearDown(self):
        self.dir.cleanup()
        root = logging.getLogger()
        root.setLevel(self._saved[0])
        root.handlers[:] = self._saved[1]

    def test_missing_token_exits_2(self):
        with mock.patch.dict("os.environ", {"FOOTBALL_DATA_TOKEN": ""}):
            with self.assertLogs("plpredict.main", "ERROR"):
                self.assertEqual(cli(["--data-dir", str(self.data), "run"]), 2)

    def test_malformed_token_exits_2_without_calling_api(self):
        with mock.patch.dict("os.environ", {"FOOTBALL_DATA_TOKEN": '"abc"'}), \
                mock.patch("plpredict.main.run") as run_mock:
            with self.assertLogs("plpredict.main", "ERROR") as logs:
                self.assertEqual(cli(["--data-dir", str(self.data), "run"]), 2)
        run_mock.assert_not_called()
        self.assertIn("quote marks", logs.output[0])

    def test_logs_token_fingerprint(self):
        token = "0123456789abcdef0123456789abcdef"
        with mock.patch.dict("os.environ", {"FOOTBALL_DATA_TOKEN": f"  {token}\n"}), \
                mock.patch("plpredict.main.run"):
            with self.assertLogs("plpredict.main", "INFO") as logs:
                self.assertEqual(cli(["--data-dir", str(self.data), "run"]), 0)
        self.assertTrue(any("sha256 fingerprint" in line for line in logs.output))
        self.assertFalse(any(token in line for line in logs.output))

    def test_fingerprint_notice_in_actions(self):
        token = "0123456789abcdef0123456789abcdef"
        env = {"FOOTBALL_DATA_TOKEN": token, "GITHUB_ACTIONS": "true"}
        with mock.patch.dict("os.environ", env), mock.patch("plpredict.main.run"), \
                mock.patch("sys.stdout") as out:
            with self.assertLogs("plpredict.main", "INFO"):
                cli(["--data-dir", str(self.data), "run"])
        written = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertTrue(written.startswith("::notice title=FOOTBALL_DATA_TOKEN::32 characters"))
        self.assertNotIn(token, written)

    def test_api_error_exits_1(self):
        with mock.patch.dict("os.environ", {"FOOTBALL_DATA_TOKEN": "0" * 32}), \
                mock.patch("plpredict.main.run", side_effect=ApiError("down")):
            with self.assertLogs("plpredict.main", "ERROR") as logs:
                self.assertEqual(cli(["--data-dir", str(self.data), "run"]), 1)
        self.assertIn("previous output left unchanged", logs.output[0])

    def test_error_becomes_github_annotation_in_actions(self):
        env = {"FOOTBALL_DATA_TOKEN": "0" * 32, "GITHUB_ACTIONS": "true"}
        with mock.patch.dict("os.environ", env), \
                mock.patch("plpredict.main.run", side_effect=ApiError("HTTP 403\nForbidden 100%")), \
                mock.patch("sys.stdout") as out:
            with self.assertLogs("plpredict.main", "ERROR"):
                self.assertEqual(cli(["--data-dir", str(self.data), "run"]), 1)
        written = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("::error title=plpredict run failed::"
                      "ApiError: HTTP 403%0AForbidden 100%25\n", written)

    def test_no_annotation_outside_actions(self):
        with mock.patch.dict("os.environ", {"FOOTBALL_DATA_TOKEN": "0" * 32, "GITHUB_ACTIONS": ""}), \
                mock.patch("plpredict.main.run", side_effect=ApiError("down")), \
                mock.patch("sys.stdout") as out:
            with self.assertLogs("plpredict.main", "ERROR"):
                cli(["--data-dir", str(self.data), "run"])
        out.write.assert_not_called()

    def test_import_sheet(self):
        xlsx = write_sheet(Path(self.dir.name) / "p.xlsx", [("ED", list(CLUBS), None)])
        self.assertEqual(cli(["--data-dir", str(self.data), "import-sheet", str(xlsx)]), 0)
        saved = json.loads((self.data / "predictions.json").read_text())
        self.assertEqual([p["person"] for p in saved["predictions"]], ["ED"])

    def test_bad_sheet_exits_1(self):
        with self.assertLogs("plpredict.main", "ERROR"):
            self.assertEqual(
                cli(["--data-dir", str(self.data), "import-sheet", "/nonexistent.xlsx"]), 1)

    def test_bad_log_level_is_usage_error(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                cli(["--log-level", "LOUD", "run"])


if __name__ == "__main__":
    unittest.main()


from plpredict.fetch import Match  # noqa: E402
from plpredict.main import load_people, table_updated  # noqa: E402


def gameweek_matches(gw, finished=10, last_updated="2026-09-01T18:00:00Z"):
    """Ten API-shaped matches for gameweek gw (one week apart), the first
    ``finished`` of them played (home side wins 2-1)."""
    out = []
    pairs = list(zip(CLUBS[0::2], CLUBS[1::2]))
    for i, (h, a) in enumerate(pairs):
        date = f"2026-08-{15 + 7 * (gw - 1):02d}T14:00:00Z" if gw <= 2 else f"2026-08-29T14:00:00Z"
        if i < finished:
            out.append(match(h, a, 2, 1, date=date, matchday=gw, last_updated=last_updated))
        else:
            out.append(match(h, a, None, None, date=date, status="TIMED", matchday=gw))
    return out


def scorers_body(*rows):
    return {"scorers": [{"player": {"name": p}, "goals": g, "assists": a} for p, g, a in rows]}


class GameweekRunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        shutil.copytree(REPO_DATA, self.data)
        (self.data / "history.json").write_text(
            '{"backfill": {}, "backfill_mode": null, "snapshots": {}}\n')
        self.out = Path(self.dir.name) / "site" / "standings.json"

    def tearDown(self):
        self.dir.cleanup()

    def run_with(self, matches, *scorer_bodies):
        session = FakeSession(FakeResponse(body=standings_body()),
                              FakeResponse(body={"matches": matches}),
                              *[FakeResponse(body=b) for b in scorer_bodies])
        client = Client("t", session=session, retries=0, sleep=lambda s: None)
        with self.assertLogs("plpredict", "INFO"):
            run(data_dir=self.data, out_path=self.out, client=client,
                today=date(2026, 9, 1), now=NOW)
        return json.loads(self.out.read_text()), session  # what the site reads

    def test_finished_gameweeks_live_gameweek_and_backfill(self):
        matches = gameweek_matches(1) + gameweek_matches(2) + gameweek_matches(3, finished=1)
        data, session = self.run_with(
            matches,
            scorers_body(("Erling Haaland", 5, 0)),                     # live
            scorers_body(("Erling Haaland", 2, 0)),                     # matchday 1
            scorers_body(("Erling Haaland", 4, 0), ("Bukayo Saka", 1, 2)))  # matchday 2
        self.assertEqual([g["gameweek"] for g in data["gameweeks"]], [1, 2])
        self.assertEqual((data["live"]["gameweek"], data["live"]["in_progress"]), (3, True))
        self.assertEqual(data["scores"], data["live"]["scores"])
        self.assertEqual(len(data["gameweeks"][0]["scores"]), 9)
        self.assertEqual([c[1] for c in session.calls[3:]],
                         [{"limit": 200, "matchday": 1}, {"limit": 200, "matchday": 2}])
        history = json.loads((self.data / "history.json").read_text())
        self.assertEqual(sorted(history["backfill"]), ["1", "2"])
        # GW 2's list has 5 goals but its matches had 30: one gameweek's scorers.
        self.assertEqual(history["backfill_mode"], "per_gameweek")
        boot = next(a for a in data["gameweeks"][1]["awards"] if a["key"] == "golden_boot")
        self.assertEqual(boot["leaders"], ["Erling Haaland"])  # 2 + 4 = 6 through GW 2
        gw1_table = data["gameweeks"][0]["table"]
        self.assertEqual(gw1_table[0]["played"], 1)
        self.assertEqual(data["table_updated_at"], "2026-09-01T18:00:00+00:00")
        self.assertEqual(data["table_updated_source"], "api_last_updated")
        self.assertEqual(data["people"]["UGWE"], {"name": "Michael Ugwe", "initials": "MU"})

    def test_between_gameweeks_records_snapshot_without_backfill(self):
        matches = gameweek_matches(1) + gameweek_matches(2, finished=0)
        data, session = self.run_with(matches, scorers_body(("Erling Haaland", 2, 1)))
        self.assertEqual(len(session.calls), 3)  # no backfill call needed
        self.assertEqual((data["live"]["gameweek"], data["live"]["in_progress"]), (1, False))
        history = json.loads((self.data / "history.json").read_text())
        self.assertEqual(history["snapshots"]["1"],
                         [{"player": "Erling Haaland", "goals": 2, "assists": 1}])
        boot = next(a for a in data["gameweeks"][0]["awards"] if a["key"] == "golden_boot")
        self.assertEqual(boot["leaders"], ["Erling Haaland"])

    def test_before_the_season(self):
        data, _ = self.run_with(gameweek_matches(1, finished=0), scorers_body())
        self.assertEqual(data["gameweeks"], [])
        self.assertEqual((data["live"]["gameweek"], data["live"]["in_progress"]), (None, False))
        self.assertIsNone(data["table_updated_at"])


class TableUpdatedTests(unittest.TestCase):
    def m(self, status="FINISHED", last=None, day=1):
        when = datetime(2026, 9, day, 14, tzinfo=timezone.utc)
        goals = (1, 0) if status == "FINISHED" else (None, None)
        return Match(when, status, "Arsenal", "Chelsea", *goals, matchday=1, last_updated=last)

    def test_prefers_api_last_updated(self):
        late = datetime(2026, 9, 2, 9, tzinfo=timezone.utc)
        self.assertEqual(table_updated([self.m(last=late), self.m(day=3)]),
                         ("2026-09-02T09:00:00+00:00", "api_last_updated"))

    def test_falls_back_to_latest_result(self):
        self.assertEqual(table_updated([self.m(day=1), self.m(day=4), self.m("TIMED", day=9)]),
                         ("2026-09-04T14:00:00+00:00", "last_result_kickoff"))

    def test_no_results(self):
        self.assertEqual(table_updated([self.m("TIMED")]), (None, "none"))


class LoadPeopleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "people.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_repo_file(self):
        people = load_people(REPO_DATA / "people.json")
        self.assertEqual(people["SUHUL"], {"name": "Suhul K", "initials": "SK"})

    def test_missing_file(self):
        with self.assertLogs("plpredict.main", "WARNING"):
            self.assertEqual(load_people(self.path), {})

    def test_bad_files(self):
        for text in ("{", '{"people": []}', '{"people": {"ED": {"name": "Ed"}}}'):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                load_people(self.path)


class TableUpdatedInPlayTests(unittest.TestCase):
    def test_in_play_match_counts(self):
        done = Match(datetime(2026, 10, 25, 14, tzinfo=timezone.utc), "FINISHED", "Arsenal", "Chelsea",
                     1, 0, matchday=9, last_updated=datetime(2026, 10, 25, 16, tzinfo=timezone.utc))
        live = Match(datetime(2026, 10, 25, 16, 30, tzinfo=timezone.utc), "IN_PLAY", "Everton", "Fulham",
                     0, 0, matchday=9, last_updated=datetime(2026, 10, 25, 17, 2, tzinfo=timezone.utc))
        later = Match(datetime(2026, 11, 1, 15, tzinfo=timezone.utc), "TIMED", "Leeds United", "Hull City",
                      None, None, matchday=10, last_updated=datetime(2026, 10, 26, 9, tzinfo=timezone.utc))
        self.assertEqual(table_updated([done, live, later]),
                         ("2026-10-25T17:02:00+00:00", "api_last_updated"))
