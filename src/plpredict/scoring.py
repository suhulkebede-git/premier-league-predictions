"""Pure scoring functions (spec: Scoring rules, Tiebreak).

Nothing here touches the network or disk, so every rule is unit-tested
directly against the spec's worked examples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import AWARD_CATEGORIES, AwardStatus, PersonScore, Prediction, TableRow

log = logging.getLogger(__name__)

N_CLUBS = 20

POINTS = {
    "exact": 5,
    "champion": 20,
    "top5": 15,
    "relegation": 15,
    "sixth_seventh": 13,
    "one_off": 3,
    "most_exact": 20,
    "award": 10,
}
ONE_OFF_BAND = range(8, 18)  # positions 8-17 inclusive


class InvalidTableError(ValueError):
    """Raised when the league table cannot be scored against."""


def positions(table: list[TableRow], season_clubs: tuple[str, ...] | list[str]) -> dict[str, int]:
    """Validate the table and return club -> current position.

    Raises:
        InvalidTableError: wrong size, unknown or missing clubs, or positions
            that are not exactly 1-20.
    """
    if len(table) != N_CLUBS:
        raise InvalidTableError(f"Table has {len(table)} rows, expected {N_CLUBS}")
    pos = {row.club: row.position for row in table}
    if set(pos) != set(season_clubs):
        unknown = sorted(set(pos) - set(season_clubs))
        missing = sorted(set(season_clubs) - set(pos))
        raise InvalidTableError(f"Table clubs differ from season: unknown {unknown}, missing {missing}")
    if sorted(pos.values()) != list(range(1, N_CLUBS + 1)):
        raise InvalidTableError(f"Table positions are not 1-{N_CLUBS}: {sorted(pos.values())}")
    return pos


@dataclass
class TableResult:
    breakdown: dict[str, int] = field(default_factory=dict)
    hits: dict[str, list[str]] = field(default_factory=dict)
    exact_count: int = 0
    misses_desc: tuple[int, ...] = ()


def score_table_rules(pred: Prediction, pos: dict[str, int]) -> TableResult:
    """Score one person's finishing order against current positions.

    Champion replaces the 5 exact points for 1st but still counts as an exact
    hit for the most-exact bonus; every other rule stacks.
    """
    if len(pred.order) != N_CLUBS or set(pred.order) != set(pos):
        raise ValueError(f"{pred.person}: prediction is not an ordering of the table's clubs")

    by_pos = {p: club for club, p in pos.items()}
    res = TableResult(breakdown={k: 0 for k in
                                 ("exact", "champion", "top5", "relegation",
                                  "sixth_seventh", "one_off")},
                      hits={k: [] for k in
                            ("exact", "champion", "top5", "relegation",
                             "sixth_seventh", "one_off")})
    misses = []
    for i, club in enumerate(pred.order):
        predicted, actual = i + 1, pos[club]
        diff = abs(predicted - actual)
        misses.append(diff)
        if diff == 0:
            res.exact_count += 1
            if predicted == 1:
                res.breakdown["champion"] += POINTS["champion"]
                res.hits["champion"].append(club)
            else:
                res.breakdown["exact"] += POINTS["exact"]
                res.hits["exact"].append(club)
        elif diff == 1 and (predicted in ONE_OFF_BAND or actual in ONE_OFF_BAND):
            res.breakdown["one_off"] += POINTS["one_off"]
            res.hits["one_off"].append(club)

    if set(pred.order[:5]) == {by_pos[p] for p in range(1, 6)}:
        res.breakdown["top5"] = POINTS["top5"]
        res.hits["top5"] = list(pred.order[:5])
    if set(pred.order[17:]) == {by_pos[p] for p in range(18, 21)}:
        res.breakdown["relegation"] = POINTS["relegation"]
        res.hits["relegation"] = list(pred.order[17:])
    if pred.order[5] == by_pos[6] and pred.order[6] == by_pos[7]:
        res.breakdown["sixth_seventh"] = POINTS["sixth_seventh"]
        res.hits["sixth_seventh"] = list(pred.order[5:7])

    res.misses_desc = tuple(sorted(misses, reverse=True))
    log.debug("%s table rules: %s (exact hits %d)", pred.person, res.breakdown, res.exact_count)
    return res


@dataclass
class AwardResult:
    points: int = 0
    hits: list[str] = field(default_factory=list)  # category keys that scored
    alt_hits: list[str] = field(default_factory=list)  # "X or Y" picks where only Y leads


def score_awards(pred: Prediction, awards: dict[str, AwardStatus]) -> AwardResult:
    """10 points per category whose pick is among the current leaders.

    A category with no leaders yet (hand entry pending, before 25 Dec, an
    unsettled yes/no) scores nothing. The second half of an "X or Y" pick
    never scores; it only counts as a tiebreak (Ugwe ruling).
    """
    res = AwardResult()
    for category in AWARD_CATEGORIES:
        pick = pred.awards.get(category.key)
        status = awards.get(category.key)
        if pick is None:
            log.warning("%s has no pick for %s", pred.person, category.key)
            continue
        if status is None or not status.leaders:
            continue
        if pick.value in status.leaders:
            res.points += POINTS["award"]
            res.hits.append(category.key)
        elif pick.alt is not None and pick.alt in status.leaders:
            res.alt_hits.append(category.key)
    return res


def _rank_key(score: PersonScore) -> tuple:
    # Higher total first; then "X or Y" tiebreak hits; then smaller misses
    # compared largest-first (stinkers).
    return (-score.total, -len(score.hits.get("tiebreak", ())), score.misses_desc)


def score_all(
    predictions: tuple[Prediction, ...] | list[Prediction],
    table: list[TableRow],
    season_clubs: tuple[str, ...] | list[str],
    awards: dict[str, AwardStatus],
) -> list[PersonScore]:
    """Score and rank everyone. Returns PersonScores sorted by rank.

    Everyone tied for the most exact hits gets the bonus, including when the
    most is zero. Ranks are shared when total, tiebreak hits and every
    miss are equal (1, 1, 3 ...).

    Raises:
        InvalidTableError: the table cannot be scored against.
        ValueError: no predictions, or a duplicate person.
    """
    if not predictions:
        raise ValueError("No predictions to score")
    people = [p.person for p in predictions]
    if len(set(people)) != len(people):
        raise ValueError(f"Duplicate people in predictions: {people}")

    pos = positions(table, season_clubs)
    table_results = {p.person: score_table_rules(p, pos) for p in predictions}
    award_results = {p.person: score_awards(p, awards) for p in predictions}
    most = max(r.exact_count for r in table_results.values())

    scores = []
    for p in predictions:
        t, a = table_results[p.person], award_results[p.person]
        breakdown = dict(t.breakdown)
        breakdown["most_exact"] = POINTS["most_exact"] if t.exact_count == most else 0
        breakdown["awards"] = a.points
        hits = {k: tuple(v) for k, v in t.hits.items()}
        hits["awards"] = tuple(a.hits)
        hits["tiebreak"] = tuple(a.alt_hits)
        scores.append(PersonScore(person=p.person, total=sum(breakdown.values()),
                                  breakdown=breakdown, hits=hits,
                                  misses_desc=t.misses_desc))

    scores.sort(key=_rank_key)
    ranked, prev_key, rank = [], None, 0
    for i, s in enumerate(scores):
        key = _rank_key(s)
        if key != prev_key:
            rank, prev_key = i + 1, key
        ranked.append(PersonScore(**{**s.__dict__, "rank": rank}))
    log.info("Scored %d people; most exact hits %d; leader %s on %d",
             len(ranked), most, ranked[0].person, ranked[0].total)
    return ranked
