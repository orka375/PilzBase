"""Laptop-side proxy and UI for five independently logging stations.

Only explicitly configured stations are contacted; never forward arbitrary URLs.
The laptop is NOT needed for continued measurement.
"""
import argparse
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

ROOT = Path(__file__).resolve().parent
ID_PATTERN = re.compile(r"UNIT-[0-9]{2}\Z")


def load_config(path: str):
    obj = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("stations"), list):
        raise ValueError("Missing stations list in configuration")
    stations = {}
    for station in obj["stations"]:
        if not isinstance(station, dict) or not ID_PATTERN.fullmatch(str(station.get("id", ""))):
            raise ValueError("Invalid station identifier")
        sid = station["id"]
        url = str(station.get("url", "")).rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError(f"Invalid station URL: {sid}")
        if sid in stations:
            raise ValueError(f"Duplicate station ID: {sid}")
        stations[sid] = url
    if len(stations) != 5 or set(stations) != {f"UNIT-{i:02}" for i in range(1, 6)}:
        raise ValueError("Exactly UNIT-01 to UNIT-05 must be configured")
    host = obj.get("bind_host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Laptop dashboard must bind to loopback; use a secure proxy for remote access")
    port = int(obj.get("port", 8080))
    if not (1 <= port <= 65535):
        raise ValueError("Invalid port")
    return {"stations": stations, "bind_host": host, "port": port}


def _token(sid):
    return os.getenv("PILZBASE_" + sid.replace("-", "_") + "_TOKEN", "")


def fetch_station(sid, base_url, path, method="GET", *, timeout=4):
    token = _token(sid)
    if not token:
        raise RuntimeError(f"Token for {sid} is not configured")
    # Explicitly disable redirects to prevent forwarding tokens to a different host.
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    req = Request(base_url + path, headers={"Authorization": "Bearer " + token}, method=method)
    try:
        with build_opener(NoRedirect).open(req, timeout=timeout) as response:
            body = response.read(2_000_000)
            return json.loads(body) if path.endswith("/status") or "/measurements?" in path or path.endswith("/events") else json.loads(body)
    except HTTPError as exc:
        raise RuntimeError(f"Station returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


def create_app(config):
    app = FastAPI(title="PilzBase Central Dashboard", version="0.2.0")
    stations = config["stations"]

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return (ROOT / "dashboard.html").read_text(encoding="utf-8")

    async def call(sid, endpoint, method="GET"):
        if sid not in stations:
            raise HTTPException(404, "Unknown station")
        try:
            return await asyncio.to_thread(fetch_station, sid, stations[sid], endpoint, method)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/stations")
    async def all_stations():
        async def one(sid):
            try:
                status = await call(sid, "/api/v1/status")
                latest = await call(sid, "/api/v1/measurements?after_id=" + str(max(0, int(status.get("measurements", 0)) - 1)) + "&limit=1")
                return {"id": sid, "online": True, "status": status, "latest": (latest.get("rows") or [None])[-1]}
            except (HTTPException, ValueError, TypeError) as exc:
                return {"id": sid, "online": False, "error": getattr(exc, "detail", str(exc))}
        return {"stations": await asyncio.gather(*(one(sid) for sid in stations))}

    @app.get("/api/stations/{sid}/measurements")
    async def readings(sid: str, after_id: int = Query(0, ge=0), limit: int = Query(240, ge=1, le=5000)):
        return await call(sid, f"/api/v1/measurements?after_id={after_id}&limit={limit}")

    @app.post("/api/stations/{sid}/start")
    async def start(sid: str):
        return await call(sid, "/api/v1/experiments/start", "POST")

    @app.post("/api/stations/{sid}/stop")
    async def stop(sid: str):
        return await call(sid, "/api/v1/experiments/stop", "POST")

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.getenv("PILZBASE_LAPTOP_CONFIG", "laptop/config.yaml"))
    args = parser.parse_args()
    cfg = load_config(args.config)
    uvicorn.run(create_app(cfg), host=cfg["bind_host"], port=cfg["port"])


if __name__ == "__main__":
    main()
