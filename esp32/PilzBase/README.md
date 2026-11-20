# PilzBase ESP32-S3 station (experimental)

The ESP32 collects sensors, records measurements to **internal flash**, and exposes
a small JSON API to the laptop. **There is no ESP32 webpage, SD card, CSV export,
or permanent experiment database.** The laptop imports and retains data.

## Setup

1. Confirm the board is an ESP32-S3 N8R8 (8 MB flash, 8 MB PSRAM).
2. Copy `include/local_settings.example.h` to `include/local_settings.h`.
3. Set Wi-Fi, a unique `UNIT-01`–`UNIT-05`, and a random API token of at least 12 characters.
4. `pio run -t upload` and `pio device monitor -b 115200` from this directory.
5. Configure the laptop `laptop/config.yaml` station URL and the **same token**.
6. Run the laptop dashboard and use **Sync now** or wait for the one-minute poll.

The laptop connects to port 8000 over a trusted lab network. The API token is
required for starting/stopping experiments and acknowledging measurements. HTTP
is **not encrypted**: do not expose port 8000 to the public Internet.

## Flash recording and transfer

`partitions.csv` allocates ~4.9 MB of flash to a LittleFS journal. During a
measurement experiment, a JSON record is appended every 15 seconds; these
records remain until the laptop commits them to SQLite and acknowledges their IDs.
An acknowledgment is idempotent. Records are never overwritten on a full journal.

**Limitations:** Actual offline retention depends on JSON record size, flash
filesystem overhead, and remaining free space. This is a first implementation,
not a validated scientific data logger. Power-loss recovery, flash exhaustion,
and flash endurance require hardware soak testing. The current journal's
compaction copies remaining records, which may temporarily require additional
free flash. A failed ACK is retried by the laptop.

The device does not manufacture timestamps without NTP; an unsynchronized
sample has `acquired_at: null` and a clock error. Starting an experiment requires
valid NTP time. Active experiments persist through reset.

## Hardware precautions

The original `src/main.cpp` sensor diagnostics are still available on serial.
Two MAX31865 channels use PT100 nominal 100 Ω, reference 430 Ω and **3-wire** mode.
Physical jumpers, probe wiring, and modified reference resistors must be tested.

GPIO8/9 are I²C, GPIO38/39/40 SPI SCK/MOSI/MISO, GPIO10/14 chip selects,
GPIO5 soil analog input. Button, status LED and relay GPIOs are deliberately
unassigned (`-1` in `pins.h`), since setup notes conflict with test code. The
relay and laser are **never energized by this firmware**.

The Waveshare UPS HAT (C) INA219 is probed at I²C `0x43`. Its bus voltage is
*not* a direct measurement of cell voltage. Implausible ~0.46 V readings are
reported as errors; battery percentage is not fabricated. Hardware power and
I²C voltage compatibility must be confirmed before connecting the HAT to ESP32.

The M5Stack AIN 4–20 mA input is uncalibrated. Its raw current is reported but
laser distance conversion is disabled pending a verified current/distance range.
