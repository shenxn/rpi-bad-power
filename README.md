# rpi-bad-power
A Python library to detect bad power supply on Raspberry Pi. This library is mainly built for the [Raspberry Pi Power Supply Checker](https://www.home-assistant.io/integrations/rpi_power/) integration of [HomeAssistant](https://github.com/home-assistant/core). It should also work for other purpose.

## Compatibility

This library only works on kernel 4.14+. It supports getting the under voltage bit from different entries.

Related PRs:
- [raspberrypi/linux#2397](https://github.com/raspberrypi/linux/pull/2397): `/sys/devices/platform/soc/soc:firmware/get_trottled`
- [raspberrypi/linux#2706](https://github.com/raspberrypi/linux/pull/2706): `/sys/class/hwmon/hwmon0/in0_lcrit_alarm`

## Usage

Here is an example on how to use this library.

```python
from rpi_bad_power import new_under_voltage

under_voltage = new_under_voltage()
if under_voltage is None:
    print("System not supported.")
elif under_voltage.get():
    print("Under voltage detected.")
else:
    print("Voltage is normal.")
```

## Credits

Some of the code are based on [custom-components/sensor.rpi_power](https://github.com/custom-components/sensor.rpi_power) maintained by [@swetoast](https://github.com/swetoast).
