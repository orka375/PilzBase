# PilzBase five-station control center

Runs on the **laptop**, separate from the five independent Raspberry Pi loggers.

## Install

Requires Python 3.11+, on the laptop:

```bash
git clone https://github.com/orka375/PilzBase.git
cd PilzBase
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\\Scripts\\activate
pip install -e '.[test]'
cp laptop/config.example.yaml laptop/config.yaml
```

Configure station addresses from the browser; the YAML starts with an empty station list. Use fixed addresses/DHCP reservations for reliable discovery. Each station must run the PilzBase logger with `api_host: 0.0.0.0`. Network traffic is plain HTTP, so use **only a trusted isolated laboratory network or a secure tunnel/VPN**, never an untrusted or public network.

Start the laptop dashboard:

```bash
python -m laptop.server --config laptop/config.yaml
```

Open **http://127.0.0.1:8080/** in the laptop's browser.

The laptop server listens on loopback only and does not require tokens or passwords. **Do not expose the laptop dashboard server to a network** without adding its own authentication and TLS.

## Features

- Single view of UNIT-01 through UNIT-05
- Last readings and station state, including offline indicators
- Temperature/CO₂/humidity/laser/soil comparison plot
- Start/stop per unit, or best-effort parallel start/stop for all eligible units
- Export each station's stored data to a CSV file
- Automatic 15-second refresh; the stations continue independently while the laptop is off

## Limitations

- Group commands are **not atomic** and are not synchronized to the same timestamp. Each Pi starts independently; check every station's returned state.
- CSV export uses the laptop browser memory and is intended for moderate experiment sizes. Exports are capped at one million measurements per station; production-grade downloads should use streaming to disk.
- Comparison plots show the last 240 records only.
- Current station firmware reports **simulated** sensor measurements. Validate real sensor drivers, synchronization, backup, and recovery before using data scientifically.
- The central dashboard is not a second measurement logger; data remain on each Pi.

## Add a Raspberry Pi from the browser

Open the laptop dashboard, choose a station ID (UNIT-01 to UNIT-05), type the Pi IP address or hostname (for example `192.168.1.101` or `unit-01.local`, optionally `:8000`), and click **Connect & save**. The laptop tests `/api/v1/status`, verifies that the returned station ID matches, and saves the address. Connected stations survive restarts. You can update the address by connecting again with the same unit ID or remove a station from its card. Removed stations continue logging independently.

No credentials are stored. Only the locally hosted dashboard can configure stations; never expose port 8080 to untrusted users. Connection is limited to privately addressed targets. For best security and reliability, use a trusted dedicated laboratory LAN and DHCP reservations. The station's own API must listen on its LAN interface.

**Security:** Authentication has intentionally been removed. Anyone able to access the Raspberry Pi API can start or stop experiments or read measurements. Use only a trusted isolated lab network; do not expose the stations or dashboard to the internet.
