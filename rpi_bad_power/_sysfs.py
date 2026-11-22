"""Internal sysfs file helpers for rpi_bad_power."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rpi_bad_power.exceptions import RpiBadPowerError

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger("rpi_bad_power")


def is_readable_file(path: Path) -> bool:
    """Return True if path exists as a regular file and can be opened for reading."""
    try:
        if not path.is_file():
            return False
        with path.open(encoding="utf-8"):
            return True
    except OSError as err:
        _LOGGER.debug("Sysfs file %s is not readable: %s", path, err)
        return False


def parse_alarm_value(raw: str, path: Path) -> bool:
    """Parse an ``in0_lcrit_alarm`` sysfs string ('0' or '1') into a boolean."""
    bit = raw.strip()
    _LOGGER.debug("Get under voltage status: %s", bit)
    if bit == "1":
        return True
    if bit == "0":
        return False
    raise RpiBadPowerError(f"Unexpected under-voltage value {bit!r} from {path}")
