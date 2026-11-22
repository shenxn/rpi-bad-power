# Changelog

All notable changes to `rpi-bad-power` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-21

### Added
- **Event-driven kernel push watcher (`rpi_bad_power.watcher.AsyncUnderVoltageWatcher`)**:
  - `UnderVoltage.supports_push` property (`True` for `UnderVoltageNew` on Linux with `POLLPRI`, `False` for `UnderVoltageLegacy`).
  - `UnderVoltageNew.async_watch(emit_initial=True)` async iterator & context manager monitoring `/sys/class/hwmon/hwmon*/in0_lcrit_alarm` via Linux `select.poll()` (`POLLPRI | POLLERR`) with a self-pipe wakeup channel for instant cancellation and zero file descriptor leaks.
- **Full `get_throttled` bitmask telemetry (`rpi_bad_power.throttled`)**:
  - `ThrottledFlag`, `ThrottledStatus` (decoding active bits 0–3 and sticky bits 16–19), `get_throttled_status()`, and `UnderVoltageLegacy.get_throttled_status()`.
- **Structured exception (`RpiBadPowerError`)**:
  - Subclasses `OSError` (preserving `errno` and `filename` on I/O errors) for full backwards compatibility with `except OSError:` handlers.
- **PEP 561 type marker (`rpi_bad_power/py.typed`)** and full strict typing (`mypy --strict`).
- **Path introspection properties**: `UnderVoltageNew.alarm_path` and `UnderVoltageLegacy.throttled_path`.

### Fixed
- Replaced fragile `file.read()[:-1]` slicing with `.strip()` so sysfs files lacking a trailing newline or using `\r\n` are parsed reliably.
- Hardened `get_rpi_volt_hwmon()` and `new_under_voltage()` to handle `OSError` / `PermissionError` / corrupt UTF-8 entries across sibling `hwmon*` devices and verify `in0_lcrit_alarm` / `get_throttled` readability before returning an instance.
- Rejected empty, malformed, or negative values in both `UnderVoltageNew.get()` and `UnderVoltageLegacy.get()` with `RpiBadPowerError`.
- Made `UnderVoltage` a proper `abc.ABC` with `@abstractmethod` on `get()`.
- Precomputed `self._alarm_path` in `UnderVoltageNew.__init__` and explicitly specified `encoding="utf-8"` on all file opens.

### Changed
- Migrated packaging from legacy `setup.py` / `setup.cfg` to PEP 621 `pyproject.toml` with `hatchling` and `uv` (`requires-python = ">=3.12"`).
- Replaced legacy custom `open()` mocks in `test_rpi_bad_power.py` with `pytest` `tmp_path` + `monkeypatch` test suites under `tests/` achieving >97% branch coverage.
- Modernized GitHub Actions (`ci.yml`, `publish.yml` via PyPI Trusted Publishing with attestations, and `codeql-analysis.yml` v4).

## [0.1.0] - 2020-11-16

- Initial release supporting modern `raspberrypi-hwmon` (`in0_lcrit_alarm`) and legacy `get_throttled` sysfs under-voltage detection.
