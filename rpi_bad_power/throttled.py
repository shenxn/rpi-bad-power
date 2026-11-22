"""Raspberry Pi firmware ``get_throttled`` bitmask decoding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
import logging
from typing import TYPE_CHECKING

from rpi_bad_power.exceptions import RpiBadPowerError

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger("rpi_bad_power")


class ThrottledFlag(IntFlag):
    """Bit flags reported by the Raspberry Pi firmware ``get_throttled`` interface."""

    NONE = 0
    UNDER_VOLTAGE_ACTIVE = 1 << 0
    ARM_FREQ_CAPPED_ACTIVE = 1 << 1
    THROTTLED_ACTIVE = 1 << 2
    SOFT_TEMP_LIMIT_ACTIVE = 1 << 3
    UNDER_VOLTAGE_OCCURRED = 1 << 16
    ARM_FREQ_CAPPED_OCCURRED = 1 << 17
    THROTTLED_OCCURRED = 1 << 18
    SOFT_TEMP_LIMIT_OCCURRED = 1 << 19


@dataclass(frozen=True, slots=True)
class ThrottledStatus:
    """Decoded Raspberry Pi firmware throttling and power status bitmask."""

    raw: int
    under_voltage: bool
    arm_frequency_capped: bool
    throttled: bool
    soft_temp_limit: bool
    under_voltage_occurred: bool
    arm_frequency_capped_occurred: bool
    throttled_occurred: bool
    soft_temp_limit_occurred: bool

    @property
    def flags(self) -> ThrottledFlag:
        """Return the bitmask as a :class:`ThrottledFlag` value."""
        return ThrottledFlag(self.raw)

    @property
    def any_active(self) -> bool:
        """Return True if any active throttling or under-voltage flag is set."""
        return (
            self.under_voltage
            or self.arm_frequency_capped
            or self.throttled
            or self.soft_temp_limit
        )

    @property
    def any_occurred(self) -> bool:
        """Return True if any sticky throttling or under-voltage flag is set."""
        return (
            self.under_voltage_occurred
            or self.arm_frequency_capped_occurred
            or self.throttled_occurred
            or self.soft_temp_limit_occurred
        )

    @classmethod
    def from_int(cls, raw: int) -> ThrottledStatus:
        """Decode a non-negative integer bitmask into a :class:`ThrottledStatus`."""
        if raw < 0:
            raise ValueError(f"Throttled bitmask must be non-negative, got {raw}")
        return cls(
            raw=raw,
            under_voltage=bool(raw & ThrottledFlag.UNDER_VOLTAGE_ACTIVE),
            arm_frequency_capped=bool(raw & ThrottledFlag.ARM_FREQ_CAPPED_ACTIVE),
            throttled=bool(raw & ThrottledFlag.THROTTLED_ACTIVE),
            soft_temp_limit=bool(raw & ThrottledFlag.SOFT_TEMP_LIMIT_ACTIVE),
            under_voltage_occurred=bool(raw & ThrottledFlag.UNDER_VOLTAGE_OCCURRED),
            arm_frequency_capped_occurred=bool(
                raw & ThrottledFlag.ARM_FREQ_CAPPED_OCCURRED
            ),
            throttled_occurred=bool(raw & ThrottledFlag.THROTTLED_OCCURRED),
            soft_temp_limit_occurred=bool(raw & ThrottledFlag.SOFT_TEMP_LIMIT_OCCURRED),
        )

    @classmethod
    def from_hex(cls, value: str) -> ThrottledStatus:
        """Parse a hexadecimal string (e.g. ``'50005'`` or ``'0x50005'``)."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Empty throttled hex value")
        raw = int(cleaned, base=16)
        return cls.from_int(raw)


def read_throttled_status(path: Path) -> ThrottledStatus:
    """Read and decode a ``get_throttled`` sysfs file."""
    try:
        with path.open(encoding="utf-8") as file:
            raw_text = file.read().strip()
    except OSError as err:
        raise RpiBadPowerError.from_os_error(
            "Failed to read legacy throttled status",
            err,
            path,
        ) from err
    except UnicodeDecodeError as err:
        raise RpiBadPowerError(
            f"Failed to decode legacy throttled status from {path}: {err}"
        ) from err

    _LOGGER.debug("Get throttled value: %s", raw_text)
    try:
        return ThrottledStatus.from_hex(raw_text)
    except ValueError as err:
        raise RpiBadPowerError(
            f"Invalid throttled hex value {raw_text!r} from {path}: {err}"
        ) from err
