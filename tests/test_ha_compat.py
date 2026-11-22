"""Contract tests mirroring Home Assistant Core's ``rpi_power`` integration."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

import rpi_bad_power
from rpi_bad_power import (
    SYSFILE_HWMON_FILE,
    RpiBadPowerError,
    UnderVoltage,
    new_under_voltage,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_ha_config_flow_and_setup_executor_contract_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirror HA config_flow._async_supported and __init__.async_setup_entry."""
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_HWMON_DIR", str(tmp_path / "no_hwmon"))
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_LEGACY", str(tmp_path / "no_legacy"))

    # HA calls new_under_voltage inside executor thread pool; must return None.
    client = await asyncio.to_thread(new_under_voltage)
    assert client is None


async def test_ha_executor_polling_and_logger_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirror HA binary_sensor polling and manifest.json loggers=['rpi_bad_power']."""
    hwmon_dir = tmp_path / "hwmon"
    entry = hwmon_dir / "hwmon0"
    entry.mkdir(parents=True)
    (entry / "name").write_text("rpi_volt\n", encoding="utf-8")
    alarm_file = entry / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")

    monkeypatch.setattr(rpi_bad_power, "SYSFILE_HWMON_DIR", str(hwmon_dir))
    monkeypatch.setattr(rpi_bad_power, "SYSFILE_LEGACY", str(tmp_path / "no_legacy"))

    with caplog.at_level(logging.DEBUG, logger="rpi_bad_power"):
        client = await asyncio.to_thread(new_under_voltage)
        assert isinstance(client, UnderVoltage)

        normal = await asyncio.to_thread(client.get)
        assert normal is False

        alarm_file.write_text("1\n", encoding="utf-8")
        under_voltage = await asyncio.to_thread(client.get)
        assert under_voltage is True

    assert any(record.name == "rpi_bad_power" for record in caplog.records)


async def test_ha_errors_remain_oserror_subclasses(tmp_path: Path) -> None:
    """Ensure HA handlers catching OSError still catch library I/O/parse failures."""
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    alarm_file = hwmon / SYSFILE_HWMON_FILE
    alarm_file.write_text("corrupt\n", encoding="utf-8")

    client = rpi_bad_power.UnderVoltageNew(hwmon)
    with pytest.raises(OSError, match="Unexpected under-voltage value") as exc_info:
        await asyncio.to_thread(client.get)
    assert isinstance(exc_info.value, RpiBadPowerError)
