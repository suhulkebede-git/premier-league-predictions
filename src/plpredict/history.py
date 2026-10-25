"""Gameweek history: which gameweek each match counts in, and the table after it.

Rulings (v2 spec):
  * A match counts in its own gameweek unless it is played outside that
    gameweek's window (a postponed game); then it counts in the gameweek
    whose window is closest to when it was actually played.
  * Points deductions apply from the gameweek they happened onward.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .fetch import Match, Scorer
from .models import TableRow
from .names import NameResolver

log = logging.getLogger(__name__)

CORE_DAYS = 4  # a gameweek's fixtures sit within this many days of its median kick-off
NOT_HAPPENING = {"POSTPONED", "SUSPENDED", "CANCELLED"}
STARTED = {"IN_PLAY", "PAUSED", "FINISHED"}


class HistoryError(ValueError):
    """History inputs (adjustments file, matches) are unusable."""


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def distance(self, when: datetime) -> timedelta:
        if when < self.start:
            return self.start - when
        if when > self.end:
            return when - self.end
        return timedelta(0)


def gameweek_windows(matches: list[Match]) -> dict[int, Window]:
    """Each gameweek's normal window: its fixtures within CORE_DAYS of the median kick-off."""
    by_gw: dict[int, list[datetime]] = {}
    for m in matches:
        if m.matchday is not None and m.status not in NOT_HAPPENING:
            by_gw.setdefault(m.matchday, []).append(m.utc_date)
    windows = {}
    for gw, dates in sorted(by_gw.items()):
        median = sorted(dates)[(len(dates) - 1) // 2]
        core = [d for d in dates if abs(d - median) <= timedelta(days=CORE_DAYS)]
        windows[gw] = Window(min(core), max(core))
    return windows


def expected_gameweek(match: Match, windows: dict[int, Window]) -> int | None:
    """The gameweek a match counts in (played) or will count in (still to play)."""
    if match.status in NOT_HAPPENING or not windows:
        return None
    own = match.matchday
    if own in windows and windows[own].distance(match.utc_date) == timedelta(0):
        return own
    # Played (or rescheduled) outside its own window: nearest window wins,
    # ties going to the earlier gameweek.
    nearest = min(windows, key=lambda gw: (windows[gw].distance(match.utc_date), gw))
    if nearest != own:
        log.info("%s v %s (matchday %s, %s) counts in gameweek %d", match.home, match.away,
                 own, match.utc_date.date(), nearest)
    return nearest


def assign_gameweeks(matches: list[Match]) -> list[tuple[int, Match]]:
    """(gameweek, match) for every match that is happening, in kick-off order."""
    windows = gameweek_windows(matches)
    out = []
    for m in sorted(matches, key=lambda m: m.utc_date):
        gw = expected_gameweek(m, windows)
        if gw is not None:
            out.append((gw, m))
    return out


def final_gameweeks(assigned: list[tuple[int, Match]]) -> list[int]:
    """Gameweeks whose every counted match is finished, in an unbroken run from GW 1."""
    by_gw: dict[int, list[Match]] = {}
    for gw, m in assigned:
        by_gw.setdefault(gw, []).append(m)
    final = []
    for gw in sorted(by_gw):
        if gw != len(final) + 1 or not all(m.finished for m in by_gw[gw]):
            break
        final.append(gw)
    return final


def live_gameweek(assigned: list[tuple[int, Match]], final: list[int]) -> int | None:
    """The gameweek in progress (some match started, not all finished), if any."""
    nxt = (final[-1] if final else 0) + 1
    if any(gw == nxt and m.status in STARTED for gw, m in assigned):
        return nxt
    return None


def gameweek_end(assigned: list[tuple[int, Match]], gameweek: int) -> datetime:
    """Kick-off of the last match counted in a gameweek."""
    dates = [m.utc_date for gw, m in assigned if gw == gameweek]
    if not dates:
        raise HistoryError(f"No matches counted in gameweek {gameweek}")
    return max(dates)


@dataclass(frozen=True)
class Adjustment:
    club: str
    points: int  # negative for a deduction
    from_gameweek: int
    reason: str = ""


def load_adjustments(path: Path, names: NameResolver) -> list[Adjustment]:
    """Read data/adjustments.json: [{"club", "points", "from_gameweek", "reason"}].

    A missing file means no adjustments.

    Raises:
        HistoryError: malformed file, bad values or an unknown club.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info("No adjustments file at %s", path)
        return []
    except json.JSONDecodeError as exc:
        raise HistoryError(f"{path} is not valid JSON: {exc}") from exc
    entries = data.get("adjustments", []) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise HistoryError(f"{path} must be {{\"adjustments\": [...]}}")
    out = []
    for i, e in enumerate(entries):
        try:
            points, gw = e["points"], e["from_gameweek"]
            if isinstance(points, bool) or not isinstance(points, int):
                raise TypeError("points must be a whole number")
            if isinstance(gw, bool) or not isinstance(gw, int) or not 1 <= gw <= 38:
                raise TypeError("from_gameweek must be 1-38")
            club = names.club(e["club"])
        except (KeyError, TypeError) as exc:
            raise HistoryError(f"{path} entry {i}: {exc}") from exc
        out.append(Adjustment(club, points, gw, str(e.get("reason", ""))))
    try:
        names.raise_if_unmapped()
    except ValueError as exc:
        raise HistoryError(f"{path}: {exc}") from exc
    log.info("Loaded %d points adjustment(s)", len(out))
    return out


def table_after(
    assigned: list[tuple[int, Match]],
    gameweek: int,
    clubs: tuple[str, ...] | list[str],
    adjustments: list[Adjustment] = (),
) -> list[TableRow]:
    """The league table after a gameweek, from results counted up to it.

    Ranked by points, goal difference, goals scored, then name (the API's
    own table is used for the live view, so rarer tiebreaks only matter here).
    """
    if gameweek < 1:
        raise HistoryError("gameweek must be 1 or more")
    stats = {c: {"played": 0, "won": 0, "draw": 0, "lost": 0, "gf": 0, "ga": 0, "pts": 0}
             for c in clubs}
    for gw, m in assigned:
        if gw > gameweek or not m.finished:
            continue
        for club, gf, ga in ((m.home, m.home_goals, m.away_goals), (m.away, m.away_goals, m.home_goals)):
            if club not in stats:
                raise HistoryError(f"Match involves {club!r}, which is not a season club")
            s = stats[club]
            s["played"] += 1
            s["gf"] += gf
            s["ga"] += ga
            if gf > ga:
                s["won"] += 1
                s["pts"] += 3
            elif gf == ga:
                s["draw"] += 1
                s["pts"] += 1
            else:
                s["lost"] += 1
    for adj in adjustments:
        if adj.from_gameweek <= gameweek:
            stats[adj.club]["pts"] += adj.points
    order = sorted(stats, key=lambda c: (-stats[c]["pts"], -(stats[c]["gf"] - stats[c]["ga"]),
                                         -stats[c]["gf"], c))
    return [TableRow(club=c, position=i + 1, played=stats[c]["played"], won=stats[c]["won"],
                     draw=stats[c]["draw"], lost=stats[c]["lost"], goals_for=stats[c]["gf"],
                     goals_against=stats[c]["ga"], points=stats[c]["pts"])
            for i, c in enumerate(order)]


# --- Golden Boot / Top Assister history -------------------------------------
#
# data/history.json keeps the scorers list as it stood after each finished
# gameweek. Two sources fill it:
#   * snapshots: the live scorers list, saved between gameweeks, when it is
#     exactly "totals through the last finished gameweek" (authoritative);
#   * backfill: the API's scorers?matchday=N, whose meaning is checked
#     against the goals actually scored. In practice football-data.org ignores
#     the filter and returns today's totals, which is detected (more goals
#     listed than had been scored by then) and discarded ("ignored"), so
#     those gameweeks show the scorer awards as not recorded.

BACKFILL_PER_RUN = 5


def load_history(path: Path) -> dict:
    """Read data/history.json ({"snapshots", "backfill", "backfill_mode"})."""
    empty = {"snapshots": {}, "backfill": {}, "backfill_mode": None, "red_cards": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info("No history file at %s; starting fresh", path)
        return empty
    except json.JSONDecodeError as exc:
        raise HistoryError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HistoryError(f"{path} must contain a JSON object")
    for key in ("snapshots", "backfill", "red_cards"):
        if not isinstance(data.get(key, {}), dict):
            raise HistoryError(f"{path}: {key!r} must be an object keyed by gameweek")
    mode = data.get("backfill_mode")
    if mode not in (None, "cumulative", "per_gameweek", "ignored"):
        raise HistoryError(f"{path}: unknown backfill_mode {mode!r}")
    return {"snapshots": data.get("snapshots", {}), "backfill": data.get("backfill", {}),
            "backfill_mode": mode, "red_cards": data.get("red_cards", {})}


def save_history(history: dict, path: Path) -> bool:
    """Write history if it changed. Returns True when the file was (re)written."""
    text = json.dumps(history, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path = Path(path)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.exception("Could not write history to %s", path)
        raise
    log.info("Saved scorer history to %s", path)
    return True


def _rows(scorers) -> list[dict]:
    return [{"player": s.player, "goals": s.goals, "assists": s.assists} for s in scorers]


def goals_in_gameweek(assigned: list[tuple[int, Match]], gameweek: int) -> int:
    return sum(m.home_goals + m.away_goals for gw, m in assigned if gw == gameweek and m.finished)


def goals_through(assigned: list[tuple[int, Match]], gameweek: int) -> int:
    return sum(m.home_goals + m.away_goals for gw, m in assigned if gw <= gameweek and m.finished)


def _mark_filter_ignored(history: dict, gameweek: int, listed: int, possible: int) -> None:
    log.warning("Scorers for matchday %d list %d goals but only %d had been scored by then: "
                "the API ignores the matchday filter, so backfilled scorer history is discarded "
                "(those gameweeks show Golden Boot / Top Assister as not recorded)",
                gameweek, listed, possible)
    history["backfill_mode"] = "ignored"
    history["backfill"] = {}


def validate_backfill(history: dict, assigned: list[tuple[int, Match]]) -> bool:
    """Re-check stored backfill against the goals actually scored; discard it if
    any gameweek lists more goals than existed then. Returns True if discarded."""
    if history["backfill_mode"] == "ignored":
        return False
    for key, rows in sorted(history["backfill"].items(), key=lambda kv: int(kv[0])):
        listed, possible = sum(r["goals"] for r in rows), goals_through(assigned, int(key))
        if listed > possible:
            _mark_filter_ignored(history, int(key), listed, possible)
            return True
    return False


def record_snapshot(history: dict, scorers, final: list[int], live: int | None) -> None:
    """Save the live scorers list as the last final gameweek's totals, when exact.

    Only between gameweeks (nothing in progress) does the live list equal the
    totals through the last finished gameweek.
    """
    if not final or live is not None:
        return
    key = str(final[-1])
    if key not in history["snapshots"]:
        history["snapshots"][key] = _rows(scorers)
        log.info("Recorded scorer snapshot for gameweek %s", key)


def backfill_scorers(client, history: dict, assigned, final: list[int], fetch,
                     max_calls: int = BACKFILL_PER_RUN) -> int:
    """Fetch scorers?matchday=N for finished gameweeks with no history yet.

    ``fetch(client, matchday)`` returns a scorers list. At most ``max_calls``
    requests per run; the rest are filled on later runs. Returns calls made.
    """
    if history["backfill_mode"] == "ignored":
        return 0
    missing = [gw for gw in final
               if str(gw) not in history["snapshots"] and str(gw) not in history["backfill"]]
    calls = 0
    for gw in missing[:max_calls]:
        scorers = fetch(client, gw)
        calls += 1
        listed, possible = sum(s.goals for s in scorers), goals_through(assigned, gw)
        if listed > possible:
            _mark_filter_ignored(history, gw, listed, possible)
            return calls
        history["backfill"][str(gw)] = _rows(scorers)
        log.info("Backfilled scorers for gameweek %d (%d players)", gw, len(scorers))
        if history["backfill_mode"] is None and gw >= 2:
            actual = goals_in_gameweek(assigned, gw)
            # Running totals exceed one gameweek's goals once past GW 1
            # (own goals are never credited, so a single week lists fewer).
            history["backfill_mode"] = "cumulative" if listed > actual else "per_gameweek"
            log.info("Scorers matchday filter looks like %s (listed %d goals, gameweek %d had %d)",
                     history["backfill_mode"], listed, gw, actual)
    if len(missing) > max_calls:
        log.info("Backfilled %d gameweek(s); %d left for later runs", calls, len(missing) - calls)
    return calls


def scorers_through(history: dict, gameweek: int):
    """Scorers totals after a finished gameweek, or None if not recorded."""
    snap = history["snapshots"].get(str(gameweek))
    if snap is not None:
        return [Scorer(r["player"], r["goals"], r["assists"]) for r in snap]
    mode, back = history["backfill_mode"], history["backfill"]
    if mode == "cumulative" or (mode is None and gameweek == 1):
        rows = back.get(str(gameweek))
        return None if rows is None else [Scorer(r["player"], r["goals"], r["assists"]) for r in rows]
    if mode == "per_gameweek":
        if any(str(g) not in back for g in range(1, gameweek + 1)):
            return None
        totals: dict[str, list[int]] = {}
        for g in range(1, gameweek + 1):
            for r in back[str(g)]:
                t = totals.setdefault(r["player"], [0, 0])
                t[0] += r["goals"]
                t[1] += r["assists"]
        return [Scorer(p, g, a) for p, (g, a) in totals.items()]
    return None


def record_red_cards(history: dict, counts: dict[str, int] | None, final: list[int],
                     live: int | None) -> None:
    """Save red cards per club as the last final gameweek's totals, between gameweeks."""
    if counts is None or not final or live is not None:
        return
    key = str(final[-1])
    stored = history.setdefault("red_cards", {})
    if key not in stored:
        stored[key] = dict(sorted(counts.items()))
        log.info("Recorded red-card snapshot for gameweek %s", key)


def red_cards_through(history: dict, gameweek: int) -> dict[str, int] | None:
    """Red cards per club after a finished gameweek, or None if not recorded."""
    return history.get("red_cards", {}).get(str(gameweek))
