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

## Raspberry Pi service

Use Raspberry Pi OS Lite with Python 3.11+. Clone into `/opt/pilzbase`; create a restricted `pilzbase` system user and virtual environment. Copy `config.example.yaml` to `/etc/pilzbase/config.yaml`, set `database_path: /var/lib/pilzbase/measurements.sqlite3`. Ensure the service user can read the config file and write to the database directory. Install `deploy/pilzbase.service` in `/etc/systemd/system/`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pilzbase
journalctl -u pilzbase -f
```

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

Copy `hardware.example.yaml` to `/etc/pilzbase/hardware.yaml` and configure the actual **BCM GPIO numbers** from your verified Fusion 360 schematic. Then set `hardware_config_path: /etc/pilzbase/hardware.yaml` in `/etc/pilzbase/config.yaml` and restart the service.

```bash
sudo cp /opt/pilzbase/hardware.example.yaml /etc/pilzbase/hardware.yaml
sudo nano /etc/pilzbase/hardware.yaml
sudo nano /etc/pilzbase/config.yaml
sudo systemctl restart pilzbase
sudo journalctl -u pilzbase -n 50 --no-pager
```

Set both `button.enabled` and `led.enabled` to `true` **only when correctly wired**. The proposed GPIO17 button and GPIO27 LED are *not verified against the current PCB*. Wire the normally-open button between GPIO and GND with a pull-up, and connect the single-colour LED with a suitable current-limiting resistor (for example, 1 kΩ) from GPIO to GND for active-high output.

Behavior:
- LED normally off; first short button press shows READY (slow blink), MEASURING (steady), STOPPED (double blink). LED times out after ten seconds.
- Second short press within the ten-second display window starts only when READY.
- In MEASURING, first short press shows status; holding for three seconds while status is visible stops the experiment.
- LED remains off for FINISHED, BOOT, SHUTDOWN. Long holds are ignored if status was not activated. Button held at boot is ignored until released.
- Sensors are still simulated. The `sensors` block documents **planned** SPI/I²C/ADC addresses; it does not yet activate sensor drivers.
- The latest design intentionally does not permit a physical button to start a new experiment after STOPPED/FINISHED; use the dashboard to create a new experiment.

The station service must have GPIO access. On Raspberry Pi OS this usually means adding the `pilzbase` user to the `gpio` group (`sudo usermod -aG gpio pilzbase`), then restarting the service. Install `gpiozero` (provided by the project dependencies) and ensure a working Pi GPIO pin factory is available. Keep the GPIO enabled settings off for host-based tests.
