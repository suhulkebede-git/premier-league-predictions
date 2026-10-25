"""Core immutable records shared by every module (see spec: Data structures).

Clubs are identified by their canonical name from the season's club list
(sheet C3:C22). Every other spelling, from the sheet or the API, is mapped
to that name before it reaches scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union

PickValue = Union[str, int, bool]


class PickKind(str, Enum):
    CLUB = "club"
    PLAYER = "player"
    MANAGER = "manager"
    NUMBER = "number"
    YES_NO = "yes_no"


@dataclass(frozen=True)
class AwardCategory:
    key: str
    label: str  # exactly as written in the sheet
    kind: PickKind


# The 20 award categories, in sheet order.
AWARD_CATEGORIES: tuple[AwardCategory, ...] = (
    AwardCategory("pfa_player", "PFA Player", PickKind.PLAYER),
    AwardCategory("first_sacked_manager", "First Sacked Manager", PickKind.MANAGER),
    AwardCategory("title_margin", "League Title Margin", PickKind.NUMBER),
    AwardCategory("biggest_increase", "Biggest Increase in Points", PickKind.CLUB),
    AwardCategory("biggest_dropoff", "Biggest Drop-off in Points", PickKind.CLUB),
    AwardCategory("most_red_cards", "Club with Most Red Cards", PickKind.CLUB),
    AwardCategory("top_assister", "Top Assister", PickKind.PLAYER),
    # A goalkeeper pick counts as his club (spec ruling), so this is a club pick.
    AwardCategory("most_clean_sheets", "Most Clean Sheets", PickKind.CLUB),
    AwardCategory("most_draws", "Club with Most Draws", PickKind.CLUB),
    AwardCategory("highest_promoted", "Highest-finishing Promoted Club", PickKind.CLUB),
    AwardCategory("golden_boot", "Golden Boot Winner", PickKind.PLAYER),
    AwardCategory("top_at_christmas", "Top at Christmas", PickKind.CLUB),
    AwardCategory("bottom_at_christmas", "Bottom at Christmas", PickKind.CLUB),
    AwardCategory("fewest_goals", "Club with Fewest Goals", PickKind.CLUB),
    AwardCategory("worst_defense", "Worst Defense", PickKind.CLUB),
    AwardCategory("most_goals", "Club with Most Goals", PickKind.CLUB),
    AwardCategory("best_defense", "Best Defense", PickKind.CLUB),
    AwardCategory(
        "relegation_final_day",
        "Will the relegation battle go to the final day?",
        PickKind.YES_NO,
    ),
    AwardCategory("any_under_25", "Will any club get under 25 points?", PickKind.YES_NO),
    AwardCategory("any_over_90", "Will any club get over 90 points?", PickKind.YES_NO),
)
CATEGORY_BY_KEY = {c.key: c for c in AWARD_CATEGORIES}
CATEGORY_BY_LABEL = {c.label.strip().lower(): c for c in AWARD_CATEGORIES}


@dataclass(frozen=True)
class AwardPick:
    raw: str  # exactly as typed, e.g. "Big Erl"
    value: PickValue  # normalised club / player name, number or yes/no
    alt: PickValue | None = None  # second half of an "X or Y" pick: tiebreak only


@dataclass(frozen=True)
class Prediction:
    person: str
    order: tuple[str, ...]  # 20 canonical club names, index 0 = predicted 1st
    awards: dict[str, AwardPick] = field(hash=False)


@dataclass(frozen=True)
class PredictionSet:
    season_clubs: tuple[str, ...]
    promoted_clubs: tuple[str, ...]
    last_season_points: dict[str, int] = field(hash=False)  # season clubs only
    predictions: tuple[Prediction, ...] = ()


@dataclass(frozen=True)
class TableRow:
    club: str  # canonical name
    position: int
    played: int
    won: int
    draw: int
    lost: int
    goals_for: int
    goals_against: int
    points: int


@dataclass(frozen=True)
class RaceEntry:
    rank: int  # shared on a tie (1, 1, 3)
    name: PickValue  # canonical club or player, as people's picks are stored
    value: int  # goals, conceded, draws, points, pace change... (unit depends on the award)


@dataclass(frozen=True)
class AwardStatus:
    key: str
    leaders: tuple[PickValue, ...]  # current leader(s) or settled answer; empty = unknown
    settled: bool  # True once it can no longer change
    note: str = ""  # shown on the site, e.g. "from 25 Dec" or "hand entry pending"
    from_gameweek: int | None = None  # hand entries: counts from this gameweek on
    value: int | None = None  # the leader's number (goals, clean sheets, margin...)
    race: tuple[RaceEntry, ...] = ()  # the top 3 (more on a tie for 3rd)


@dataclass(frozen=True)
class PersonScore:
    person: str
    total: int
    breakdown: dict[str, int] = field(hash=False)
    hits: dict[str, tuple[str, ...]] = field(hash=False)
    misses_desc: tuple[int, ...] = ()
    rank: int = 0
