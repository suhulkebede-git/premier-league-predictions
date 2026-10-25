import unittest
from datetime import datetime, timezone

from plpredict.fetch import ApiError, Client, api_club, fetch_matches, fetch_scorers, fetch_table
from plpredict.names import NameResolver, UnknownNameError
from tests.fakes import FakeResponse, FakeSession, match, network_error, standings_body
from tests.helpers import CLUBS, test_aliases


def client(*responses, retries=2):
    session = FakeSession(*responses)
    sleeps = []
    return Client("token", session=session, retries=retries, sleep=sleeps.append), session, sleeps


def resolver():
    return NameResolver(test_aliases(), CLUBS)


class ClientTests(unittest.TestCase):
    def test_sets_token_header(self):
        _, session, _ = client()
        self.assertEqual(session.headers["X-Auth-Token"], "token")

    def test_requires_token(self):
        with self.assertRaisesRegex(ValueError, "API key"):
            Client("  ", session=FakeSession())

    def test_success(self):
        c, session, sleeps = client(FakeResponse(body={"ok": 1}))
        self.assertEqual(c.get("/x", {"a": 1}), {"ok": 1})
        self.assertEqual(session.calls, [("https://api.football-data.org/v4/x", {"a": 1})])
        self.assertEqual(sleeps, [])

    def test_retries_rate_limit_then_succeeds(self):
        c, _, sleeps = client(FakeResponse(429, reason="Too Many"), FakeResponse(body={"ok": 1}))
        with self.assertLogs("plpredict.fetch", "WARNING"):
            self.assertEqual(c.get("/x"), {"ok": 1})
        self.assertEqual(sleeps, [6.0])

    def test_retries_network_error_then_succeeds(self):
        c, _, _ = client(network_error(), FakeResponse(body={"ok": 1}))
        with self.assertLogs("plpredict.fetch", "WARNING"):
            self.assertEqual(c.get("/x"), {"ok": 1})

    def test_gives_up_after_retries(self):
        c, _, sleeps = client(*[FakeResponse(503, reason="Down")] * 3)
        with self.assertLogs("plpredict.fetch", "WARNING"):
            with self.assertRaisesRegex(ApiError, "giving up after 3 attempts"):
                c.get("/x")
        self.assertEqual(sleeps, [6.0, 12.0])

    def test_auth_error_not_retried(self):
        c, session, _ = client(FakeResponse(403, reason="Forbidden"))
        with self.assertRaisesRegex(ApiError, "check the FOOTBALL_DATA_TOKEN secret"):
            c.get("/x")
        self.assertEqual(len(session.calls), 1)

    def test_bad_request_shows_api_message(self):
        body = {"message": "Your API token is invalid.", "errorCode": 400}
        c, session, _ = client(FakeResponse(400, body=body, reason=""))
        with self.assertRaises(ApiError) as ctx:
            c.get("/competitions/PL/standings")
        self.assertEqual(str(ctx.exception),
                         "/competitions/PL/standings: HTTP 400: Your API token is invalid. "
                         "(check the FOOTBALL_DATA_TOKEN secret)")
        self.assertEqual(len(session.calls), 1)

    def test_error_detail_falls_back_to_text(self):
        c, _, _ = client(FakeResponse(404, bad_json=True, reason="Not Found", text="no such page"))
        with self.assertRaisesRegex(ApiError, "HTTP 404: no such page$"):
            c.get("/x")

    def test_error_detail_falls_back_to_reason(self):
        c, _, _ = client(FakeResponse(404, bad_json=True, reason="Not Found"))
        with self.assertRaisesRegex(ApiError, "HTTP 404: Not Found$"):
            c.get("/x")

    def test_bad_json(self):
        c, _, _ = client(FakeResponse(bad_json=True))
        with self.assertRaisesRegex(ApiError, "not JSON"):
            c.get("/x")

    def test_non_object_json(self):
        c, _, _ = client(FakeResponse(body=[1, 2]))
        with self.assertRaisesRegex(ApiError, "JSON object"):
            c.get("/x")


class ApiClubTests(unittest.TestCase):
    def test_suffixes_and_prefixes(self):
        names = resolver()
        self.assertEqual(api_club("Hull City AFC", names), "Hull City")
        self.assertEqual(api_club("Sunderland AFC", names), "Sunderland")
        self.assertEqual(api_club("AFC Bournemouth", names), "AFC Bournemouth")
        self.assertEqual(api_club("Tottenham Hotspur FC", names), "Tottenham Hotspur")
        self.assertEqual(names.unmapped, [])

    def test_unknown_club_collected(self):
        names = resolver()
        with self.assertLogs("plpredict.names", "WARNING"):
            self.assertIsNone(api_club("Burnley FC", names))
        self.assertEqual(names.unmapped[0][:2], ("clubs", "Burnley FC"))


class FetchTableTests(unittest.TestCase):
    def test_parses_total_table(self):
        c, session, _ = client(FakeResponse(body=standings_body()))
        rows = fetch_table(c, resolver())
        self.assertEqual(len(rows), 20)
        self.assertEqual((rows[0].club, rows[0].position, rows[0].points), (CLUBS[0], 1, 30))
        self.assertEqual(rows[10].club, "Hull City")
        self.assertTrue(session.calls[0][0].endswith("/competitions/PL/standings"))

    def test_unknown_team_raises(self):
        body = standings_body()
        body["standings"][1]["table"][0]["team"]["name"] = "Burnley FC"
        c, _, _ = client(FakeResponse(body=body))
        with self.assertLogs("plpredict.names", "WARNING"):
            with self.assertRaises(UnknownNameError):
                fetch_table(c, resolver())

    def test_missing_total_table(self):
        c, _, _ = client(FakeResponse(body={"standings": []}))
        with self.assertRaisesRegex(ApiError, "standings response shape"):
            fetch_table(c, resolver())

    def test_non_integer_field(self):
        body = standings_body()
        body["standings"][1]["table"][3]["points"] = "12"
        c, _, _ = client(FakeResponse(body=body))
        with self.assertRaisesRegex(ApiError, "points is '12'"):
            fetch_table(c, resolver())


class FetchMatchesTests(unittest.TestCase):
    def test_parses_matches(self):
        body = {"matches": [
            match("Arsenal", "Hull City", 3, 0),
            match("Chelsea", "Fulham", None, None, "2026-12-26T15:00:00Z", "TIMED"),
        ]}
        c, _, _ = client(FakeResponse(body=body))
        matches = fetch_matches(c, resolver())
        self.assertEqual((matches[0].home, matches[0].away), ("Arsenal", "Hull City"))
        self.assertTrue(matches[0].finished)
        self.assertFalse(matches[1].finished)
        self.assertEqual(matches[1].utc_date, datetime(2026, 12, 26, 15, tzinfo=timezone.utc))

    def test_bad_date(self):
        bad = match("Arsenal", "Hull City", 1, 0, date="yesterday")
        c, _, _ = client(FakeResponse(body={"matches": [bad]}))
        with self.assertRaisesRegex(ApiError, "matches response shape"):
            fetch_matches(c, resolver())


class FetchScorersTests(unittest.TestCase):
    def test_parses_and_treats_null_assists_as_zero(self):
        body = {"scorers": [
            {"player": {"name": "Erling Haaland"}, "team": {"name": "Manchester City FC"},
             "goals": 12, "assists": None},
            {"player": {"name": "Martin Ødegaard"}, "team": {"name": "Arsenal FC"},
             "goals": 2, "assists": 7},
        ]}
        c, session, _ = client(FakeResponse(body=body))
        scorers = fetch_scorers(c)
        self.assertEqual((scorers[0].goals, scorers[0].assists), (12, 0))
        self.assertEqual(scorers[1].player, "Martin Ødegaard")
        self.assertEqual(session.calls[0][1], {"limit": 200})

    def test_bad_shape(self):
        c, _, _ = client(FakeResponse(body={"scorers": [{"goals": 3}]}))
        with self.assertRaisesRegex(ApiError, "scorers response shape"):
            fetch_scorers(c)

    def test_limit_must_be_positive(self):
        c, _, _ = client()
        with self.assertRaises(ValueError):
            fetch_scorers(c, limit=0)


if __name__ == "__main__":
    unittest.main()
