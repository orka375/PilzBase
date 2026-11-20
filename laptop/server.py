"""Laptop-side proxy and UI for five independently logging stations.

Only explicitly configured stations are contacted; never forward arbitrary URLs.
The laptop is NOT needed for continued measurement.
"""
import argparse
import asyncio
import ipaddress
import json
import os
import socket
import tempfile
from contextlib import asynccontextmanager
import csv
import io
from threading import RLock
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as URLRequest, urlopen

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from .collector import Collector

ROOT = Path(__file__).resolve().parent
ID_PATTERN = re.compile(r"UNIT-[0-9]{2}\Z")


def load_config(path: str):
    obj = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("stations"), list):
        raise ValueError("Missing stations list in configuration")
    stations = {}
    tokens = {}
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
        if station.get("token"):
            tokens[sid] = str(station["token"])
    if len(stations) > 5 or not set(stations).issubset({f"UNIT-{i:02}" for i in range(1, 6)}):
        raise ValueError("Only UNIT-01 to UNIT-05 are supported")
    host = obj.get("bind_host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Laptop dashboard must bind to loopback; use a secure proxy for remote access")
    port = int(obj.get("port", 8080))
    if not (1 <= port <= 65535):
        raise ValueError("Invalid port")
    database_path = obj.get("database_path") or str(Path(path).resolve().parent / "pilzbase-laptop.sqlite3")
    return {"stations": stations, "tokens": tokens, "bind_host": host, "port": port, "config_path": str(Path(path).resolve()), "database_path": database_path}


def fetch_station(sid, base_url, path, method="GET", *, timeout=4, payload=None, token=""):
    # Do not follow redirects to unexpected hosts.
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    req = URLRequest(base_url + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={**({"Content-Type": "application/json"} if payload is not None else {}),
                 **({"X-Pilz-Token": token} if token else {})})
    try:
        with build_opener(NoRedirect).open(req, timeout=timeout) as response:
            body = response.read(2_000_000)
            return json.loads(body) if path.endswith("/status") or "/measurements?" in path or path.endswith("/events") else json.loads(body)
    except HTTPError as exc:
        raise RuntimeError(f"Station returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


def create_app(config):
    collector = Collector(config.get("database_path", ":memory:"))
    stations = dict(config["stations"])
    tokens = dict(config.get("tokens", {}))
    lock = RLock()
    config_path = config.get("config_path")

    def sync_one(sid):
        if sid not in stations:
            raise ValueError("Unknown station")
        base = stations[sid]
        status = fetch_station(sid, base, "/api/v1/status")
        generation = status.get("storage_generation")
        if not generation:
            return {"station_id": sid, "mode": "legacy", "downloaded": 0}
        downloaded = 0
        while True:
            cursor = collector.cursor(sid)
            if cursor and cursor["generation"] != generation:
                raise ValueError("Station storage generation changed; review before synchronization")
            last = cursor["last_id"] if cursor else 0
            packet = fetch_station(sid, base, f"/api/v1/measurements?after_id={last}&limit=100")
            if packet.get("storage_generation") != generation:
                raise ValueError("Station changed storage generation during download")
            rows = packet.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError("Invalid measurement response")
            committed = collector.ingest(sid, generation, rows)
            # Also resend the last ACK if the response was lost earlier.
            fetch_station(sid, base, "/api/v1/measurements/ack", "POST",
                          payload={"generation": generation, "through_id": committed}, token=tokens.get(sid, ""))
            downloaded += len(rows)
            if len(rows) < 100:
                break
        return {"station_id": sid, "mode": "esp32", "downloaded": downloaded,
                "last_id": collector.cursor(sid)["last_id"]}

    @asynccontextmanager
    async def lifespan(app):
        async def periodic():
            while True:
                with lock:
                    targets = list(stations)
                for sid in targets:
                    try:
                        await asyncio.to_thread(sync_one, sid)
                    except Exception:
                        # Individual stations may be offline; retry next interval.
                        pass
                await asyncio.sleep(60)
        task = asyncio.create_task(periodic())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            collector.close()

    app = FastAPI(title="PilzBase Central Dashboard", version="0.3.0", lifespan=lifespan)

    def persist():
        if not config_path:
            raise HTTPException(409, "Persistence requires a configuration file")
        config_file = Path(config_path)
        obj = {"bind_host": config["bind_host"], "port": config["port"],
               "database_path": config.get("database_path", "laptop/pilzbase-laptop.sqlite3"),
               "stations": [{"id": sid, "url": url, **({"token": tokens[sid]} if sid in tokens else {})}
                            for sid, url in sorted(stations.items())]}
        for target, payload, mode in ((config_file, yaml.safe_dump(obj, sort_keys=False), 0o600),):
            fd, tmp = tempfile.mkstemp(prefix=".pilzbase-", dir=target.parent)
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write(payload)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def verify_address(value):
        if not isinstance(value, str):
            raise HTTPException(400, "Invalid address")
        value = value.strip().lower()
        parts = value.split(":")
        if len(parts) > 2 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", parts[0]):
            raise HTTPException(400, "Use a hostname or IPv4 address, optionally with port")
        port = 8000 if len(parts) == 1 else int(parts[1]) if parts[1].isdigit() else 0
        if not (1 <= port <= 65535):
            raise HTTPException(400, "Invalid port")
        try:
            addresses = socket.getaddrinfo(parts[0], port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise HTTPException(400, "Cannot resolve hostname") from exc
        if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_private for addr in addresses):
            raise HTTPException(400, "Only private network addresses are supported")
        return f"http://{parts[0]}:{port}"

    @app.post("/api/stations/connect")
    async def connect(request: Request):
        data = await request.json()
        sid = data.get("id")
        if sid not in {f"UNIT-{i:02}" for i in range(1, 6)}:
            raise HTTPException(400, "Invalid station ID")
        url = await asyncio.to_thread(verify_address, data.get("address"))
        try:
            status = await asyncio.to_thread(fetch_station, sid, url, "/api/v1/status")
        except RuntimeError as exc:
            raise HTTPException(502, "Connection failed: " + str(exc)) from exc
        if status.get("station_id") != sid:
            raise HTTPException(409, "Connected device has another station ID")
        with lock:
            stations[sid] = url
            if data.get("token"):
                tokens[sid] = str(data["token"])
            persist()
        return {"id": sid, "url": url, "connected": True}

    @app.delete("/api/stations/{sid}")
    async def remove(sid: str):
        with lock:
            if sid not in stations:
                raise HTTPException(404, "Unknown station")
            del stations[sid]
            tokens.pop(sid, None)
            persist()
        return {"removed": sid}

    @app.get("/api/configured-stations")
    async def configured():
        return {"stations": [{"id": sid, "url": url} for sid, url in sorted(stations.items())]}


    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return (ROOT / "dashboard.html").read_text(encoding="utf-8")

    async def call(sid, endpoint, method="GET"):
        if sid not in stations:
            raise HTTPException(404, "Unknown station")
        try:
            return await asyncio.to_thread(fetch_station, sid, stations[sid], endpoint, method,
                                            token=tokens.get(sid, ""))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/stations")
    async def all_stations():
        async def one(sid):
            try:
                status = await call(sid, "/api/v1/status")
                if status.get("storage_generation"):
                    rows = collector.rows(sid, max(0, (collector.cursor(sid) or {}).get("last_id", 0) - 1), 1)
                    latest = rows[-1] if rows else None
                    status["measurements"] = collector.count(sid)
                else:
                    result = await call(sid, "/api/v1/measurements?after_id=" + str(max(0, int(status.get("measurements", 0)) - 1)) + "&limit=1")
                    latest = (result.get("rows") or [None])[-1]
                return {"id": sid, "online": True, "url": stations[sid], "status": status, "latest": latest}
            except (HTTPException, ValueError, TypeError) as exc:
                cursor = collector.cursor(sid)
                rows = collector.rows(sid, max(0, cursor["last_id"] - 1), 1) if cursor else []
                return {"id": sid, "online": False, "url": stations[sid],
                        "latest": rows[-1] if rows else None,
                        "status": {"measurements": collector.count(sid), "state": "OFFLINE"},
                        "error": getattr(exc, "detail", str(exc))}
        return {"stations": await asyncio.gather(*(one(sid) for sid in stations))}

    @app.get("/api/stations/{sid}/measurements")
    async def readings(sid: str, after_id: int = Query(0, ge=0), limit: int = Query(240, ge=1, le=5000)):
        if sid not in stations:
            raise HTTPException(404, "Unknown station")
        if collector.cursor(sid):
            return {"station_id": sid, "rows": collector.rows(sid, after_id, limit)}
        return await call(sid, f"/api/v1/measurements?after_id={after_id}&limit={limit}")

    @app.post("/api/stations/{sid}/sync")
    async def synchronize(sid: str):
        if sid not in stations:
            raise HTTPException(404, "Unknown station")
        try:
            return await asyncio.to_thread(sync_one, sid)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/stations/{sid}/measurements.csv")
    def export_station(sid: str):
        if sid not in stations:
            raise HTTPException(404, "Unknown station")
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "acquired_at", "experiment_id", "values_json", "errors_json"])
        after = 0
        while True:
            batch = collector.rows(sid, after, 1000)
            if not batch:
                break
            for r in batch:
                writer.writerow([r["id"], r["acquired_at"], r["experiment_id"],
                                 json.dumps(r["values"]), json.dumps(r["errors"])])
            after = batch[-1]["id"]
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={sid}.csv"})

    @app.get("/api/stations/{sid}/sensors")
    async def sensor_overview(sid: str):
        return await call(sid, "/api/v1/sensors")

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
