"""Work out the current leader(s) of each award category (spec: Awards).

Live categories score against the current leader; categories with no data
yet return no leaders, so they score nothing until the data exists.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable

from .fetch import Match, Scorer
from .models import AWARD_CATEGORIES, CATEGORY_BY_KEY, AwardStatus, PickKind, PredictionSet, TableRow
from .names import Aliases, NameResolver, normalise

log = logging.getLogger(__name__)

SEASON_GAMES = 38
OVER_POINTS = 91  # "over 90"
UNDER_POINTS = 25  # "under 25" means 24 or fewer
MANUAL_KEYS = ("most_red_cards", "first_sacked_manager", "relegation_final_day", "pfa_player")


class ManualAwardsError(ValueError):
    """manual_awards.json is malformed or names something unknown."""


def _leaders(items: Iterable, metric: Callable, *, best=max) -> tuple:
    items = list(items)
    if not items:
        return ()
    top = best(metric(i) for i in items)
    return tuple(i for i in items if metric(i) == top)


def _season_over(table: list[TableRow]) -> bool:
    return all(r.played >= SEASON_GAMES for r in table)


def _canonical_player(api_name: str, aliases: Aliases) -> str:
    """Use the canonical spelling people picked if it matches, else the API's."""
    canon = {normalise(v): v for v in aliases.players.values()}
    return canon.get(normalise(api_name), api_name)


def _table_awards(table: list[TableRow], pset: PredictionSet) -> dict[str, AwardStatus]:
    over = _season_over(table)
    if all(r.played == 0 for r in table):
        return {}
    clubs = lambda rows: tuple(r.club for r in rows)  # noqa: E731
    out = {
        "most_goals": clubs(_leaders(table, lambda r: r.goals_for)),
        "fewest_goals": clubs(_leaders(table, lambda r: r.goals_for, best=min)),
        "best_defense": clubs(_leaders(table, lambda r: r.goals_against, best=min)),
        "worst_defense": clubs(_leaders(table, lambda r: r.goals_against)),
        "most_draws": clubs(_leaders(table, lambda r: r.draw)),
        "highest_promoted": clubs(_leaders(
            [r for r in table if r.club in pset.promoted_clubs], lambda r: r.position, best=min)),
    }
    by_pos = sorted(table, key=lambda r: r.position)
    out["title_margin"] = (by_pos[0].points - by_pos[1].points,)

    # Increase / drop-off on pace: points per game x 38 vs last season.
    paced = [r for r in table if r.club in pset.last_season_points and r.played > 0]
    delta = lambda r: Fraction(r.points * SEASON_GAMES, r.played) - pset.last_season_points[r.club]  # noqa: E731
    out["biggest_increase"] = clubs(_leaders(paced, delta))
    out["biggest_dropoff"] = clubs(_leaders(paced, delta, best=min))
    return {k: AwardStatus(key=k, leaders=v, settled=over) for k, v in out.items()}


def _threshold_awards(table: list[TableRow]) -> dict[str, AwardStatus]:
    max_possible = lambda r: r.points + 3 * max(SEASON_GAMES - r.played, 0)  # noqa: E731
    if any(r.points >= OVER_POINTS for r in table):
        over = AwardStatus("any_over_90", (True,), True, "a club has passed 90")
    elif all(max_possible(r) < OVER_POINTS for r in table):
        over = AwardStatus("any_over_90", (False,), True, "no club can reach 91")
    else:
        over = AwardStatus("any_over_90", (), False, "not decided yet")
    if all(r.points >= UNDER_POINTS for r in table):
        under = AwardStatus("any_under_25", (False,), True, "every club has 25+")
    elif any(max_possible(r) < UNDER_POINTS for r in table):
        under = AwardStatus("any_under_25", (True,), True, "a club cannot reach 25")
    else:
        under = AwardStatus("any_under_25", (), False, "not decided yet")
    return {"any_over_90": over, "any_under_25": under}


def _clean_sheets(matches: list[Match]) -> AwardStatus:
    finished = [m for m in matches if m.finished]
    if not finished:
        return AwardStatus("most_clean_sheets", (), False, "no results yet")
    counts: dict[str, int] = defaultdict(int)
    for m in finished:
        counts[m.home] += 0
        counts[m.away] += 0
        if m.away_goals == 0:
            counts[m.home] += 1
        if m.home_goals == 0:
            counts[m.away] += 1
    leaders = _leaders(counts.items(), lambda kv: kv[1])
    return AwardStatus("most_clean_sheets", tuple(club for club, _ in leaders), False)


def christmas_table(matches: list[Match]) -> list[tuple[str, int, int, int]]:
    """(club, points, goal difference, goals for) from results before 26 Dec, best first."""
    season_year = min(m.utc_date for m in matches).year
    cutoff = datetime(season_year, 12, 26, tzinfo=timezone.utc)
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for m in matches:
        stats[m.home], stats[m.away]  # every club appears, even with no results
        if not m.finished or m.utc_date >= cutoff:
            continue
        for club, gf, ga in ((m.home, m.home_goals, m.away_goals), (m.away, m.away_goals, m.home_goals)):
            pts = 3 if gf > ga else 1 if gf == ga else 0
            stats[club][0] += pts
            stats[club][1] += gf - ga
            stats[club][2] += gf
    rows = [(club, *vals) for club, vals in stats.items()]
    return sorted(rows, key=lambda r: (-r[1], -r[2], -r[3], r[0]))


def _christmas_awards(matches: list[Match], today: date) -> dict[str, AwardStatus]:
    if not matches:
        return {}
    season_year = min(m.utc_date for m in matches).year
    if today < date(season_year, 12, 25):
        note = f"settles on 25 Dec {season_year}"
        return {k: AwardStatus(k, (), False, note) for k in ("top_at_christmas", "bottom_at_christmas")}
    rows = christmas_table(matches)
    key = lambda r: r[1:]  # noqa: E731
    top = tuple(r[0] for r in rows if key(r) == key(rows[0]))
    bottom = tuple(r[0] for r in rows if key(r) == key(rows[-1]))
    return {
        "top_at_christmas": AwardStatus("top_at_christmas", top, True),
        "bottom_at_christmas": AwardStatus("bottom_at_christmas", bottom, True),
    }


def _scorer_awards(scorers: list[Scorer], aliases: Aliases, over: bool) -> dict[str, AwardStatus]:
    out = {}
    for key, metric in (("golden_boot", lambda s: s.goals), ("top_assister", lambda s: s.assists)):
        ranked = [s for s in scorers if metric(s) > 0]
        leaders = tuple(_canonical_player(s.player, aliases) for s in _leaders(ranked, metric))
        out[key] = AwardStatus(key, leaders, over and bool(leaders))
    return out


def load_manual_awards(path: Path, names: NameResolver) -> dict[str, AwardStatus]:
    """Read hand-entered awards.

    Format: {"pfa_player": {"leaders": ["Bruno Fernandes"], "settled": true}, ...}
    with leaders empty until known. Only MANUAL_KEYS are accepted.

    Raises:
        ManualAwardsError: bad JSON, unknown key, or an unmapped name.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("No manual awards file at %s; hand-entry awards stay unknown", path)
        return {}
    except json.JSONDecodeError as exc:
        raise ManualAwardsError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManualAwardsError(f"{path} must contain a JSON object")

    out = {}
    for key, entry in data.items():
        if key.startswith("_"):
            continue
        if key not in MANUAL_KEYS:
            raise ManualAwardsError(f"{key!r} is not a hand-entry award; expected one of {MANUAL_KEYS}")
        if not isinstance(entry, dict) or not isinstance(entry.get("leaders", []), list):
            raise ManualAwardsError(f"{key!r} must be {{'leaders': [...], 'settled': bool}}")
        kind = CATEGORY_BY_KEY[key].kind
        leaders = []
        for raw in entry.get("leaders", []):
            if kind is PickKind.CLUB:
                value = names.club(raw)
            elif kind is PickKind.YES_NO:
                value = raw if isinstance(raw, bool) else names.answer(raw)
            else:
                value = names.person_name(raw, "players" if kind is PickKind.PLAYER else "managers")
            leaders.append(value)
        try:
            names.raise_if_unmapped()
        except ValueError as exc:
            raise ManualAwardsError(f"{path}: {exc}") from exc
        from_gw = entry.get("from_gameweek")
        if from_gw is not None and (isinstance(from_gw, bool) or not isinstance(from_gw, int)
                                    or not 1 <= from_gw <= 38):
            raise ManualAwardsError(f"{key!r}: from_gameweek must be 1-38 or null")
        out[key] = AwardStatus(key, tuple(leaders), bool(entry.get("settled", False)),
                               "" if leaders else "hand entry pending", from_gw)
    log.info("Loaded %d hand-entered awards (%d known)", len(out),
             sum(bool(s.leaders) for s in out.values()))
    return out


def resolve_awards(
    table: list[TableRow],
    matches: list[Match],
    scorers: list[Scorer] | None,
    pset: PredictionSet,
    manual: dict[str, AwardStatus],
    aliases: Aliases,
    today: date,
    gameweek: int | None = None,
) -> dict[str, AwardStatus]:
    """Status of all 20 award categories, live or as of a past gameweek.

    For a past gameweek, pass that gameweek's table and matches, ``today`` =
    its last kick-off date, and ``gameweek``: hand entries recorded from a
    later gameweek are left out, and ``scorers=None`` marks Golden Boot and
    Top Assister as not recorded.
    """
    if not table:
        raise ValueError("Cannot resolve awards without a league table")
    over = _season_over(table)
    statuses: dict[str, AwardStatus] = {}
    statuses.update(_table_awards(table, pset))
    statuses.update(_threshold_awards(table))
    statuses["most_clean_sheets"] = AwardStatus(
        **{**_clean_sheets(matches).__dict__, "settled": over})
    statuses.update(_christmas_awards(matches, today))
    if scorers is None:
        for key in ("golden_boot", "top_assister"):
            statuses[key] = AwardStatus(key, (), False, "not recorded for this gameweek")
    else:
        statuses.update(_scorer_awards(scorers, aliases, over))
    statuses.update({k: v for k, v in manual.items()
                     if gameweek is None or v.from_gameweek is None or v.from_gameweek <= gameweek})
    for category in AWARD_CATEGORIES:
        statuses.setdefault(category.key, AwardStatus(
            category.key, (), False,
            "hand entry pending" if category.key in MANUAL_KEYS else "no data yet"))
    known = sum(bool(s.leaders) for s in statuses.values())
    log.info("Resolved awards: %d/%d have leaders, %d settled", known, len(statuses),
             sum(s.settled for s in statuses.values()))
    return statuses
