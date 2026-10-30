# PilzBase — Raspberry Pi station software (v0.1)

Python 3.11+ autonomous logger for Raspberry Pi Zero 2 W. **Current version operates in simulated-sensor mode only.** No real GPIO/I²C/SPI hardware is controlled yet. Verify the final schematic, voltages, and PT100 channel count before hardware integration.

## Features

- Local SQLite database with WAL and synchronous FULL commits
- Configurable 15-second acquisition, default 56-day experiment
- Experiment start/stop/finish and automatic resume after service restart
- Failed/invalid sensor values recorded as null with error details
- Event log and paginated JSON/CSV exports
- Bearer-token-protected REST API for future browser dashboard
- systemd service and automated pytest tests

## Quick start (Pi or development computer)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp config.example.yaml config.yaml
export PILZBASE_API_TOKEN='replace-with-long-random-secret'
pilzbase
```

The default API binds only to localhost. Open `http://127.0.0.1:8000/docs` for interactive API documentation.

```bash
curl -H "Authorization: Bearer $PILZBASE_API_TOKEN" http://127.0.0.1:8000/api/v1/status
curl -X POST -H "Authorization: Bearer $PILZBASE_API_TOKEN" http://127.0.0.1:8000/api/v1/experiments/start
curl -H "Authorization: Bearer $PILZBASE_API_TOKEN" 'http://127.0.0.1:8000/api/v1/measurements?after_id=0&limit=1000'
curl -H "Authorization: Bearer $PILZBASE_API_TOKEN" -o data.csv 'http://127.0.0.1:8000/api/v1/measurements.csv?after_id=0&limit=5000'
pytest -q
```

For a trusted isolated LAN, set `api_host: 0.0.0.0` and supply a strong `PILZBASE_API_TOKEN`. **HTTP is not encrypted**; use TLS or VPN for communication beyond a trusted network. Do not expose the station directly to the public internet.

## Raspberry Pi service

Use Raspberry Pi OS Lite with Python 3.11+. Clone into `/opt/pilzbase`; create a restricted `pilzbase` system user and virtual environment. Copy `config.example.yaml` to `/etc/pilzbase/config.yaml`, set `database_path: /var/lib/pilzbase/measurements.sqlite3`, and place `PILZBASE_API_TOKEN=...` in `/etc/pilzbase/secrets.env` (permissions 0600). Ensure the service user can read both files and write to the database directory. Install `deploy/pilzbase.service` in `/etc/systemd/system/`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pilzbase
journalctl -u pilzbase -f
```

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
