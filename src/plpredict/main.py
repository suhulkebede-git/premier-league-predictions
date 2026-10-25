"""Entry points: the scheduled scoring run and the one-off sheet import.

  plpredict run                      fetch, score, write site/standings.json
  plpredict import-sheet FILE.xlsx   re-import predictions into data/predictions.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import logging
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from .awards import load_manual_awards, resolve_awards
from .fetch import ApiError, Client, fetch_matches, fetch_scorers, fetch_table
from .logging_config import configure_logging
from .models import AWARD_CATEGORIES, AwardStatus, PersonScore, PredictionSet, TableRow
from .names import NameResolver, UnknownNameError, load_aliases
from .predictions import (
    InvalidPredictionError,
    load_predictions,
    open_sheet,
    parse_predictions,
    save_predictions,
)
from .scoring import InvalidTableError, score_all

log = logging.getLogger(__name__)

TOKEN_ENV = "FOOTBALL_DATA_TOKEN"


def build_standings(
    pset: PredictionSet,
    table: list[TableRow],
    awards: dict[str, AwardStatus],
    scores: list[PersonScore],
    now: datetime,
) -> dict:
    """Everything the static site needs, as one JSON-ready dict."""
    labels = {c.key: c.label for c in AWARD_CATEGORIES}
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "games_played": max((r.played for r in table), default=0),
        "season_over": all(r.played >= 38 for r in table),
        "table": [asdict(r) for r in sorted(table, key=lambda r: r.position)],
        "awards": [{**asdict(awards[c.key]), "label": labels[c.key]} for c in AWARD_CATEGORIES],
        "scores": [asdict(s) for s in scores],
        "predictions": {
            p.person: {"order": list(p.order),
                       "awards": {k: asdict(v) for k, v in p.awards.items()}}
            for p in pset.predictions
        },
    }


def write_json_atomic(data: dict, path: Path) -> None:
    """Write via a temp file + rename so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        log.exception("Could not write %s", path)
        Path(tmp).unlink(missing_ok=True)
        raise


def run(
    *,
    data_dir: Path,
    out_path: Path,
    client: Client,
    today: date | None = None,
    now: datetime | None = None,
) -> dict:
    """One scheduled run. Leaves the previous output untouched on any failure."""
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    aliases = load_aliases(data_dir / "aliases.json")
    pset = load_predictions(data_dir / "predictions.json")
    names = NameResolver(aliases, list(pset.season_clubs))
    manual = load_manual_awards(data_dir / "manual_awards.json", names)

    table = fetch_table(client, names)
    matches = fetch_matches(client, names)
    scorers = fetch_scorers(client)

    awards = resolve_awards(table, matches, scorers, pset, manual, aliases, today)
    scores = score_all(pset.predictions, table, pset.season_clubs, awards)
    standings = build_standings(pset, table, awards, scores, now)
    write_json_atomic(standings, out_path)
    log.info("Wrote %s: leader %s (%d pts) after %d games",
             out_path, scores[0].person, scores[0].total, standings["games_played"])
    return standings


def import_sheet(xlsx: Path, data_dir: Path) -> PredictionSet:
    aliases = load_aliases(data_dir / "aliases.json")
    pset = parse_predictions(open_sheet(xlsx), aliases)
    save_predictions(pset, data_dir / "predictions.json")
    return pset


TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")


def describe_token(token: str) -> tuple[bool, str]:
    """Check a token's shape and describe it without revealing it.

    Returns (looks_valid, description). The description gives the length,
    a SHA-256 fingerprint (first 8 hex chars, useless for authenticating)
    and any obvious paste mistakes, so a bad secret can be diagnosed from
    the logs.
    """
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    problems = []
    if any(q in token for q in "\"'\u201c\u201d\u2018\u2019"):
        problems.append("contains quote marks")
    if any(ch.isspace() for ch in token):
        problems.append("contains spaces or line breaks")
    if ":" in token:
        problems.append("contains ':' (paste only the token, not 'X-Auth-Token:')")
    if not problems and not TOKEN_PATTERN.fullmatch(token):
        problems.append("is not 32 lowercase hex characters")
    desc = f"{len(token)} characters, sha256 fingerprint {fingerprint}"
    if problems:
        desc += "; " + "; ".join(problems)
    return (not problems, desc)


def github_annotation(title: str, message: str, level: str = "error") -> None:
    """Surface an error on the GitHub Actions run summary (no-op elsewhere).

    Uses the ::error workflow command, which must go to stdout; newlines are
    %-encoded as the command format requires.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    encoded = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    sys.stdout.write(f"::{level} title={title}::{encoded}\n")
    sys.stdout.flush()


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plpredict", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="fetch live data, score everyone, write standings")
    run_p.add_argument("--out", type=Path, default=Path("site/standings.json"))
    imp = sub.add_parser("import-sheet", help="re-import the predictions spreadsheet")
    imp.add_argument("xlsx", type=Path)
    args = parser.parse_args(argv)

    try:
        configure_logging(args.log_level)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        if args.command == "import-sheet":
            import_sheet(args.xlsx, args.data_dir)
            return 0
        raw = os.environ.get(TOKEN_ENV, "")
        if not raw.strip():
            log.error("Set %s to your football-data.org API token", TOKEN_ENV)
            return 2
        token = raw.strip()
        ok, desc = describe_token(token)
        if not ok:
            log.error("%s looks wrong: %s", TOKEN_ENV, desc)
            github_annotation(f"{TOKEN_ENV} looks wrong", desc)
            return 2
        log.info("Using %s (%s)", TOKEN_ENV, desc)
        github_annotation(TOKEN_ENV, desc, level="notice")
        run(data_dir=args.data_dir, out_path=args.out, client=Client(token))
        return 0
    except (UnknownNameError, InvalidPredictionError, InvalidTableError, ApiError,
            FileNotFoundError, ValueError) as exc:
        log.error("%s failed; previous output left unchanged:\n%s", args.command, exc)
        github_annotation(f"plpredict {args.command} failed", f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
