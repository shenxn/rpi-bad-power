"""
A sensor platform which detects underruns and capped status from the official Raspberry Pi Kernel.
Minimal Kernel needed is 4.14+
"""
import logging
import os

_LOGGER = logging.getLogger(__name__)

HWMON_NAME = "rpi_volt"

SYSFILE_HWMON_DIR = "/sys/class/hwmon"
SYSFILE_HWMON_FILE = "in0_lcrit_alarm"
SYSFILE_LEGACY = "/sys/devices/platform/soc/soc:firmware/get_throttled"

UNDERVOLTAGE_STICKY_BIT = 1 << 16

DESCRIPTION_NORMALIZED = "Voltage normalized. Everything is working as intended."
DESCRIPTION_UNDER_VOLTAGE = "Under-voltage was detected. Consider getting a uninterruptible power supply for your Raspberry Pi."

def get_rpi_volt_hwmon():
    """Find rpi_volt hwmon device"""
    for hwmon in os.listdir(SYSFILE_HWMON_DIR):
        name_file = os.path.join(SYSFILE_HWMON_DIR, hwmon, "name")
        if os.path.isfile(name_file):
            hwmon_name = open(name_file).read().strip()
            if hwmon_name == HWMON_NAME:
                return os.path.join(SYSFILE_HWMON_DIR, hwmon)
    return None

def new_under_voltage():
    """Create new UnderVoltage object."""
    hwmon = get_rpi_volt_hwmon()
    if hwmon:
        return UnderVoltage(hwmon)
    if os.path.isfile(SYSFILE_LEGACY):  # support older kernel
        return UnderVoltageLegacy()
    return None


class UnderVoltage:
    """Read under voltage status."""

    def __init__(self, hwmon):
        self._hwmon = hwmon

    def get(self):
        """Get under voltage status."""
        # Use new hwmon entry
        bit = open(os.path.join(self._hwmon, SYSFILE_HWMON_FILE)).read()[:-1]
        _LOGGER.debug("Get under voltage status: %s", bit)
        return bit == "1"


class UnderVoltageLegacy(UnderVoltage):
    """Read under voltage status from legacy entry."""

    def get(self):
        """Get under voltage status."""
        # Using legacy get_throttled entry
        throttled = open(SYSFILE_LEGACY).read()[:-1]
        _LOGGER.debug("Get throttled value: %s", throttled)
        return (
            int(throttled, base=16) & UNDERVOLTAGE_STICKY_BIT
            == UNDERVOLTAGE_STICKY_BIT
        )
