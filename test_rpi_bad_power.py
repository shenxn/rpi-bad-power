"""
Testcases for rpi bad power
"""

from unittest.mock import patch, MagicMock
from rpi_bad_power import new_under_voltage, SYSFILE_HWMON_DIR, SYSFILE_LEGACY, UnderVoltageLegacy, UnderVoltageNew


class MockFile:
    """Mocked file."""
    def __init__(self, content):
        self.read = MagicMock(return_value=f"{content}\n")
        self.close = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()
        return True


class MockSysFiles:
    """Mocking the system files."""
    def __init__(self, multi, new, legacy, noHwmon, isUnderVoltage):
        self.multi = multi  # multiple entries
        self.new = new  # single new entry
        self.legacy = legacy  # legacy entry
        self.noHwmon = noHwmon  # hwmon dir does not exist
        self.isUnderVoltage = isUnderVoltage

        self.listdir = MagicMock(side_effect=self._listdir)
        self.open = MagicMock(side_effect=self._open)
        self.isfile = MagicMock(side_effect=self._isfile)

        self.openedFiles = []
        self.files = {}
        if multi:
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/name"] = "cpu_thermal"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon2/name"] = "rpi_volt"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon2/in0_lcrit_alarm"] = "1" if isUnderVoltage else "0"
        elif new:
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/name"] = "rpi_volt"
            self.files[f"{SYSFILE_HWMON_DIR}/hwmon0/in0_lcrit_alarm"] = "1" if isUnderVoltage else "0"
        elif legacy:
            self.files[SYSFILE_LEGACY] = "50005" if self.isUnderVoltage else "0"

    def _listdir(self, path):
        assert path == SYSFILE_HWMON_DIR
        if self.noHwmon:
            raise FileNotFoundError()
        if self.multi:
            return ["hwmon0", "hwmon1", "hwmon2"]
        elif self.new:
            return ["hwmon0"]
        else:
            return []

    def _open(self, path):
        try:
            file = MockFile(self.files[path])
        except KeyError:
            raise FileNotFoundError()
        self.openedFiles.append(file)
        return file

    def _isfile(self, path):
        return path in self.files

    def assertAllFilesClosed(self):
        for file in self.openedFiles:
            file.close.assert_called_once()


class PatchSysFiles:
    """Patch the system files."""
    def __init__(self, multi=False, new=False, legacy=False, noHwmn=False, isUnderVoltage=False):
        self.mock = MockSysFiles(multi, new, legacy, noHwmn, isUnderVoltage)
        self.listdir_patch = None
        self.open_patch = None
        self.isfile_patch = None
    
    def __enter__(self):
        self.listdir_patch = patch("rpi_bad_power.os.listdir", self.mock.listdir)
        self.open_patch = patch("rpi_bad_power.open", self.mock.open)
        self.isfile_patch = patch("rpi_bad_power.os.path.isfile", self.mock.isfile)
        self.listdir_patch.__enter__()
        self.open_patch.__enter__()
        self.isfile_patch.__enter__()
        return self.mock
    
    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is not None:
            return False
        self.listdir_patch.__exit__(exc_type, exc_value, tb)
        self.open_patch.__exit__(exc_type, exc_value, tb)
        self.isfile_patch.__exit__(exc_type, exc_value, tb)
        self.mock.assertAllFilesClosed()
        return True


def test_non_rpi():
    """Test running on non raspberry pi environment."""
    with PatchSysFiles() as mockSysFiles:
        assert new_under_voltage() is None
    mockSysFiles.listdir.assert_called_once_with(SYSFILE_HWMON_DIR)


def test_non_rpi():
    """Test running on a system without hwmon directory."""
    with PatchSysFiles(noHwmn=True):
        assert new_under_voltage() is None


def test_multi():
    """Test running on a rpi kernel with multiple hwmon entries."""
    with PatchSysFiles(True):
        underVoltage = new_under_voltage()
        assert isinstance(underVoltage, UnderVoltageNew)
        assert underVoltage.get() is False
    
    with PatchSysFiles(True, isUnderVoltage=True):
        assert new_under_voltage().get() is True


def test_new():
    """Test running on a rpi kernel with one new hwmon entry."""
    with PatchSysFiles(new=True):
        underVoltage = new_under_voltage()
        assert isinstance(underVoltage, UnderVoltageNew)
        assert underVoltage.get() is False

    with PatchSysFiles(new=True, isUnderVoltage=True):
        assert new_under_voltage().get() is True


def test_legacy():
    """Test running on a legacy rpi kernel."""
    with PatchSysFiles(legacy=True) as mockSysFiles:
        underVoltage = new_under_voltage()
        assert isinstance(underVoltage, UnderVoltageLegacy)
        assert underVoltage.get() is False
    mockSysFiles.isfile.assert_called_once_with(SYSFILE_LEGACY)

    with PatchSysFiles(legacy=True, isUnderVoltage=True):
        assert new_under_voltage().get() is True