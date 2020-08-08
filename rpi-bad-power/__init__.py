"""
A sensor platform which detects underruns and capped status from the official Raspberry Pi Kernel.
Minimal Kernel needed is 4.14+
"""
import logging
import os

_LOGGER = logging.getLogger(__name__)

SYSFILE = "/sys/class/hwmon/hwmon0/in0_lcrit_alarm"
SYSFILE_LEGACY = "/sys/devices/platform/soc/soc:firmware/get_throttled"

UNDERVOLTAGE_STICKY_BIT = 1 << 16

DESCRIPTION_NORMALIZED = "Voltage normalized. Everything is working as intended."
DESCRIPTION_UNDER_VOLTAGE = "Under-voltage was detected. Consider getting a uninterruptible power supply for your Raspberry Pi."


def new_under_voltage():
    """Create new UnderVoltage object."""
    if os.path.isfile(SYSFILE):
        return UnderVoltage()
    if os.path.isfile(SYSFILE_LEGACY):  # support older kernel
        return UnderVoltageLegacy()
    return None


class UnderVoltage:
    """Read under voltage status."""

    def get(self):
        """Get under voltage status."""
        # Use new hwmon entry
        bit = open(SYSFILE).read()[:-1]
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
