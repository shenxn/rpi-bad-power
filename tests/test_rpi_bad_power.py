from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import rpi_bad_power
from rpi_bad_power import (
    HWMON_NAME,
    SYSFILE_HWMON_DIR,
    SYSFILE_HWMON_FILE,
    SYSFILE_LEGACY,
    UNDERVOLTAGE_STICKY_BIT,
    RpiBadPowerError,
    UnderVoltage,
    UnderVoltageLegacy,
    UnderVoltageNew,
    get_rpi_volt_hwmon,
    new_under_voltage,
)


def _configure_sysfs(
    monkeypatch: pytest.MonkeyPatch,
    hwmon_dir: Path,
    legacy_file: Path,
) -> None:
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_HWMON_DIR", str(hwmon_dir))
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_LEGACY", str(legacy_file))


def _create_hwmon_entry(
    hwmon_dir: Path,
    entry_name: str,
    device_name: str | None = None,
    alarm_content: str | None = None,
) -> Path:
    entry = hwmon_dir / entry_name
    entry.mkdir(parents=True, exist_ok=True)
    if device_name is not None:
        (entry / "name").write_text(device_name, encoding="utf-8")
    if alarm_content is not None:
        (entry / SYSFILE_HWMON_FILE).write_text(alarm_content, encoding="utf-8")
    return entry


def test_public_exports() -> None:
    assert HWMON_NAME == "rpi_volt"
    assert SYSFILE_HWMON_DIR == "/sys/class/hwmon"
    assert SYSFILE_HWMON_FILE == "in0_lcrit_alarm"
    assert SYSFILE_LEGACY == "/sys/devices/platform/soc/soc:firmware/get_throttled"
    assert UNDERVOLTAGE_STICKY_BIT == 1 << 16


def test_under_voltage_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError, match="abstract"):
        UnderVoltage()  # type: ignore[abstract]


def test_non_rpi_empty_hwmon_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    hwmon_dir.mkdir()
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    assert get_rpi_volt_hwmon() is None
    assert new_under_voltage() is None


def test_no_hwmon_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_sysfs(
        monkeypatch, tmp_path / "missing_hwmon", tmp_path / "get_throttled"
    )

    assert get_rpi_volt_hwmon() is None
    assert new_under_voltage() is None


def test_hwmon_dir_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    hwmon_dir.mkdir()
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    with patch("pathlib.Path.iterdir", side_effect=PermissionError("denied")):
        assert get_rpi_volt_hwmon() is None
        assert new_under_voltage() is None


def test_multi_hwmon_discovery_and_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    _create_hwmon_entry(hwmon_dir, "hwmon0", device_name="cpu_thermal\n")
    _create_hwmon_entry(hwmon_dir, "hwmon1")  # no name file
    rpi_entry = _create_hwmon_entry(
        hwmon_dir, "hwmon2", device_name="rpi_volt\n", alarm_content="0\n"
    )
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    assert get_rpi_volt_hwmon() == str(rpi_entry)
    under_voltage = new_under_voltage()
    assert isinstance(under_voltage, UnderVoltageNew)
    assert under_voltage.alarm_path == rpi_entry / SYSFILE_HWMON_FILE
    assert under_voltage.get() is False

    (rpi_entry / SYSFILE_HWMON_FILE).write_text("1\n", encoding="utf-8")
    assert under_voltage.get() is True


def test_symlinked_hwmon_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_device = tmp_path / "devices" / "hwmon0"
    real_device.mkdir(parents=True)
    (real_device / "name").write_text("rpi_volt\n", encoding="utf-8")
    (real_device / SYSFILE_HWMON_FILE).write_text("1\n", encoding="utf-8")

    hwmon_dir = tmp_path / "hwmon"
    hwmon_dir.mkdir()
    (hwmon_dir / "hwmon0").symlink_to(real_device)
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    under_voltage = new_under_voltage()
    assert isinstance(under_voltage, UnderVoltageNew)
    assert under_voltage.get() is True


def test_hwmon_skips_unreadable_and_corrupt_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    bad_utf8 = _create_hwmon_entry(hwmon_dir, "hwmon0")
    (bad_utf8 / "name").write_bytes(b"\xff\xfe")

    unreadable = _create_hwmon_entry(hwmon_dir, "hwmon1", device_name="other\n")
    rpi_entry = _create_hwmon_entry(
        hwmon_dir, "hwmon2", device_name="rpi_volt\n", alarm_content="0\n"
    )
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    original_open = Path.open

    def flaky_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == unreadable / "name":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", flaky_open):
        assert get_rpi_volt_hwmon() == str(rpi_entry)
        under_voltage = new_under_voltage()
        assert isinstance(under_voltage, UnderVoltageNew)
        assert under_voltage.get() is False


def test_hwmon_missing_alarm_file_falls_back_or_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    _create_hwmon_entry(hwmon_dir, "hwmon0", device_name="rpi_volt\n")
    legacy_file = tmp_path / "get_throttled"
    _configure_sysfs(monkeypatch, hwmon_dir, legacy_file)

    assert new_under_voltage() is None

    legacy_file.write_text("50005\n", encoding="utf-8")
    under_voltage = new_under_voltage()
    assert isinstance(under_voltage, UnderVoltageLegacy)
    assert under_voltage.get() is True


def test_hwmon_unreadable_alarm_file_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hwmon_dir = tmp_path / "hwmon"
    rpi_entry = _create_hwmon_entry(
        hwmon_dir, "hwmon0", device_name="rpi_volt\n", alarm_content="0\n"
    )
    _configure_sysfs(monkeypatch, hwmon_dir, tmp_path / "get_throttled")

    original_open = Path.open

    def deny_alarm(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == rpi_entry / SYSFILE_HWMON_FILE:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", deny_alarm):
        assert new_under_voltage() is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("0\n", False),
        ("1\n", True),
        ("0", False),
        ("1", True),
        ("0\r\n", False),
        ("1\r\n", True),
        ("  1  \n", True),
        ("  0  ", False),
    ],
)
def test_new_handles_whitespace_and_newline_variants(
    tmp_path: Path, content: str, expected: bool
) -> None:
    entry = _create_hwmon_entry(
        tmp_path, "hwmon0", device_name="rpi_volt\n", alarm_content=content
    )
    under_voltage = UnderVoltageNew(entry)
    assert under_voltage.get() is expected


@pytest.mark.parametrize("bad_content", ["", "\n", "2\n", "true\n", "01\n"])
def test_new_invalid_alarm_content_raises(tmp_path: Path, bad_content: str) -> None:
    entry = _create_hwmon_entry(
        tmp_path, "hwmon0", device_name="rpi_volt\n", alarm_content=bad_content
    )
    under_voltage = UnderVoltageNew(entry)
    with pytest.raises(RpiBadPowerError, match="Unexpected under-voltage value"):
        under_voltage.get()


def test_new_io_error_preserves_errno_and_oserror_compat(tmp_path: Path) -> None:
    entry = _create_hwmon_entry(
        tmp_path, "hwmon0", device_name="rpi_volt\n", alarm_content="0\n"
    )
    under_voltage = UnderVoltageNew(entry)

    io_err = OSError(errno.EIO, os.strerror(errno.EIO), str(under_voltage.alarm_path))
    with (
        patch.object(Path, "open", side_effect=io_err),
        pytest.raises(RpiBadPowerError) as exc_info,
    ):
        under_voltage.get()

    assert isinstance(exc_info.value, OSError)
    assert exc_info.value.errno == errno.EIO
    assert exc_info.value.filename == str(under_voltage.alarm_path)


def test_new_io_error_without_errno(tmp_path: Path) -> None:
    entry = _create_hwmon_entry(
        tmp_path, "hwmon0", device_name="rpi_volt\n", alarm_content="0\n"
    )
    under_voltage = UnderVoltageNew(entry)

    with (
        patch.object(Path, "open", side_effect=OSError("boom")),
        pytest.raises(RpiBadPowerError, match="boom") as exc_info,
    ):
        under_voltage.get()
    assert exc_info.value.errno is None


def test_new_invalid_utf8_raises(tmp_path: Path) -> None:
    entry = _create_hwmon_entry(tmp_path, "hwmon0", device_name="rpi_volt\n")
    (entry / SYSFILE_HWMON_FILE).write_bytes(b"\xff\xfe")
    under_voltage = UnderVoltageNew(entry)

    with pytest.raises(RpiBadPowerError, match="Failed to decode"):
        under_voltage.get()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("0\n", False),
        ("0", False),
        ("00000000\r\n", False),
        ("50005\n", True),
        ("50005", True),
        ("0x50005\n", True),
        ("10000\r\n", True),
        ("e000e\n", False),
    ],
)
def test_legacy_valid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, expected: bool
) -> None:
    legacy_file = tmp_path / "get_throttled"
    legacy_file.write_text(content, encoding="utf-8")
    _configure_sysfs(monkeypatch, tmp_path / "missing_hwmon", legacy_file)

    under_voltage = new_under_voltage()
    assert isinstance(under_voltage, UnderVoltageLegacy)
    assert under_voltage.throttled_path == legacy_file
    assert under_voltage.get() is expected


def test_legacy_explicit_path_override(tmp_path: Path) -> None:
    legacy_file = tmp_path / "custom_throttled"
    legacy_file.write_text("10000", encoding="utf-8")

    under_voltage = UnderVoltageLegacy(legacy_file)
    assert under_voltage.throttled_path == legacy_file
    assert under_voltage.get() is True


@pytest.mark.parametrize("bad_content", ["", "   \n", "zzzz\n", "-10000\n", "0xGG\n"])
def test_legacy_invalid_hex_raises(tmp_path: Path, bad_content: str) -> None:
    legacy_file = tmp_path / "get_throttled"
    legacy_file.write_text(bad_content, encoding="utf-8")

    under_voltage = UnderVoltageLegacy(legacy_file)
    with pytest.raises(RpiBadPowerError):
        under_voltage.get()


def test_legacy_io_and_decode_errors(tmp_path: Path) -> None:
    missing = UnderVoltageLegacy(tmp_path / "nonexistent")
    with pytest.raises(RpiBadPowerError) as exc_info:
        missing.get()
    assert exc_info.value.errno == errno.ENOENT

    corrupt = tmp_path / "corrupt_throttled"
    corrupt.write_bytes(b"\xff\xfe")
    with pytest.raises(RpiBadPowerError, match="Failed to decode"):
        UnderVoltageLegacy(corrupt).get()
