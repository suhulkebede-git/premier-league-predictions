"""football-data.org v4 client: standings, matches and scorers for the PL.

Free tier: 10 requests/minute, token in the X-Auth-Token header. The
client reads the API's rate-limit headers (X-Requests-Available-Minute,
X-RequestCounter-Reset) and waits for the counter to reset when the
allowance runs out, so a run with history backfill stays under the limit.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import requests

from .models import TableRow
from .names import NameResolver

log = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "PL"
TIMEOUT_S = 20
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """The API could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Match:
    utc_date: datetime
    status: str  # SCHEDULED, TIMED, IN_PLAY, PAUSED, FINISHED, ...
    home: str  # canonical club names
    away: str
    home_goals: int | None
    away_goals: int | None
    matchday: int | None = None  # the API's scheduled gameweek
    last_updated: datetime | None = None  # when the API last changed this match

    @property
    def finished(self) -> bool:
        return self.status == "FINISHED" and None not in (self.home_goals, self.away_goals)


@dataclass(frozen=True)
class Scorer:
    player: str  # as the API spells it, e.g. "Martin Ødegaard"
    goals: int
    assists: int


class Client:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        retries: int = 3,
        backoff_s: float = 6.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("A football-data.org API key is required (FOOTBALL_DATA_TOKEN)")
        self._session = session or requests.Session()
        self._session.headers["X-Auth-Token"] = api_key.strip()
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._wait_before_next = 0.0

    @staticmethod
    def _header_int(resp, name: str) -> int | None:
        value = (getattr(resp, "headers", None) or {}).get(name)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            log.debug("Ignoring non-numeric %s header %r", name, value)
            return None

    def _note_rate_limit(self, resp) -> None:
        """If this response used up the minute's allowance, wait before the next call."""
        remaining = self._header_int(resp, "X-Requests-Available-Minute")
        reset = self._header_int(resp, "X-RequestCounter-Reset")
        if remaining is not None and remaining <= 0:
            self._wait_before_next = float(reset if reset is not None else 60) + 1
            log.info("Rate limit allowance used up; next request waits %.0fs",
                     self._wait_before_next)

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET a JSON resource, retrying on 429/5xx and network errors.

        Raises:
            ApiError: a non-retryable status (e.g. 401/403), retries exhausted,
                or a body that is not a JSON object.
        """
        url = f"{BASE_URL}{path}"
        if self._wait_before_next:
            self._sleep(self._wait_before_next)
            self._wait_before_next = 0.0
        for attempt in range(1, self._retries + 2):
            try:
                resp = self._session.get(url, params=params, timeout=TIMEOUT_S)
            except (requests.ConnectionError, requests.Timeout) as exc:
                problem = f"network error: {exc}"
            else:
                self._note_rate_limit(resp)
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError as exc:
                        raise ApiError(f"{path}: response is not JSON") from exc
                    if not isinstance(body, dict):
                        raise ApiError(f"{path}: expected a JSON object")
                    log.debug("GET %s ok (attempt %d)", path, attempt)
                    return body
                if resp.status_code not in RETRY_STATUSES:
                    detail = _error_detail(resp)
                    raise ApiError(
                        f"{path}: HTTP {resp.status_code}"
                        + (f": {detail}" if detail else "")
                        + (" (check the FOOTBALL_DATA_TOKEN secret)"
                           if resp.status_code in (400, 401, 403) else "")
                    )
                problem = f"HTTP {resp.status_code}"
            if attempt > self._retries:
                raise ApiError(f"{path}: giving up after {attempt} attempts ({problem})")
            reset = self._header_int(resp, "X-RequestCounter-Reset") if problem == "HTTP 429" else None
            wait = float(reset) + 1 if reset is not None else self._backoff_s * attempt
            self._wait_before_next = 0.0
            log.warning("GET %s failed (%s); retry %d in %.0fs", path, problem, attempt, wait)
            self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover


def _error_detail(resp) -> str:
    """The API's own explanation of an error, e.g. 'Your API token is invalid.'"""
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("message"):
            return str(body["message"]).strip()
    except ValueError:
        pass
    text = (getattr(resp, "text", "") or "").strip()
    return text[:300] or (getattr(resp, "reason", "") or "").strip()


def api_club(raw: str, names: NameResolver) -> str | None:
    """Map an API team name ('Hull City AFC', 'AFC Bournemouth') to a canonical club.

    Unmapped names are collected on ``names`` for a single report.
    """
    candidates = [raw, re.sub(r"\s+A?FC$", "", raw.strip()), re.sub(r"^AFC\s+", "", raw.strip())]
    for candidate in candidates:
        club = names.club(candidate, required=False)
        if club is not None:
            return club
    return names.club(raw)  # records the miss with a suggestion


def _int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f"{what} is {value!r}, expected a whole number")
    return value


def fetch_table(client: Client, names: NameResolver) -> list[TableRow]:
    body = client.get(f"/competitions/{COMPETITION}/standings")
    try:
        total = next(s for s in body["standings"] if s.get("type") == "TOTAL")
        rows = [
            TableRow(
                club=api_club(r["team"]["name"], names),
                position=_int(r["position"], "position"),
                played=_int(r["playedGames"], "playedGames"),
                won=_int(r["won"], "won"),
                draw=_int(r["draw"], "draw"),
                lost=_int(r["lost"], "lost"),
                goals_for=_int(r["goalsFor"], "goalsFor"),
                goals_against=_int(r["goalsAgainst"], "goalsAgainst"),
                points=_int(r["points"], "points"),
            )
            for r in total["table"]
        ]
    except (KeyError, TypeError, StopIteration) as exc:
        raise ApiError(f"Unexpected standings response shape: {exc!r}") from exc
    names.raise_if_unmapped()
    log.info("Fetched table: %d clubs, leader %s on %d", len(rows),
             rows[0].club if rows else "-", rows[0].points if rows else 0)
    return rows


def fetch_matches(client: Client, names: NameResolver) -> list[Match]:
    body = client.get(f"/competitions/{COMPETITION}/matches")
    matches = []
    try:
        for m in body["matches"]:
            full = (m.get("score") or {}).get("fullTime") or {}
            matches.append(Match(
                utc_date=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")),
                status=m["status"],
                home=api_club(m["homeTeam"]["name"], names),
                away=api_club(m["awayTeam"]["name"], names),
                home_goals=full.get("home"),
                away_goals=full.get("away"),
                matchday=m.get("matchday"),
                last_updated=(datetime.fromisoformat(m["lastUpdated"].replace("Z", "+00:00"))
                              if m.get("lastUpdated") else None),
            ))
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(f"Unexpected matches response shape: {exc!r}") from exc
    names.raise_if_unmapped()
    log.info("Fetched %d matches (%d finished)", len(matches), sum(m.finished for m in matches))
    return matches


def fetch_scorers(client: Client, limit: int = 200, matchday: int | None = None) -> list[Scorer]:
    """Top scorers with assists. A high limit keeps assist leaders who score
    few goals in the list (the API ranks by goals). ``matchday`` asks for the
    list as of that gameweek (used to backfill history)."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if matchday is not None and matchday < 1:
        raise ValueError("matchday must be positive")
    params = {"limit": limit} if matchday is None else {"limit": limit, "matchday": matchday}
    body = client.get(f"/competitions/{COMPETITION}/scorers", params)
    try:
        scorers = [
            Scorer(
                player=s["player"]["name"],
                goals=_int(s.get("goals") or 0, "goals"),
                assists=_int(s.get("assists") or 0, "assists"),
            )
            for s in body["scorers"]
        ]
    except (KeyError, TypeError) as exc:
        raise ApiError(f"Unexpected scorers response shape: {exc!r}") from exc
    log.info("Fetched %d scorers%s", len(scorers), f" for matchday {matchday}" if matchday else "")
    return scorers
