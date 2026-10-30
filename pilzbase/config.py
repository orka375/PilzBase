from dataclasses import dataclass
from pathlib import Path
import os
import re
import yaml


@dataclass(frozen=True)
class Config:
    station_id: str = "UNIT-01"
    interval_seconds: float = 15
    duration_days: float = 56
    database_path: str = "./data/measurements.sqlite3"
    sensor_mode: str = "simulated"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_token: str = ""


def load_config(path: str | None = None) -> Config:
    path = path or os.getenv("PILZBASE_CONFIG", "config.yaml")
    settings = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(settings, dict):
        raise ValueError("Configuration must be a YAML mapping")
    allowed = set(Config.__dataclass_fields__) - {"api_token"}
    unknown = set(settings) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
    settings["api_token"] = os.getenv("PILZBASE_API_TOKEN", "")
    cfg = Config(**settings)
    if not re.fullmatch(r"UNIT-[0-9]{2}", cfg.station_id):
        raise ValueError("station_id must look like UNIT-01")
    if not (0.1 <= cfg.interval_seconds <= 3600):
        raise ValueError("interval_seconds must be between 0.1 and 3600")
    if not (0 < cfg.duration_days <= 365):
        raise ValueError("duration_days must be between 0 and 365")
    if cfg.sensor_mode != "simulated":
        raise ValueError("Only simulated mode is implemented; real sensor drivers require verified wiring")
    if not (1 <= cfg.api_port <= 65535):
        raise ValueError("Invalid port")
    if cfg.api_host not in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
        raise ValueError("api_host must be a known bind address")
    if cfg.api_host == "0.0.0.0" and not cfg.api_token:
        raise ValueError("PILZBASE_API_TOKEN is required when binding to the network")
    return cfg
