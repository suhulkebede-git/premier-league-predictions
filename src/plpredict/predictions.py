"""Import the group's prediction spreadsheet into validated, normalised records.

Sheet layout (spec: Data structures):
  * C3:C22  this season's 20 clubs
  * A3:B22  last season's final table (club, points)
  * From row 26, person blocks repeat every 24 rows, two side by side
    (columns A-D and F-I): a "Name" cell with the name beside it, a header
    row, then 20 rows of position, predicted club, award category, award pick.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

from .models import (
    AWARD_CATEGORIES,
    CATEGORY_BY_LABEL,
    AwardPick,
    PickKind,
    Prediction,
    PredictionSet,
)
from .names import Aliases, NameResolver, normalise

log = logging.getLogger(__name__)

N_CLUBS = 20
FIRST_BLOCK_ROW = 26
BLOCK_STRIDE = 24
BLOCK_START_COLS = (1, 6)  # A and F
SEASON_CLUB_COL = 3  # C
TABLE_FIRST_ROW = 3


class InvalidPredictionError(ValueError):
    """Raised when the sheet's structure or a person's picks are invalid."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            f"{len(problems)} problem(s) in the predictions sheet:\n"
            + "\n".join(f"  {p}" for p in problems)
        )


def _cell(ws: Worksheet, row: int, col: int):
    value = ws.cell(row=row, column=col).value
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def open_sheet(path: Path) -> Worksheet:
    """Open the first worksheet of an .xlsx file (values, not formulas)."""
    try:
        wb = load_workbook(Path(path), data_only=True, read_only=False)
    except FileNotFoundError:
        log.error("Predictions sheet not found: %s", path)
        raise
    except (InvalidFileException, BadZipFile, KeyError, OSError) as exc:
        raise InvalidPredictionError([f"Cannot open {path} as an .xlsx workbook: {exc}"]) from exc
    log.info("Opened %s (sheet %r, %d rows)", path, wb.active.title, wb.active.max_row)
    return wb.active


def _read_season_clubs(ws: Worksheet) -> list[str]:
    clubs = [_cell(ws, TABLE_FIRST_ROW + i, SEASON_CLUB_COL) for i in range(N_CLUBS)]
    if any(c is None for c in clubs):
        raise InvalidPredictionError(["Season club list C3:C22 has blank cells"])
    clubs = [str(c) for c in clubs]
    if len({normalise(c) for c in clubs}) != N_CLUBS:
        raise InvalidPredictionError(["Season club list C3:C22 has duplicate clubs"])
    return clubs


def _read_last_season(ws: Worksheet, names: NameResolver, problems: list[str]) -> dict[str, int]:
    points: dict[str, int] = {}
    for i in range(N_CLUBS):
        row = TABLE_FIRST_ROW + i
        raw_club, raw_pts = _cell(ws, row, 1), _cell(ws, row, 2)
        if raw_club is None:
            problems.append(f"Last season table row {row} has no club")
            continue
        club = names.club(raw_club, required=False)
        if club is None:
            log.info("Last season club %r is not in this season (relegated); skipped", raw_club)
            continue
        if not isinstance(raw_pts, (int, float)) or raw_pts < 0:
            problems.append(f"Last season points for {raw_club!r} (row {row}) are not a number")
            continue
        points[club] = int(raw_pts)
    return points


def _parse_number(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if float(raw).is_integer() else None
    match = re.fullmatch(r"\s*(\d+)\s*(points?|pts)?\s*", str(raw), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_pick(kind: PickKind, raw: object, names: NameResolver) -> AwardPick | str:
    """Return an AwardPick, or a problem description string."""
    text = str(raw).strip()
    if kind is PickKind.CLUB:
        parts = re.split(r"\s+or\s+", text, flags=re.IGNORECASE)
        if len(parts) > 2:
            return f"club pick {text!r} names more than two clubs"
        value = names.club(parts[0])
        alt = names.club(parts[1]) if len(parts) == 2 else None
        return AwardPick(raw=text, value=value, alt=alt)
    if kind is PickKind.PLAYER:
        return AwardPick(raw=text, value=names.person_name(text, "players"))
    if kind is PickKind.MANAGER:
        return AwardPick(raw=text, value=names.person_name(text, "managers"))
    if kind is PickKind.NUMBER:
        number = _parse_number(raw)
        if number is None:
            return f"{text!r} is not a whole number of points"
        return AwardPick(raw=text, value=number)
    if kind is PickKind.YES_NO:
        return AwardPick(raw=text, value=names.answer(text))
    raise AssertionError(f"Unhandled pick kind {kind}")  # pragma: no cover


def _read_block(
    ws: Worksheet, top: int, col0: int, names: NameResolver, problems: list[str]
) -> Prediction | None:
    person = _cell(ws, top, col0 + 1)
    where = f"block at row {top}, column {ws.cell(row=top, column=col0).column_letter}"
    if person is None:
        problems.append(f"{where}: 'Name' cell has no name beside it")
        return None
    person = str(person)
    data_rows = range(top + 2, top + 2 + N_CLUBS)
    raw_clubs = [_cell(ws, r, col0 + 1) for r in data_rows]
    raw_picks = [_cell(ws, r, col0 + 3) for r in data_rows]
    if all(c is None for c in raw_clubs) and all(p is None for p in raw_picks):
        log.info("Skipping %s: block is blank", person)
        return None

    for i, r in enumerate(data_rows):
        pos = _cell(ws, r, col0)
        if not isinstance(pos, (int, float)) or int(pos) != i + 1:
            problems.append(f"{person}: row {r} position is {pos!r}, expected {i + 1}")

    order: list[str] = []
    for r, raw in zip(data_rows, raw_clubs):
        if raw is None:
            problems.append(f"{person}: no club predicted at row {r}")
            continue
        club = names.club(raw)
        if club is not None:
            order.append(club)
    dupes = sorted({c for c in order if order.count(c) > 1})
    if dupes:
        problems.append(f"{person}: clubs predicted more than once: {', '.join(dupes)}")

    awards: dict[str, AwardPick] = {}
    for r, category, raw in zip(data_rows, AWARD_CATEGORIES, raw_picks):
        label = _cell(ws, r, col0 + 2)
        found = CATEGORY_BY_LABEL.get(normalise(label)) if label else None
        if found is None or found.key != category.key:
            problems.append(
                f"{person}: row {r} award label {label!r}, expected {category.label!r}"
            )
            continue
        if raw is None:
            problems.append(f"{person}: no pick for {category.label!r}")
            continue
        pick = _parse_pick(category.kind, raw, names)
        if isinstance(pick, str):
            problems.append(f"{person}: {category.label}: {pick}")
        else:
            awards[category.key] = pick

    return Prediction(person=person, order=tuple(order), awards=awards)


def parse_predictions(ws: Worksheet, aliases: Aliases) -> PredictionSet:
    """Parse and validate every person's predictions from the worksheet.

    Raises:
        UnknownNameError: some spellings have no confirmed alias (all listed).
        InvalidPredictionError: structural problems (all listed).
    """
    clubs = _read_season_clubs(ws)
    names = NameResolver(aliases, clubs)
    problems: list[str] = []
    last_season = _read_last_season(ws, names, problems)
    promoted = tuple(c for c in clubs if c not in last_season)

    predictions: list[Prediction] = []
    seen: set[str] = set()
    top = FIRST_BLOCK_ROW
    while top <= ws.max_row:
        labels = [normalise(_cell(ws, top, c) or "") for c in BLOCK_START_COLS]
        if "name" not in labels:
            break
        for col0, label in zip(BLOCK_START_COLS, labels):
            if label != "name":
                continue
            pred = _read_block(ws, top, col0, names, problems)
            if pred is None:
                continue
            if normalise(pred.person) in seen:
                problems.append(f"Person {pred.person!r} appears more than once")
                continue
            seen.add(normalise(pred.person))
            predictions.append(pred)
        top += BLOCK_STRIDE

    names.raise_if_unmapped()
    if not predictions:
        problems.append("No filled-in prediction blocks found from row 26")
    if problems:
        raise InvalidPredictionError(problems)

    log.info(
        "Parsed %d people (%s); promoted clubs: %s",
        len(predictions),
        ", ".join(p.person for p in predictions),
        ", ".join(promoted),
    )
    return PredictionSet(
        season_clubs=tuple(clubs),
        promoted_clubs=promoted,
        last_season_points=last_season,
        predictions=tuple(predictions),
    )


def save_predictions(pset: PredictionSet, path: Path) -> None:
    """Write the parsed predictions to JSON (the file the scheduled job reads)."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(pset), indent=2) + "\n", encoding="utf-8")
    except OSError:
        log.exception("Could not write predictions to %s", path)
        raise
    log.info("Saved %d people's predictions to %s", len(pset.predictions), path)


def load_predictions(path: Path) -> PredictionSet:
    """Load predictions previously written by :func:`save_predictions`.

    Raises:
        FileNotFoundError: the file does not exist.
        InvalidPredictionError: the JSON is malformed or fails validation.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        clubs = tuple(data["season_clubs"])
        preds = tuple(
            Prediction(
                person=p["person"],
                order=tuple(p["order"]),
                awards={k: AwardPick(**v) for k, v in p["awards"].items()},
            )
            for p in data["predictions"]
        )
        pset = PredictionSet(
            season_clubs=clubs,
            promoted_clubs=tuple(data["promoted_clubs"]),
            last_season_points={k: int(v) for k, v in data["last_season_points"].items()},
            predictions=preds,
        )
    except FileNotFoundError:
        log.error("Predictions file not found: %s", path)
        raise
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InvalidPredictionError([f"{path} is not a valid predictions file: {exc!r}"]) from exc

    problems = []
    if len(clubs) != N_CLUBS:
        problems.append(f"expected {N_CLUBS} season clubs, found {len(clubs)}")
    for p in preds:
        if sorted(p.order) != sorted(clubs):
            problems.append(f"{p.person}: order is not a permutation of the season's clubs")
        missing = {c.key for c in AWARD_CATEGORIES} - set(p.awards)
        if missing:
            problems.append(f"{p.person}: missing award picks {sorted(missing)}")
    if problems:
        raise InvalidPredictionError(problems)
    log.info("Loaded %d people's predictions from %s", len(preds), path)
    return pset
