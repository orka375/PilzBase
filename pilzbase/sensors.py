"""Sensor interface and deterministic, explicitly simulated measurements.

No physical sensor drivers are implemented until pin assignments and voltage levels
have been validated against the actual schematic and manufacturers' datasheets.
"""
from typing import Protocol
import math
import time

FIELDS = (
    "pt100_1_c", "pt100_2_c", "pt100_3_c", "co2_ppm",
    "air_temperature_c", "humidity_rh_pct", "soil_voltage_v",
    "laser_distance_mm", "light_raw", "uv_raw",
)

class SensorSource(Protocol):
    def read(self) -> dict[str, float | None]: ...

class SimulatedSensors:
    def read(self) -> dict[str, float | None]:
        phase = time.monotonic() / 300
        drift = math.sin(phase)
        return {
            "pt100_1_c": round(24.0 + 0.3 * drift, 3),
            "pt100_2_c": round(24.2 + 0.3 * drift, 3),
            "pt100_3_c": round(23.9 + 0.3 * drift, 3),
            "co2_ppm": round(1200 + 80 * drift, 1),
            "air_temperature_c": round(24.1 + 0.3 * drift, 3),
            "humidity_rh_pct": round(92 + drift, 2),
            "soil_voltage_v": round(2.1 + 0.04 * drift, 4),
            "laser_distance_mm": round(155 + 2 * drift, 3),
            "light_raw": round(120 + 5 * drift, 3),
            "uv_raw": round(10 + drift, 3),
        }
