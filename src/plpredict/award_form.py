"""Apply a hand-entered award submitted through the GitHub issue form.

The issue form (.github/ISSUE_TEMPLATE/award.yml) renders its answers as
markdown under "### <label>" headings. ``parse_issue_body`` reads those back,
``apply_award`` validates the names against aliases.json and the season's clubs
and updates data/manual_awards.json, and ``cli`` wires both up for the
award-form workflow, writing a markdown reply for the issue comment.

Nothing is written unless every name resolves, so a typo leaves the files
unchanged and the reply suggests the closest spelling instead.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from plpredict.awards import MANUAL_KEYS, ManualAwardsError, load_manual_awards
from plpredict.models import CATEGORY_BY_KEY, PickKind
from plpredict.names import NameResolver, load_aliases, normalise, suggest
from plpredict.predictions import load_predictions

log = logging.getLogger(__name__)

NO_RESPONSE = "_No response_"
CLEAR_WORDS = {"none", "clear", "reset", "nobody"}
CONFIRM_NEW_NAME = "spelled correctly"  # substring of the checkbox label


class AwardFormError(ValueError):
    """The submitted form can't be applied; the message is shown to the submitter."""


@dataclass(frozen=True)
class FormEntry:
    key: str
    winners: tuple[str, ...]  # raw spellings; empty means clear the award
    gameweek: int | None
    final: bool
    new_name_ok: bool


@dataclass(frozen=True)
class AppliedAward:
    key: str
    leaders: tuple[object, ...]
    settled: bool
    from_gameweek: int | None
    added_aliases: tuple[str, ...]


def _sections(body: str) -> dict[str, str]:
    """Map each '### Heading' (lower-cased) to the text under it."""
    out: dict[str, str] = {}
    for block in re.split(r"^###\s+", body or "", flags=re.M)[1:]:
        heading, _, text = block.partition("\n")
        text = text.strip()
        out[heading.strip().lower()] = "" if text == NO_RESPONSE else text
    return out


def _find(sections: dict[str, str], word: str) -> str | None:
    for heading, text in sections.items():
        if word in heading:
            return text
    return None


def parse_issue_body(body: str) -> FormEntry:
    """Read the award form's answers from a rendered issue body.

    Raises:
        AwardFormError: a required answer is missing or malformed.
    """
    sections = _sections(body)
    award = _find(sections, "award")
    if not award:
        raise AwardFormError("The form has no **Award** answer. Please use the *Hand-entry award* issue form.")
    by_label = {normalise(CATEGORY_BY_KEY[k].label): k for k in MANUAL_KEYS}
    key = by_label.get(normalise(award)) or (award.strip() if award.strip() in MANUAL_KEYS else None)
    if key is None:
        labels = ", ".join(CATEGORY_BY_KEY[k].label for k in MANUAL_KEYS)
        raise AwardFormError(f"Unknown award {award!r}. Pick one of: {labels}.")

    raw_winners = _find(sections, "winner") or ""
    if not raw_winners.strip():
        raise AwardFormError("**Winner** is empty. Type a name, or `none` to clear the award.")
    parts = [p.strip() for p in re.split(r"[,\n]|\band\b", raw_winners) if p.strip()]
    winners = () if len(parts) == 1 and normalise(parts[0]) in CLEAR_WORDS else tuple(parts)

    gw_text = (_find(sections, "gameweek") or "").strip()
    gameweek = None
    if gw_text:
        match = re.fullmatch(r"(?:gw|gameweek)?\s*(\d{1,2})", gw_text, flags=re.I)
        if not match or not 1 <= int(match.group(1)) <= 38:
            raise AwardFormError(f"**Gameweek** must be a number from 1 to 38, not {gw_text!r}.")
        gameweek = int(match.group(1))

    final_text = normalise(_find(sections, "final") or "")
    checks = _find(sections, "check") or ""
    new_name_ok = any(CONFIRM_NEW_NAME in line.lower() and re.match(r"-\s*\[[xX]\]", line.strip())
                      for line in checks.splitlines())
    return FormEntry(key, winners, gameweek, final_text.startswith("yes"), new_name_ok)


def _resolve(entry: FormEntry, names: NameResolver) -> tuple[list[object], list[str]]:
    """Canonical leaders plus any new player/manager names to add as aliases."""
    kind = CATEGORY_BY_KEY[entry.key].kind
    leaders: list[object] = []
    new_names: list[str] = []
    problems: list[str] = []
    for raw in entry.winners:
        if kind is PickKind.CLUB:
            value = names.club(raw, required=False)
            if value is None:
                hint = suggest(raw, names.clubs + list(names.aliases.clubs))
                problems.append(f"`{raw}` isn't a club this season"
                                + (f" — did you mean **{hint}**?" if hint else "."))
                continue
        elif kind is PickKind.YES_NO:
            value = names.aliases.answers.get(normalise(raw))
            if value is None:
                problems.append(f"`{raw}` isn't a yes/no answer — type `yes` or `no`.")
                continue
        else:
            section = "players" if kind is PickKind.PLAYER else "managers"
            table = getattr(names.aliases, section)
            known = {normalise(v): v for v in table.values()}
            value = table.get(normalise(raw)) or known.get(normalise(raw))
            if value is None:
                hint = suggest(raw, sorted(set(table.values())))
                if not entry.new_name_ok:
                    problems.append(
                        f"`{raw}` doesn't match any {section[:-1]} anyone picked"
                        + (f" — did you mean **{hint}**?" if hint else ".")
                        + " If the spelling is right, tick *The name is spelled correctly* and resubmit.")
                    continue
                value = raw.strip()
                new_names.append(value)
        if value not in leaders:
            leaders.append(value)
    if problems:
        raise AwardFormError("Nothing was saved:\n\n" + "\n".join(f"- {p}" for p in problems))
    return leaders, new_names


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def apply_award(entry: FormEntry, data_dir: Path) -> AppliedAward:
    """Validate ``entry`` and write it into manual_awards.json (and aliases.json
    for a confirmed new player/manager name).

    Raises:
        AwardFormError: a name doesn't resolve; nothing is written.
        FileNotFoundError / ValueError: the data files are missing or broken.
    """
    data_dir = Path(data_dir)
    aliases_path, awards_path = data_dir / "aliases.json", data_dir / "manual_awards.json"
    aliases = load_aliases(aliases_path)
    pset = load_predictions(data_dir / "predictions.json")
    names = NameResolver(aliases, list(pset.season_clubs))
    leaders, new_names = _resolve(entry, names)

    awards = json.loads(awards_path.read_text(encoding="utf-8")) if awards_path.exists() else {}
    record = {
        "leaders": leaders,
        "settled": bool(entry.final and leaders),
        "from_gameweek": entry.gameweek if leaders else None,
    }
    awards[entry.key] = record

    raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    if new_names:
        section = "players" if CATEGORY_BY_KEY[entry.key].kind is PickKind.PLAYER else "managers"
        raw_aliases.setdefault(section, {})
        for name in new_names:
            raw_aliases[section].setdefault(name.lower(), name)

    # Prove the result loads before touching either file.
    check_dir = awards_path.parent
    check_awards = check_dir / ".manual_awards.check.json"
    check_aliases = check_dir / ".aliases.check.json"
    try:
        check_awards.write_text(json.dumps(awards), encoding="utf-8")
        check_aliases.write_text(json.dumps(raw_aliases), encoding="utf-8")
        load_manual_awards(check_awards, NameResolver(load_aliases(check_aliases), list(pset.season_clubs)))
    except ManualAwardsError as exc:
        raise AwardFormError(f"Nothing was saved: the result wouldn't load ({exc}).") from exc
    finally:
        check_awards.unlink(missing_ok=True)
        check_aliases.unlink(missing_ok=True)

    if new_names:
        _write_json(aliases_path, raw_aliases)
    _write_json(awards_path, awards)
    log.info("Saved %s: %s (settled=%s, from GW%s)", entry.key, leaders, record["settled"],
             record["from_gameweek"])
    return AppliedAward(entry.key, tuple(leaders), record["settled"], record["from_gameweek"],
                        tuple(new_names))


def _show(value: object) -> str:
    return {True: "Yes", False: "No"}.get(value, str(value)) if isinstance(value, bool) else str(value)


def reply_for(result: AppliedAward) -> str:
    label = CATEGORY_BY_KEY[result.key].label
    if not result.leaders:
        return f"Cleared **{label}**. It shows as pending again on the next update."
    who = ", ".join(_show(v) for v in result.leaders)
    lines = [f"Saved **{label}**: {who}" + (" (final)." if result.settled else " (provisional).")]
    if result.from_gameweek:
        lines.append(f"History counts it from gameweek {result.from_gameweek}.")
    if result.key == "most_red_cards":
        lines.append("This overrides the automatic red-card count from FPL.")
    if result.added_aliases:
        lines.append("Added as a new name: " + ", ".join(result.added_aliases) + ".")
    lines.append("The site picks it up on the next update (within about 15 minutes).")
    return "\n\n".join(lines)


def run_form(body: str, data_dir: Path) -> tuple[bool, str]:
    """Parse and apply one submission; returns (saved, markdown reply)."""
    try:
        result = apply_award(parse_issue_body(body), data_dir)
    except AwardFormError as exc:
        log.warning("Award form rejected: %s", exc)
        return False, str(exc)
    return True, reply_for(result)
