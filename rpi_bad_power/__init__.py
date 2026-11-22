"""A library reading under-voltage status from the official Raspberry Pi kernel.

Minimal kernel needed is 4.14+.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import os
from pathlib import Path

from rpi_bad_power._sysfs import is_readable_file, parse_alarm_value
from rpi_bad_power.exceptions import RpiBadPowerError
from rpi_bad_power.throttled import (
    ThrottledFlag,
    ThrottledStatus,
    read_throttled_status,
)
from rpi_bad_power.watcher import AsyncUnderVoltageWatcher, supports_sysfs_poll

__version__ = "0.2.0"

__all__ = [
    "HWMON_NAME",
    "SYSFILE_HWMON_DIR",
    "SYSFILE_HWMON_FILE",
    "SYSFILE_LEGACY",
    "UNDERVOLTAGE_STICKY_BIT",
    "AsyncUnderVoltageWatcher",
    "RpiBadPowerError",
    "ThrottledFlag",
    "ThrottledStatus",
    "UnderVoltage",
    "UnderVoltageLegacy",
    "UnderVoltageNew",
    "__version__",
    "get_rpi_volt_hwmon",
    "get_throttled_status",
    "new_under_voltage",
]

_LOGGER = logging.getLogger(__name__)

HWMON_NAME = "rpi_volt"

SYSFILE_HWMON_DIR = "/sys/class/hwmon"
SYSFILE_HWMON_FILE = "in0_lcrit_alarm"
SYSFILE_LEGACY = "/sys/devices/platform/soc/soc:firmware/get_throttled"

UNDERVOLTAGE_STICKY_BIT = 1 << 16


def get_rpi_volt_hwmon() -> str | None:
    """Find the ``rpi_volt`` hwmon device directory path."""
    hwmon_root = Path(SYSFILE_HWMON_DIR)
    try:
        entries = sorted(hwmon_root.iterdir())
    except OSError as err:
        _LOGGER.debug("Unable to list hwmon directory %s: %s", hwmon_root, err)
        return None

    for hwmon_dir in entries:
        name_file = hwmon_dir / "name"
        try:
            if not name_file.is_file():
                continue
            with name_file.open(encoding="utf-8") as file:
                hwmon_name = file.read().strip()
        except (OSError, UnicodeDecodeError) as err:
            _LOGGER.debug("Skipping unreadable hwmon entry %s: %s", name_file, err)
            continue
        if hwmon_name == HWMON_NAME:
            return str(hwmon_dir)

    return None


class UnderVoltage(ABC):
    """Abstract base class for reading Raspberry Pi under-voltage status."""

    @property
    def supports_push(self) -> bool:
        """Return True if kernel ``sysfs_notify`` push notifications are supported."""
        return False

    @abstractmethod
    def get(self) -> bool:
        """Get current under-voltage status."""

    def async_watch(self, *, emit_initial: bool = True) -> AsyncUnderVoltageWatcher:
        """Watch for kernel under-voltage events asynchronously."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support push notifications"
        )


class UnderVoltageNew(UnderVoltage):
    """Read under-voltage status from the modern ``raspberrypi-hwmon`` sysfs entry."""

    def __init__(self, hwmon: str | os.PathLike[str]) -> None:
        """Initialize the modern hwmon under-voltage reader."""
        self._hwmon = os.fspath(hwmon)
        self._alarm_path = Path(self._hwmon) / SYSFILE_HWMON_FILE

    @property
    def alarm_path(self) -> Path:
        """Return the resolved ``in0_lcrit_alarm`` sysfs file path."""
        return self._alarm_path

    @property
    def supports_push(self) -> bool:
        """Return True if ``select.poll`` with ``POLLPRI`` is supported."""
        return supports_sysfs_poll()

    def async_watch(self, *, emit_initial: bool = True) -> AsyncUnderVoltageWatcher:
        """Return an async watcher for ``in0_lcrit_alarm`` kernel events."""
        if not self.supports_push:
            raise NotImplementedError(
                "sysfs_notify push watching requires select.poll with POLLPRI"
            )
        return AsyncUnderVoltageWatcher(
            self._alarm_path,
            emit_initial=emit_initial,
        )

    def get(self) -> bool:
        """Get under-voltage status from ``in0_lcrit_alarm``."""
        try:
            with self._alarm_path.open(encoding="utf-8") as file:
                raw = file.read()
        except OSError as err:
            raise RpiBadPowerError.from_os_error(
                "Failed to read under-voltage status",
                err,
                self._alarm_path,
            ) from err
        except UnicodeDecodeError as err:
            raise RpiBadPowerError(
                f"Failed to decode under-voltage status from {self._alarm_path}: {err}"
            ) from err
        return parse_alarm_value(raw, self._alarm_path)


class UnderVoltageLegacy(UnderVoltage):
    """Read under-voltage status from the legacy ``get_throttled`` sysfs entry."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        """Initialize the legacy under-voltage reader."""
        self._path: Path | None = Path(path) if path is not None else None

    @property
    def throttled_path(self) -> Path:
        """Return the ``get_throttled`` sysfs file path."""
        return self._path if self._path is not None else Path(SYSFILE_LEGACY)

    def get_throttled_status(self) -> ThrottledStatus:
        """Read and decode the full ``get_throttled`` firmware bitmask."""
        return read_throttled_status(self.throttled_path)

    def get(self) -> bool:
        """Get under-voltage status from ``get_throttled``."""
        status = self.get_throttled_status()
        return (status.raw & UNDERVOLTAGE_STICKY_BIT) == UNDERVOLTAGE_STICKY_BIT


def get_throttled_status(
    path: str | os.PathLike[str] | None = None,
) -> ThrottledStatus | None:
    """Read and decode the Raspberry Pi ``get_throttled`` sysfs bitmask.

    Returns ``None`` if the ``get_throttled`` sysfs entry is not present on the
    current system or kernel. Raises :class:`RpiBadPowerError` if the file
    exists but cannot be read or contains invalid hexadecimal data.
    """
    target = Path(path) if path is not None else Path(SYSFILE_LEGACY)
    try:
        return read_throttled_status(target)
    except RpiBadPowerError as err:
        if isinstance(err.__cause__, (FileNotFoundError, NotADirectoryError)):
            return None
        raise


def new_under_voltage() -> UnderVoltage | None:
    """Create an ``UnderVoltage`` reader for the current Raspberry Pi kernel.

    Returns ``None`` on unsupported hardware, kernels, or container mounts.
    """
    hwmon = get_rpi_volt_hwmon()
    if hwmon is not None:
        candidate = UnderVoltageNew(hwmon)
        if is_readable_file(candidate.alarm_path):
            return candidate
        _LOGGER.debug(
            "Found %s hwmon at %s but %s is not readable",
            HWMON_NAME,
            hwmon,
            candidate.alarm_path,
        )

    legacy_path = Path(SYSFILE_LEGACY)
    if is_readable_file(legacy_path):
        return UnderVoltageLegacy()

    return None
