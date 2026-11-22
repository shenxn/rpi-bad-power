from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

import rpi_bad_power
from rpi_bad_power import (
    RpiBadPowerError,
    ThrottledFlag,
    ThrottledStatus,
    UnderVoltageLegacy,
    get_throttled_status,
)


def test_throttled_status_zero() -> None:
    status = ThrottledStatus.from_hex("0\n")
    assert status.raw == 0
    assert status.flags == ThrottledFlag.NONE
    assert status.any_active is False
    assert status.any_occurred is False
    assert status.under_voltage is False
    assert status.arm_frequency_capped is False
    assert status.throttled is False
    assert status.soft_temp_limit is False
    assert status.under_voltage_occurred is False
    assert status.arm_frequency_capped_occurred is False
    assert status.throttled_occurred is False
    assert status.soft_temp_limit_occurred is False


def test_throttled_status_typical_undervoltage_50005() -> None:
    status = ThrottledStatus.from_hex("0x50005")
    assert status.raw == 0x50005
    assert status.under_voltage is True
    assert status.arm_frequency_capped is False
    assert status.throttled is True
    assert status.soft_temp_limit is False
    assert status.under_voltage_occurred is True
    assert status.arm_frequency_capped_occurred is False
    assert status.throttled_occurred is True
    assert status.soft_temp_limit_occurred is False
    assert status.any_active is True
    assert status.any_occurred is True
    assert ThrottledFlag.UNDER_VOLTAGE_ACTIVE in status.flags
    assert ThrottledFlag.THROTTLED_OCCURRED in status.flags


def test_throttled_status_all_flags_and_unknown_bits() -> None:
    raw = 0x1F000F
    status = ThrottledStatus.from_int(raw)
    assert status.raw == raw
    assert status.under_voltage is True
    assert status.arm_frequency_capped is True
    assert status.throttled is True
    assert status.soft_temp_limit is True
    assert status.under_voltage_occurred is True
    assert status.arm_frequency_capped_occurred is True
    assert status.throttled_occurred is True
    assert status.soft_temp_limit_occurred is True
    assert status.any_active is True
    assert status.any_occurred is True


@pytest.mark.parametrize(
    ("raw", "expected_active", "expected_occurred"),
    [
        (0x00001, True, False),
        (0x00002, True, False),
        (0x00004, True, False),
        (0x00008, True, False),
        (0x10000, False, True),
        (0x20000, False, True),
        (0x40000, False, True),
        (0x80000, False, True),
    ],
)
def test_throttled_status_individual_bits(
    raw: int, expected_active: bool, expected_occurred: bool
) -> None:
    status = ThrottledStatus.from_int(raw)
    assert status.any_active is expected_active
    assert status.any_occurred is expected_occurred


@pytest.mark.parametrize(
    ("bad_value", "pattern"),
    [
        ("", "Empty"),
        ("   ", "Empty"),
        ("not-hex", "invalid literal"),
        ("-1", "non-negative"),
    ],
)
def test_throttled_status_from_hex_invalid(bad_value: str, pattern: str) -> None:
    with pytest.raises(ValueError, match=pattern):
        ThrottledStatus.from_hex(bad_value)


def test_throttled_status_from_int_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ThrottledStatus.from_int(-1)


def test_get_throttled_status_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    throttled_file = tmp_path / "get_throttled"
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_LEGACY", str(throttled_file))

    assert get_throttled_status() is None

    throttled_file.write_text("80008\n", encoding="utf-8")
    status = get_throttled_status()
    assert status is not None
    assert status.soft_temp_limit is True
    assert status.soft_temp_limit_occurred is True
    assert status.under_voltage is False

    custom = tmp_path / "custom"
    custom.write_text("20002", encoding="utf-8")
    custom_status = get_throttled_status(custom)
    assert custom_status is not None
    assert custom_status.arm_frequency_capped is True
    assert custom_status.arm_frequency_capped_occurred is True

    legacy = UnderVoltageLegacy(custom)
    assert legacy.get_throttled_status() == custom_status


def test_get_throttled_status_propagates_non_missing_errors(tmp_path: Path) -> None:
    throttled_file = tmp_path / "get_throttled"
    throttled_file.write_text("not-hex\n", encoding="utf-8")
    with pytest.raises(RpiBadPowerError, match="Invalid throttled hex"):
        get_throttled_status(throttled_file)

    perm_err = PermissionError(errno.EACCES, "Permission denied", str(throttled_file))
    with (
        patch.object(Path, "open", side_effect=perm_err),
        pytest.raises(RpiBadPowerError) as exc_info,
    ):
        get_throttled_status(throttled_file)
    assert exc_info.value.errno == errno.EACCES
