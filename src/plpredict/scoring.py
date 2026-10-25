"""Pure scoring functions (spec: Scoring rules, Tiebreak).

Nothing here touches the network or disk, so every rule is unit-tested
directly against the spec's worked examples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import Prediction, TableRow

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
