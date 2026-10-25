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
