# PilzBase — Raspberry Pi station software (v0.1)

Python 3.11+ autonomous logger for Raspberry Pi Zero 2 W. **Current version operates in simulated-sensor mode only.** No real GPIO/I²C/SPI hardware is controlled yet. Verify the final schematic, voltages, and PT100 channel count before hardware integration.

## Features

- Local SQLite database with WAL and synchronous FULL commits
- Configurable 15-second acquisition, default 56-day experiment
- Experiment start/stop/finish and automatic resume after service restart
- Failed/invalid sensor values recorded as null with error details
- Event log and paginated JSON/CSV exports
- Open REST API (no authentication; trusted LAN only)
- systemd service and automated pytest tests

## Quick start (Pi or development computer)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp config.example.yaml config.yaml
pilzbase
```

The default API binds only to localhost. Open `http://127.0.0.1:8000/docs` for interactive API documentation.

```bash
curl http://127.0.0.1:8000/api/v1/status
curl -X POST http://127.0.0.1:8000/api/v1/experiments/start
curl 'http://127.0.0.1:8000/api/v1/measurements?after_id=0&limit=1000'
curl -o data.csv 'http://127.0.0.1:8000/api/v1/measurements.csv?after_id=0&limit=5000'
pytest -q
```

For a trusted isolated LAN, set `api_host: 0.0.0.0` . **HTTP is not encrypted**; use TLS or VPN for communication beyond a trusted network. Do not expose the station directly to the public internet.

## One-command Raspberry Pi setup (recommended)

Run as your **normal user**, not with `sudo`. The installer calls sudo for required APT packages and enables systemd user-service boot startup:

```bash
curl -fsSL https://raw.githubusercontent.com/orka375/PilzBase/main/deploy/install-rpi.sh -o /tmp/pilzbase-install.sh
bash /tmp/pilzbase-install.sh
```

The script installs system Python dependencies via APT, clones or updates `~/PilzBase`, creates `config.yaml` and `hardware.yaml` without overwriting an existing database, creates `data/`, installs the systemd **user** service, enables lingering and starts PilzBase. The button and LED remain disabled in a fresh hardware config until the wiring is verified. For existing installations, review your database path and saved station settings after running it.

Check the service with `systemctl --user status pilzbase`, see logs with `journalctl --user -u pilzbase -f`, and open `http://<pi-ip>:8000/` on a trusted LAN.

## Raspberry Pi installation: system Python, single home folder (no venv)

On Raspberry Pi OS Lite, install dependencies using APT, not global pip (PEP 668):

```bash
sudo apt update
sudo apt install -y git python3-fastapi python3-uvicorn python3-yaml python3-gpiozero
cd ~
git clone https://github.com/orka375/PilzBase.git
cd ~/PilzBase
cp -n config.example.yaml config.yaml
cp -n hardware.example.yaml hardware.yaml
mkdir -p data ~/.config/systemd/user
```

In `~/PilzBase/config.yaml` set `database_path: ./data/measurements.sqlite3`, `hardware_config_path: hardware.yaml`, your station ID, and `api_host: 0.0.0.0` if you need LAN access. The GPIO assignments are in `~/PilzBase/hardware.yaml`; button/LED are disabled until wiring is verified.

```bash
/usr/bin/python3 -c 'import fastapi, uvicorn, yaml, gpiozero; print("Python packages OK")'
cp ~/PilzBase/deploy/pilzbase.service ~/.config/systemd/user/pilzbase.service
systemctl --user daemon-reload
systemctl --user enable --now pilzbase
sudo loginctl enable-linger "$USER"
systemctl --user status pilzbase
journalctl --user -u pilzbase -f
```

**Note:** Do not use sudo on the `systemctl --user` commands, and do not install this file to `/etc/systemd/system`. The service runs as your normal login user and starts at boot after `enable-linger`. Test foreground startup with `cd ~/PilzBase && PILZBASE_CONFIG="$HOME/PilzBase/config.yaml" /usr/bin/python3 -m pilzbase.app` before enabling the service if desired. If an old system-wide service exists, stop/disable it first: `sudo systemctl disable --now pilzbase` (only if installed).

When updating code: `cd ~/PilzBase && git pull && systemctl --user restart pilzbase`.

Keep the Pi on a trusted isolated LAN because the API has no authentication.

## Browser dashboard

Open `http://127.0.0.1:8000/` from the Pi (or `http://<pi-address>:8000/` when running on a trusted local network with `api_host: 0.0.0.0`). The page shows station status, latest values, temperature and CO₂ trends, start/stop controls, recent measurements and a full paginated CSV download. It polls the Pi every 15 seconds and works without external CDNs or internet access. Only one station is shown per dashboard; a future laptop dashboard will combine all five units. Avoid exposing the unencrypted HTTP API on untrusted networks.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Basic health check |
| GET | `/api/v1/status` | Station and experiment status |
| POST | `/api/v1/experiments/start` | Begin experiment |
| POST | `/api/v1/experiments/stop` | Stop experiment |
| GET | `/api/v1/measurements` | Paginated JSON readings |
| GET | `/api/v1/measurements.csv` | Paginated CSV export |
| GET | `/api/v1/events` | Event history |

## Important limitations

- **All readings are simulated**, with `simulated=1`. No production sensor drivers yet.
- Restart resumes the experiment but **does not backfill** readings missed during downtime.
- Timestamps depend on the Pi system clock; configure NTP or an RTC.
- Power loss can still damage SD storage; use reliable power and backups.
- CSV exports are paginated; use `after_id` to retrieve the full dataset.
- Physical sensor drivers, calibration, clock-health checks, end-to-end backup, and soak tests are required before a real 56-day experiment.

## Central five-station laptop dashboard

Run the optional laptop-side control center to monitor **UNIT-01 through UNIT-05** from one browser. Each Raspberry Pi keeps recording autonomously. See [laptop setup instructions](laptop/README.md). The control center defaults to `http://127.0.0.1:8080/` on the laptop and proxies each Pi's REST API. Its group start/stop commands are best-effort, **not synchronized or atomic**.

## Physical button, single-colour status LED, and hardware pin configuration

Copy `hardware.example.yaml` to `~/PilzBase/hardware.yaml` and configure the actual **BCM GPIO numbers** from your verified Fusion 360 schematic. Then set `hardware_config_path: hardware.yaml` in `~/PilzBase/config.yaml` and restart the user service.

```bash
cd ~/PilzBase
nano hardware.yaml
nano config.yaml
systemctl --user restart pilzbase
journalctl --user -u pilzbase -n 50 --no-pager
```

Set both `button.enabled` and `led.enabled` to `true` **only when correctly wired**. The proposed GPIO17 button and GPIO27 LED are *not verified against the current PCB*. Wire the normally-open button between GPIO and GND with a pull-up, and connect the single-colour LED with a suitable current-limiting resistor (for example, 1 kΩ) from GPIO to GND for active-high output.

Behavior:
- LED normally off; first short button press shows READY (slow blink), MEASURING (steady), STOPPED (double blink). LED times out after ten seconds.
- Second short press within the ten-second display window starts only when READY.
- In MEASURING, first short press shows status; holding for three seconds while status is visible stops the experiment.
- LED remains off for FINISHED, BOOT, SHUTDOWN. Long holds are ignored if status was not activated. Button held at boot is ignored until released.
- Sensors are still simulated. The `sensors` block documents **planned** SPI/I²C/ADC addresses; it does not yet activate sensor drivers.
- The latest design intentionally does not permit a physical button to start a new experiment after STOPPED/FINISHED; use the dashboard to create a new experiment.

The station service must have GPIO access. On Raspberry Pi OS this usually means ensuring your login user has GPIO access (check `groups`; if required, `sudo usermod -aG gpio "$USER"` followed by logout/login). Install `gpiozero` through APT and ensure a working Pi GPIO pin factory is available. Keep the GPIO enabled settings off for host-based tests.

## Confirmed GPIO routing and UPS HAT (C)

User-confirmed BCM GPIO assignments: **button GPIO16**, **single-colour status LED GPIO19**, **relay 2 GPIO20** (reserved/inactive, no actuation logic). The updated `hardware.example.yaml` contains these assignments. Existing `~/PilzBase/hardware.yaml` is **not overwritten**, so update it manually.

Hardware inventory: **two MAX31865** PT100 interfaces (provisional SPI0 chip selects GPIO8 and GPIO7); **DFRobot DFR0553** (ADS1115, I²C address 0x48 as example); **optoNCDT 1220** laser 4–20 mA via a shunt and ADS1115 analog input; and the **Waveshare UPS HAT (C)** with INA219 on I²C. The assigned ADC channel and shunt value are **provisional** and must match the finished wiring. Hardware drivers for MAX31865, DFR0553, and laser are **not yet implemented**; their data remain simulated.

UPS battery telemetry is implemented as an **opt-in** real INA219 I²C read. In `~/PilzBase/hardware.yaml` set:

```yaml
ups_hat_c:
  enabled: true
  bus: 1
  address: 0x43
```

Install I²C tools and driver: `sudo apt install -y python3-smbus i2c-tools`; enable I²C using `sudo raspi-config` and inspect `i2cdetect -y 1` to verify the INA219 address before enabling. Restart with `systemctl --user restart pilzbase`. The station dashboard and measurement JSON/CSV expose battery voltage, signed current, power and **voltage-based estimated percentage** during experiments. When the HAT is disabled or I²C fails the battery fields remain null. This measurement is not a calibrated fuel-gauge reading; percent is particularly unreliable while charging. All other sensor channels currently remain simulated.

## 2026-11 hardware inventory and activation

See `hardware.example.yaml` for full wiring map. **Confirmed BCM GPIO**: pushbutton 16, LED 19, relay 2 output 20. **Relay 2 HIGH turns laser OFF; LOW turns laser ON.** Relay driving is disabled by default: verify output polarity, optocoupler/relay supply voltage, and a hardware pull-up or equivalent failsafe. GPIO is not guaranteed to be HIGH before Raspberry Pi boot, so software-only laser interlock is not sufficient.

| Signal | Connection | Configuration |
|---|---|---|
| PT100 #1, PT100 #2 | MAX31865 via SPI0 CE0 GPIO8 / CE1 GPIO7 (proposed) | `pt100_1`, `pt100_2` |
| Laser optoNCDT 1220 | 4–20mA through verified shunt → DFR0553 ADS1115 A1 (proposed) | `opto_1220`; configure actual 4mA and 20mA *distance* values |
| Grove resistive soil moisture | Analog output → ADS1115 A0 (proposed) | `grove_soil`; moisture stored as raw volts pending calibration |
| DFRobot DFR0553 | I²C1 address 0x48 | `dfr0553` |
| DFRobot SEN0335 ENS160/BME280 | I²C1 0x53 ENS160 / 0x76 BME280 | `ens160`, `bme280` |
| Sensirion SCD41 | I²C1 0x62 | `scd41` |
| Waveshare UPS HAT (C) INA219 | I²C1 0x43 (check on hardware) | `ups_hat_c` |

The SPI chip-select assignments and ADC channels are **provisional**, not confirmed PCB connections. For I²C1 use GPIO2/SDA (physical pin 3) and GPIO3/SCL (physical pin 5). All devices must share GND, and the bus must use 3.3 V-safe logic levels.

The real readers are opt-in; example `enabled: false` prevents accidental use on unverified hardware. To switch from test data to the sensor drivers, set `sensor_mode: hardware` in `config.yaml`, then selectively enable tested devices in `hardware.yaml`. In hardware mode, **disabled or failed sensors are null, not simulated**, and the database row is marked `simulated=0`. The real-sensor drivers are an initial implementation, **not yet validated on the Raspberry Pi or calibrated**. Check I²C with `sudo i2cdetect -y 1` and SPI settings through `sudo raspi-config`.

Additional Python system packages for readers: `sudo apt install python3-spidev python3-smbus i2c-tools`; BME280 reader imports the `bme280` package and SCD41 reader imports `smbus2`, which must be available to your system Python before enabling those channels. Availability and package names vary by Raspberry Pi OS release. ENS160 eCO₂ is a **gas-sensor estimate** and must not be presented as equivalent to the directly measured CO₂ from SCD41.

Laser output should be enabled only when a test experiment is active. The GPIO software implements this best-effort rule, but it is **not a hard real-time interlock**; test the relay's electrical failsafe before using the laser, and do not enable it during initial installation.
