"""Monte Carlo season simulation: everyone's chance of winning the league.

Model (v3 spec): goals are Poisson. Each club has an attack and a defence
rating from this season's goals per game, shrunk toward the league average
as if it had also played PRIOR_GAMES average games; home sides get the
league's measured home-goal boost. The remaining fixtures are played out
``n`` times and every simulated season is scored with the real scorer.

Awards in a simulated season: everything table-based is recomputed from the
simulated results; Golden Boot, Top Assister and Most Red Cards keep today's
leaders (ruling: hold today's leaders); hand entries count as entered.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np

from .awards import resolve_awards
from .fetch import Match
from .history import Adjustment, table_after
from .models import AwardStatus, PredictionSet, TableRow
from .names import Aliases
from .scoring import score_all

log = logging.getLogger(__name__)

PRIOR_GAMES = 5
DEFAULT_HOME_GOALS = 1.5  # used before any match is played
DEFAULT_AWAY_GOALS = 1.2
HELD_AWARDS = ("golden_boot", "top_assister", "most_red_cards")
UNPLAYABLE = {"CANCELLED"}


class SimulationError(ValueError):
    """Inputs can't be simulated (e.g. no clubs)."""


@dataclass(frozen=True)
class Rating:
    attack: float
    defence: float  # >1 means concedes more than average


@dataclass(frozen=True)
class Model:
    ratings: dict[str, Rating]
    home_goals: float  # league average goals per game, home side
    away_goals: float

    def rates(self, home: str, away: str) -> tuple[float, float]:
        h, a = self.ratings[home], self.ratings[away]
        return (self.home_goals * h.attack * a.defence, self.away_goals * a.attack * h.defence)


def team_ratings(matches: list[Match], clubs: tuple[str, ...] | list[str],
                 prior_games: float = PRIOR_GAMES) -> Model:
    """Attack/defence ratings from finished matches, shrunk toward average."""
    if not clubs:
        raise SimulationError("No clubs to rate")
    done = [m for m in matches if m.finished]
    if done:
        home_avg = sum(m.home_goals for m in done) / len(done)
        away_avg = sum(m.away_goals for m in done) / len(done)
    else:
        home_avg, away_avg = DEFAULT_HOME_GOALS, DEFAULT_AWAY_GOALS
    per_team = (home_avg + away_avg) / 2 or 1.0
    gf, ga, played = Counter(), Counter(), Counter()
    for m in done:
        gf[m.home] += m.home_goals
        ga[m.home] += m.away_goals
        gf[m.away] += m.away_goals
        ga[m.away] += m.home_goals
        played[m.home] += 1
        played[m.away] += 1
    ratings = {}
    for c in clubs:
        games = played[c] + prior_games
        ratings[c] = Rating(
            attack=(gf[c] + prior_games * per_team) / games / per_team,
            defence=(ga[c] + prior_games * per_team) / games / per_team,
        )
    return Model(ratings, home_avg, away_avg)


def results_seed(matches: list[Match]) -> int:
    """A seed that only changes when a result changes, so odds don't flicker."""
    h = hashlib.sha256()
    for m in sorted((m for m in matches if m.finished), key=lambda m: (m.utc_date, m.home)):
        h.update(f"{m.home}|{m.away}|{m.home_goals}|{m.away_goals};".encode())
    return int.from_bytes(h.digest()[:8], "big")


@dataclass
class SimResult:
    n: int
    seed: int
    people: dict[str, dict] = field(default_factory=dict)  # win, top3, points_p10/p50/p90, likely_rank
    clubs: dict[str, dict] = field(default_factory=dict)  # title, top5, relegation
    fixtures: dict[tuple[str, str], dict] = field(default_factory=dict)  # p_home/p_draw/p_away


def simulate_season(
    pset: PredictionSet,
    matches: list[Match],
    live_awards: dict[str, AwardStatus],
    manual: dict[str, AwardStatus],
    aliases: Aliases,
    adjustments: list[Adjustment] = (),
    n: int = 2000,
    seed: int | None = None,
) -> SimResult:
    """Play out the rest of the season ``n`` times and score each one.

    Raises:
        SimulationError: n < 1 or no predictions.
    """
    if n < 1:
        raise SimulationError("n must be at least 1")
    if not pset.predictions:
        raise SimulationError("No predictions to simulate")
    clubs = pset.season_clubs
    model = team_ratings(matches, clubs)
    played = [m for m in matches if m.finished]
    todo = [m for m in matches if not m.finished and m.status not in UNPLAYABLE]
    seed = results_seed(matches) if seed is None else seed
    rng = np.random.default_rng(seed)

    lam = np.array([model.rates(m.home, m.away) for m in todo], dtype=float).reshape(-1, 2)
    home_goals = rng.poisson(lam[:, :1], size=(len(todo), n)) if todo else np.zeros((0, n), int)
    away_goals = rng.poisson(lam[:, 1:], size=(len(todo), n)) if todo else np.zeros((0, n), int)

    # Today's leaders hold for the awards the model can't play out.
    held = {k: live_awards[k] for k in HELD_AWARDS if k in live_awards and live_awards[k].leaders}
    held.update({k: v for k, v in manual.items() if v.leaders})
    season_end = date(max([m.utc_date for m in matches], default=datetime.now(timezone.utc)).year, 6, 30)

    names = [p.person for p in pset.predictions]
    totals = np.zeros((len(names), n))
    ranks = np.zeros((len(names), n), dtype=int)
    win = np.zeros(len(names))
    club_title, club_top5, club_drop = Counter(), Counter(), Counter()

    for s in range(n):
        sim = played + [Match(m.utc_date, "FINISHED", m.home, m.away,
                              int(home_goals[i, s]), int(away_goals[i, s]), matchday=m.matchday)
                        for i, m in enumerate(todo)]
        table = table_after([(1, m) for m in sim], 1, clubs, list(adjustments))
        awards = resolve_awards(table, sim, [], pset, held, aliases, season_end)
        scores = score_all(pset.predictions, table, clubs, awards)
        leaders = [sc for sc in scores if sc.rank == 1]
        for sc in scores:
            i = names.index(sc.person)
            totals[i, s] = sc.total
            ranks[i, s] = sc.rank
            if sc.rank == 1:
                win[i] += 1 / len(leaders)
        for row in table:
            if row.position == 1:
                club_title[row.club] += 1
            if row.position <= 5:
                club_top5[row.club] += 1
            if row.position >= 18:
                club_drop[row.club] += 1

    result = SimResult(n=n, seed=seed)
    for i, name in enumerate(names):
        rank_counts = Counter(ranks[i].tolist())
        p10, p50, p90 = np.percentile(totals[i], [10, 50, 90])
        result.people[name] = {
            "win": round(float(win[i]) / n, 4),
            "top3": round(float((ranks[i] <= 3).mean()), 4),
            "points_p10": int(round(p10)), "points_p50": int(round(p50)), "points_p90": int(round(p90)),
            "likely_rank": min(rank_counts, key=lambda r: (-rank_counts[r], r)),
        }
    for c in clubs:
        result.clubs[c] = {"title": round(club_title[c] / n, 4), "top5": round(club_top5[c] / n, 4),
                           "relegation": round(club_drop[c] / n, 4)}
    for i, m in enumerate(todo):
        hg, ag = home_goals[i], away_goals[i]
        result.fixtures[(m.home, m.away)] = {
            "p_home": round(float((hg > ag).mean()), 4),
            "p_draw": round(float((hg == ag).mean()), 4),
            "p_away": round(float((hg < ag).mean()), 4),
        }
    log.info("Simulated %d seasons (%d fixtures left, seed %d); favourite %s at %.0f%%",
             n, len(todo), seed, max(result.people, key=lambda p: result.people[p]["win"]),
             100 * max(v["win"] for v in result.people.values()))
    return result
