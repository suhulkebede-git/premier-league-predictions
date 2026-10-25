"""What this weekend means: whose points move with each upcoming result.

For each fixture, three what-ifs are scored exactly with the real scorer:
a home win (1-0), a draw (1-1) and an away win (0-1), with every other
result as it is now. A person's swing is their points in that what-if minus
their points today (both computed the same way, from match results).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from .awards import resolve_awards
from .fetch import Match, Scorer
from .history import Adjustment, table_after
from .models import AwardStatus, PredictionSet
from .names import Aliases
from .scoring import score_all

log = logging.getLogger(__name__)

ALL_GAMEWEEKS = 38  # count every result and every deduction
OUTCOMES = (("home", 1, 0), ("draw", 1, 1), ("away", 0, 1))


@dataclass
class FixtureImpact:
    home: str
    away: str
    kickoff: datetime
    swings: dict[str, dict[str, int]] = field(default_factory=dict)  # outcome -> person -> delta

    @property
    def size(self) -> int:
        """The biggest total movement any one result causes (for 'match of the week')."""
        return max((sum(abs(d) for d in s.values()) for s in self.swings.values()), default=0)


def totals_for(pset: PredictionSet, results: list[Match], scorers: list[Scorer] | None,
               manual: dict[str, AwardStatus], aliases: Aliases, adjustments: list[Adjustment],
               today: date) -> dict[str, int]:
    """Everyone's points if the season stood at exactly these results."""
    table = table_after([(1, m) for m in results], ALL_GAMEWEEKS, pset.season_clubs, list(adjustments))
    awards = resolve_awards(table, results, scorers, pset, manual, aliases, today)
    return {s.person: s.total for s in score_all(pset.predictions, table, pset.season_clubs, awards)}


def fixture_swings(
    pset: PredictionSet,
    matches: list[Match],
    fixtures: list[Match],
    scorers: list[Scorer] | None,
    manual: dict[str, AwardStatus],
    aliases: Aliases,
    adjustments: list[Adjustment],
    today: date,
) -> list[FixtureImpact]:
    """Swings for each upcoming fixture, in kick-off order.

    Raises:
        ValueError: a fixture is already finished.
    """
    done = [m for m in matches if m.finished]
    base = totals_for(pset, done, scorers, manual, aliases, adjustments, today)
    out = []
    for f in sorted(fixtures, key=lambda m: m.utc_date):
        if f.finished:
            raise ValueError(f"{f.home} v {f.away} is already finished")
        impact = FixtureImpact(f.home, f.away, f.utc_date)
        for outcome, hg, ag in OUTCOMES:
            what_if = done + [Match(f.utc_date, "FINISHED", f.home, f.away, hg, ag, matchday=f.matchday)]
            totals = totals_for(pset, what_if, scorers, manual, aliases, adjustments, today)
            impact.swings[outcome] = {p: totals[p] - base[p] for p in base if totals[p] != base[p]}
        out.append(impact)
    if out:
        top = max(out, key=lambda i: i.size)
        log.info("Worked out swings for %d fixtures; biggest: %s v %s (%d points of movement)",
                 len(out), top.home, top.away, top.size)
    return out
