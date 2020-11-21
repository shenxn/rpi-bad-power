"""Testcases for rpi bad power."""

from typing import Any, List, Mapping, Text
from unittest.mock import MagicMock, patch

from rpi_bad_power import (
    SYSFILE_HWMON_DIR,
    SYSFILE_LEGACY,
    UnderVoltageLegacy,
    UnderVoltageNew,
    new_under_voltage,
)


class MockFile:
    """Mocked file."""

    def __init__(self, content: Text):
        """Initialize the mocked file."""
        self.read = MagicMock(return_value=f"{content}\n")
        self.close = MagicMock()


class PatchFile:
    """Patch a file."""

    def __init__(self, content: Text):
        """Initialize the mocked file."""
        self.file = MockFile(content)

    def __enter__(self) -> MockFile:
        """Enter a with statement doing nothing."""
        return self.file

    def __exit__(self, *exc_info: Any) -> bool:
        """Exit a with statement by closing the file."""
        self.file.close()
        return True


class MockSysFiles:
    """Mocking the system files."""

    def __init__(
        self,
        multi: bool,
        new: bool,
        legacy: bool,
        no_hwmon: bool,
        is_under_voltage: bool,
    ):
        """Initialize the mocked system files."""
        self.multi = multi  # multiple entries
        self.new = new  # single new entry
        self.legacy = legacy  # legacy entry
        self.no_hwmon = no_hwmon  # hwmon dir does not exist
        self.is_under_voltage = is_under_voltage

        self.listdir = MagicMock(side_effect=self._listdir)
        self.open = MagicMock(side_effect=self._open)
        self.isfile = MagicMock(side_effect=self._isfile)

        self.opened_files: List[MockFile] = []
        self.files: Mapping[Text, Text] = {}
        if multi:
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/name"] = "cpu_thermal"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon2/name"] = "rpi_volt"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon2/in0_lcrit_alarm"] = (
                "1" if is_under_voltage else "0"
            )
        elif new:
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/name"] = "rpi_volt"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/in0_lcrit_alarm"] = (
                "1" if is_under_voltage else "0"
            )
        elif legacy:
            self.files[SYSFILE_LEGACY] = "50005" if self.is_under_voltage else "0"

    def _listdir(self, path: Text) -> List[Text]:
        assert path == SYSFILE_HWMON_DIR
        if self.no_hwmon:
            raise FileNotFoundError()
        if self.multi:
            return ["hwmon0", "hwmon1", "hwmon2"]
        if self.new:
            return ["hwmon0"]
        return []

    def _open(self, path: Text) -> PatchFile:
        try:
            patch_file = PatchFile(self.files[path])
        except KeyError:
            raise FileNotFoundError()  # pylint: disable=raise-missing-from
        self.opened_files.append(patch_file.file)
        return patch_file

    def _isfile(self, path: Text) -> bool:
        return path in self.files

    def assert_all_files_closed(self) -> None:
        """Assert all opened files are closed."""
        for file in self.opened_files:
            file.close.assert_called_once()


class PatchSysFiles:
    """Patch the system files."""

    def __init__(
        self,
        multi: bool = False,
        new: bool = False,
        legacy: bool = False,
        no_hwmon: bool = False,
        is_under_voltage: bool = False,
    ):
        """Initialize the patch helper class."""
        self.mock = MockSysFiles(multi, new, legacy, no_hwmon, is_under_voltage)
        self.listdir_patch = patch("rpi_bad_power.os.listdir", self.mock.listdir)
        self.open_patch = patch("rpi_bad_power.open", self.mock.open)
        self.isfile_patch = patch("rpi_bad_power.os.path.isfile", self.mock.isfile)

    def __enter__(self) -> MockSysFiles:
        """Enter the with statement by patching functions."""
        self.listdir_patch.__enter__()
        self.open_patch.__enter__()
        self.isfile_patch.__enter__()
        return self.mock

    def __exit__(self, *exc_info: Any) -> bool:
        """Exit the with statement by un-patching functions."""
        if exc_info[0] is not None:
            return False
        self.listdir_patch.__exit__(*exc_info)
        self.open_patch.__exit__(*exc_info)
        self.isfile_patch.__exit__(*exc_info)
        self.mock.assert_all_files_closed()
        return True


def test_non_rpi() -> None:
    """Test running on non raspberry pi environment."""
    with PatchSysFiles() as mock_sys_files:
        assert new_under_voltage() is None
    mock_sys_files.listdir.assert_called_once_with(SYSFILE_HWMON_DIR)


def test_no_hwmon() -> None:
    """Test running on a system without hwmon directory."""
    with PatchSysFiles(no_hwmon=True):
        assert new_under_voltage() is None


def test_multi() -> None:
    """Test running on a rpi kernel with multiple hwmon entries."""
    with PatchSysFiles(True):
        under_voltage = new_under_voltage()
        assert isinstance(under_voltage, UnderVoltageNew)
        assert under_voltage.get() is False

    with PatchSysFiles(True, is_under_voltage=True):
        assert new_under_voltage().get() is True  # type: ignore


def test_new() -> None:
    """Test running on a rpi kernel with one new hwmon entry."""
    with PatchSysFiles(new=True):
        under_voltage = new_under_voltage()
        assert isinstance(under_voltage, UnderVoltageNew)
        assert under_voltage.get() is False

    with PatchSysFiles(new=True, is_under_voltage=True):
        assert new_under_voltage().get() is True  # type: ignore


def test_legacy() -> None:
    """Test running on a legacy rpi kernel."""
    with PatchSysFiles(legacy=True) as mock_sys_files:
        under_voltage = new_under_voltage()
        assert isinstance(under_voltage, UnderVoltageLegacy)
        assert under_voltage.get() is False
    mock_sys_files.isfile.assert_called_once_with(SYSFILE_LEGACY)

    with PatchSysFiles(legacy=True, is_under_voltage=True):
        assert new_under_voltage().get() is True  # type: ignore
