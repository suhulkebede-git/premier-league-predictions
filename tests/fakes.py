"""Fake HTTP session and API payloads shaped like football-data.org v4."""

from __future__ import annotations

import requests

from tests.helpers import CLUBS

API_NAMES = {
    "AFC Bournemouth": "AFC Bournemouth", "Hull City": "Hull City AFC",
    "Sunderland": "Sunderland AFC", "Brighton & Hove Albion": "Brighton & Hove Albion FC",
}


def api_name(club: str) -> str:
    return API_NAMES.get(club, f"{club} FC")


class FakeResponse:
    def __init__(self, status_code=200, body=None, reason="OK", bad_json=False, text="",
                 headers=None):
        self.status_code, self._body, self.reason, self._bad = status_code, body, reason, bad_json
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, *responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def standings_body(order=CLUBS, *, played=10, points=None):
    rows = []
    for i, club in enumerate(order):
        pts = points[i] if points else 30 - i
        rows.append({
            "position": i + 1, "team": {"id": 100 + i, "name": api_name(club)},
            "playedGames": played, "won": pts // 3, "draw": pts % 3,
            "lost": played - pts // 3 - pts % 3, "points": pts,
            "goalsFor": 20 - i, "goalsAgainst": 5 + i, "goalDifference": 15 - 2 * i,
        })
    return {"standings": [{"type": "HOME", "table": []}, {"type": "TOTAL", "table": rows}]}


def match(home, away, hg, ag, date="2026-09-01T14:00:00Z", status="FINISHED", matchday=1,
          last_updated=None):
    return {"utcDate": date, "status": status, "matchday": matchday,
            "lastUpdated": last_updated,
            "homeTeam": {"name": api_name(home)}, "awayTeam": {"name": api_name(away)},
            "score": {"fullTime": {"home": hg, "away": ag}}}


def network_error():
    return requests.ConnectionError("connection reset")
