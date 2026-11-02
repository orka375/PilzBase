#!/usr/bin/env bash
# PilzBase Raspberry Pi OS installer; run as your regular login user (NOT sudo).
set -Eeuo pipefail

REPO_URL="https://github.com/orka375/PilzBase.git"
PROJECT="$HOME/PilzBase"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_NAME="pilzbase.service"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -eq 0 ]]; then
  die "Run as the normal Raspberry Pi user, not with sudo. The installer uses sudo only for APT and loginctl."
fi
command -v sudo >/dev/null || die "sudo is required."
command -v systemctl >/dev/null || die "systemd is required."
[[ -n "${HOME:-}" && -d "$HOME" ]] || die "A real user home directory is required."

say "Installing system Python packages (no virtual environment)"
sudo apt-get update
sudo apt-get install -y git curl python3 python3-fastapi python3-uvicorn python3-yaml python3-gpiozero

say "Checking system Python dependencies"
if ! /usr/bin/python3 -c 'import fastapi, uvicorn, yaml, gpiozero'; then
  die "A required Python package cannot be imported by /usr/bin/python3."
fi

say "Setting up source in $PROJECT"
if [[ -d "$PROJECT/.git" ]]; then
  git -C "$PROJECT" pull --ff-only || die "Git update failed; resolve local changes manually (existing files preserved)."
elif [[ -e "$PROJECT" ]]; then
  die "$PROJECT already exists but is not a Git checkout. Move it or clone manually; nothing was overwritten."
else
  git clone "$REPO_URL" "$PROJECT"
fi

mkdir -p "$PROJECT/data" "$UNIT_DIR"
if [[ ! -e "$PROJECT/config.yaml" ]]; then
  cp "$PROJECT/config.example.yaml" "$PROJECT/config.yaml"
  say "Created config.yaml"
else
  say "Keeping existing config.yaml"
fi
if [[ ! -e "$PROJECT/hardware.yaml" ]]; then
  cp "$PROJECT/hardware.example.yaml" "$PROJECT/hardware.yaml"
  say "Created hardware.yaml (button/LED disabled until wired)"
else
  say "Keeping existing hardware.yaml"
fi

# Update only settings required for this directory layout. Do not touch the other
# settings or existing database; PyYAML is already installed by APT.
PROJECT_DIR="$PROJECT" /usr/bin/python3 - <<'PY'
import os
from pathlib import Path
import yaml
p = Path(os.environ["PROJECT_DIR"]) / "config.yaml"
config = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
if not isinstance(config, dict):
    raise SystemExit("config.yaml must contain a YAML mapping")
# Preserve explicit custom settings, including station_id and existing DB paths.
if not config.get("hardware_config_path"):
    config["hardware_config_path"] = "hardware.yaml"
# For fresh/example-relative paths, store the database in the project data folder.
if config.get("database_path") in (None, "", "./data/measurements.sqlite3"):
    config["database_path"] = "./data/measurements.sqlite3"
p.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY

if systemctl is-active --quiet pilzbase.service; then
  die "An old system-wide pilzbase.service is active. Stop it first with 'sudo systemctl disable --now pilzbase' to prevent two copies writing the same database."
fi

say "Installing user-level systemd service"
cp "$PROJECT/deploy/pilzbase.service" "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload

say "Allowing the user service to start at boot without login"
sudo loginctl enable-linger "$USER"

say "Enabling and starting PilzBase"
systemctl --user enable --now "$UNIT_NAME"
systemctl --user restart "$UNIT_NAME"

say "Service status"
systemctl --user --no-pager --full status "$UNIT_NAME" || true

say "API health check"
if command -v curl >/dev/null; then
  for _ in 1 2 3 4 5; do
    if curl -fsS --max-time 2 "http://127.0.0.1:8000/health"; then
      printf '\n'
      break
    fi
    sleep 1
  done
fi

cat <<'END'

PilzBase installation complete.
  Code and configs:  ~/PilzBase/
  SQLite database:   ~/PilzBase/data/measurements.sqlite3
  Hardware pins:     ~/PilzBase/hardware.yaml
  Config:            ~/PilzBase/config.yaml

Commands:
  systemctl --user status pilzbase
  journalctl --user -u pilzbase -f
  systemctl --user restart pilzbase
  hostname -I

Station dashboard: http://<pi-ip>:8000/
The service starts automatically; experiments require a separate START action.
Sensor values remain SIMULATED until real sensor drivers are integrated.
No authentication: use only on a trusted isolated lab network.
END
