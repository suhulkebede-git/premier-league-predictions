"""Name normalisation and alias resolution for clubs, players, managers, answers."""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ALIAS_SECTIONS = ("clubs", "players", "managers", "answers")


class UnknownNameError(ValueError):
    """Raised when one or more spellings cannot be mapped to a canonical value."""

    def __init__(self, unmapped: list[tuple[str, str, str | None]]):
        # unmapped: (section, raw spelling, suggestion or None)
        self.unmapped = unmapped
        lines = [
            f"  [{section}] {raw!r}" + (f" -> did you mean {sug!r}?" if sug else "")
            for section, raw, sug in unmapped
        ]
        super().__init__(
            f"{len(unmapped)} name(s) need a confirmed alias in aliases.json:\n"
            + "\n".join(lines)
        )


def normalise(text: object) -> str:
    """Lowercase, strip accents and collapse whitespace: 'Ødegaard ' -> 'odegaard'."""
    if text is None:
        raise ValueError("Cannot normalise an empty value")
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ø", "o").replace("Ø", "o")
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass(frozen=True)
class Aliases:
    clubs: dict[str, str] = field(default_factory=dict)
    players: dict[str, str] = field(default_factory=dict)
    managers: dict[str, str] = field(default_factory=dict)
    answers: dict[str, bool] = field(default_factory=dict)


def load_aliases(path: Path) -> Aliases:
    """Load aliases.json, normalising every key.

    Raises:
        FileNotFoundError: the file does not exist.
        ValueError: the file is not valid JSON or a section has the wrong shape.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error("Alias file not found: %s", path)
        raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"Alias file {path} is not valid JSON: {exc}") from exc

    sections = {}
    for name in ALIAS_SECTIONS:
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise ValueError(f"Alias section {name!r} in {path} must be an object")
        sections[name] = {normalise(k): v for k, v in section.items()}
    for raw, value in sections["answers"].items():
        if not isinstance(value, bool):
            raise ValueError(f"Answer alias {raw!r} must map to true or false")
    log.info(
        "Loaded aliases: %s",
        ", ".join(f"{len(sections[s])} {s}" for s in ALIAS_SECTIONS),
    )
    return Aliases(**sections)


def suggest(raw: str, candidates: list[str]) -> str | None:
    """Closest candidate to ``raw`` (case/accent-insensitive), or None."""
    by_norm = {normalise(c): c for c in candidates}
    match = difflib.get_close_matches(normalise(raw), list(by_norm), n=1, cutoff=0.6)
    return by_norm[match[0]] if match else None


class NameResolver:
    """Maps raw spellings to canonical values, collecting every miss.

    Misses are collected rather than raised one at a time, so a single run
    reports every name that needs confirming.
    """

    def __init__(self, aliases: Aliases, clubs: list[str]):
        if len(set(clubs)) != len(clubs):
            raise ValueError("Season club list contains duplicates")
        self.aliases = aliases
        self.clubs = list(clubs)
        self._club_by_norm = {normalise(c): c for c in clubs}
        self.unmapped: list[tuple[str, str, str | None]] = []

    def _miss(self, section: str, raw: str, candidates: list[str]) -> None:
        entry = (section, str(raw).strip(), suggest(str(raw), candidates))
        if entry not in self.unmapped:
            log.warning("Unmapped %s spelling %r (suggest %r)", section, entry[1], entry[2])
            self.unmapped.append(entry)

    def club(self, raw: object, *, required: bool = True) -> str | None:
        key = normalise(raw)
        if key in self._club_by_norm:
            return self._club_by_norm[key]
        if key in self.aliases.clubs:
            target = self.aliases.clubs[key]
            if target not in self.clubs:
                raise ValueError(f"Alias {raw!r} points at {target!r}, not a season club")
            return target
        if required:
            self._miss("clubs", str(raw), self.clubs + list(self.aliases.clubs))
        return None

    def person_name(self, raw: object, section: str) -> str | None:
        table = getattr(self.aliases, section)
        key = normalise(raw)
        if key in table:
            return table[key]
        canonical = {normalise(v): v for v in table.values()}
        if key in canonical:
            return canonical[key]
        self._miss(section, str(raw), sorted(set(table.values())))
        return None

    def answer(self, raw: object) -> bool | None:
        key = normalise(raw)
        if key in self.aliases.answers:
            return self.aliases.answers[key]
        self._miss("answers", str(raw), list(self.aliases.answers))
        return None

    def raise_if_unmapped(self) -> None:
        if self.unmapped:
            raise UnknownNameError(self.unmapped)
