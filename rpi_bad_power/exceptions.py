"""Custom exceptions for rpi_bad_power."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class RpiBadPowerError(OSError):
    """Raised when reading or parsing Raspberry Pi power status fails.

    Subclasses ``OSError`` so existing ``except OSError:`` handlers (such as in
    Home Assistant Core) continue catching errors transparently.
    """

    @classmethod
    def from_os_error(
        cls,
        message: str,
        err: OSError,
        path: str | os.PathLike[str] | Path,
    ) -> RpiBadPowerError:
        """Create an error preserving ``errno`` and ``filename`` when available."""
        if err.errno is not None:
            detail = f"{message}: {err.strerror}" if err.strerror else message
            return cls(err.errno, detail, os.fspath(path))
        return cls(f"{message} ({os.fspath(path)}): {err}")
