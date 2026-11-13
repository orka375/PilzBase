"""Regression tests for physical sensor reads without real hardware."""
import sys
import types

from pilzbase.physical_sensors import tcs34725_read, ens160_read, PhysicalSensors


def test_tcs34725_uses_verified_driver(monkeypatch):
    monkeypatch.setitem(sys.modules, "board", types.SimpleNamespace(I2C=lambda: object()))
    class TCS:
        def __init__(self, bus):
            self.color_raw = (5, 5, 4, 12)  # red, green, blue, clear
    monkeypatch.setitem(sys.modules, "adafruit_tcs34725",
                        types.SimpleNamespace(TCS34725=TCS))
    result = tcs34725_read({"bus": 1, "address": 0x29})
    assert result == {"color_clear_raw": 12, "color_red_raw": 5,
                      "color_green_raw": 5, "color_blue_raw": 4}


def test_ens160_zero_values_are_not_valid(monkeypatch):
    class Bus:
        def __init__(self, bus): pass
        def close(self): pass
        def write_byte_data(self, *args): pass
        def read_byte_data(self, addr, reg):
            return {0x20: 0, 0x21: 0}[reg]
        def read_word_data(self, addr, reg):
            return 0
    monkeypatch.setitem(sys.modules, "smbus", types.SimpleNamespace(SMBus=Bus))
    import pytest
    with pytest.raises(OSError, match="implausible"):
        ens160_read({"bus": 1, "address": 0x53})


def test_sensor_errors_retained(monkeypatch):
    from pilzbase import physical_sensors
    def fail(cfg):
        raise OSError(5, "I/O failure")
    monkeypatch.setattr(physical_sensors, "tcs34725_read", fail)
    sensors = PhysicalSensors({"sensors": {"cjmcu_34725": {"enabled": True}}})
    assert sensors.read() == {}
    assert "OSError" in sensors.errors["cjmcu_34725"]
