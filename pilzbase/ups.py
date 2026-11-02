"""Waveshare UPS HAT (C) battery telemetry via INA219 I2C.

Bus voltage is an approximation of battery voltage in this board's monitor
path. Battery percent is *voltage-derived*, not true fuel-gauge capacity.
Requires python3-smbus and enabled I2C on the Raspberry Pi.
"""
from contextlib import closing

def _swap(value):
    return ((value & 255) << 8) | (value >> 8)

def _signed(value):
    return value - 65536 if value & 0x8000 else value

class UPSHatC:
    def __init__(self, bus=1, address=0x43):
        self.bus = bus
        self.address = address

    def read(self):
        from smbus import SMBus
        with closing(SMBus(self.bus)) as bus:
            def write(reg, value):
                bus.write_word_data(self.address, reg, _swap(value))
            def read(reg):
                return _swap(bus.read_word_data(self.address, reg))
            # Waveshare INA219 reference calibration: 0.1 mA/bit
            write(0x05, 4096)
            write(0x00, 0x3fff)  # 32 V range, gain 8, 12-bit sampling, continuous
            bus_raw = read(0x02)
            shunt_raw = _signed(read(0x01))
            current_raw = _signed(read(0x04))
            # INA219 bus-voltage LSB: 4 mV; shunt LSB: 10 uV
            voltage = (bus_raw >> 3) * 0.004 + shunt_raw * 0.00001
            current = current_raw * 0.1
            power = voltage * current / 1000
            # Approximate voltage-based state of charge; Waveshare warns the
            # reading is inaccurate especially when charging.
            pct = max(0.0, min(100.0, (voltage - 3.0) / 1.2 * 100.0))
            return {
                "battery_voltage_v": round(voltage, 3),
                "battery_current_ma": round(current, 2),
                "battery_power_w": round(power, 3),
                "battery_capacity_est_pct": round(pct, 1),
            }
