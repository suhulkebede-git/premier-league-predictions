"""Standard-library logging setup shared by every entry point."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once with a consistent format.

    Raises:
        ValueError: if ``level`` is not a standard logging level name.
    """
    if not isinstance(level, str) or level.upper() not in _VALID_LEVELS:
        raise ValueError(
            f"Invalid log level {level!r}; expected one of {sorted(_VALID_LEVELS)}"
        )
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    logging.getLogger(__name__).debug("Logging configured at %s", level.upper())
