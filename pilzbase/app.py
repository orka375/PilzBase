"""Station HTTP API. Use a reverse proxy/TLS or trusted LAN/VPN for remote control."""
from contextlib import asynccontextmanager
from importlib.resources import files
import asyncio
import csv
import io
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn
from .config import Config, load_config
from .engine import Engine
from .sensors import SimulatedSensors, FIELDS
from .storage import Storage
from .hardware import HardwareController, load_hardware
from .ups import UPSHatC
from .physical_sensors import PhysicalSensors

log = logging.getLogger(__name__)


def create_app(cfg: Config) -> FastAPI:
    store = Storage(cfg.database_path)

    hardware_config = load_hardware(cfg.hardware_config_path) if cfg.hardware_config_path else {}
    ups_cfg = hardware_config.get("ups_hat_c", {})
    ups = UPSHatC(ups_cfg.get("bus", 1), ups_cfg.get("address", 0x43)) if ups_cfg.get("enabled", False) else None

    physical = PhysicalSensors(hardware_config) if cfg.sensor_mode == "hardware" else None

    class StationSensors:
        def read(self):
            values = physical.read() if physical else SimulatedSensors().read()
            if ups:
                try:
                    values.update(ups.read())
                except Exception:
                    log.exception("UPS HAT C read failed")
            return values

    engine = Engine(store, StationSensors(), cfg.station_id, cfg.interval_seconds, cfg.duration_days)
    hardware = HardwareController(engine, hardware_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.log("INFO", "BOOT", "Station software started")
        task = asyncio.create_task(engine.run())
        hw_task = asyncio.create_task(hardware.run())
        try:
            yield
        finally:
            engine.shutdown()
            hw_task.cancel()
            try:
                await hw_task
            except asyncio.CancelledError:
                pass
            await task
            store.log("INFO", "SHUTDOWN", "Station software stopped")
            store.close()

    app = FastAPI(title="PilzBase Station", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine

    @app.get('/', response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return files('pilzbase').joinpath('dashboard.html').read_text(encoding='utf-8')

    @app.get("/health")
    def health():
        return {"ok": True, "station_id": cfg.station_id}

    @app.get("/api/v1/status")
    def status():
        return engine.status()

    @app.post("/api/v1/experiments/start")
    def start():
        try:
            return engine.start()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/experiments/stop")
    def stop():
        try:
            return engine.stop()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/measurements")
    def measurements(after_id: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000)):
        return {"station_id": cfg.station_id, "rows": store.rows(after_id, limit)}

    @app.get("/api/v1/events")
    def events(limit: int = Query(100, ge=1, le=1000)):
        return {"rows": store.recent_events(limit)}

    @app.get("/api/v1/measurements.csv")
    def csv_export(after_id: int = Query(0, ge=0), limit: int = Query(5000, ge=1, le=50000)):
        # Bounded export: client paginates by id for large experiments.
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "experiment_id", "cycle", "scheduled_at", "acquired_at", "simulated", *FIELDS, "errors_json"])
        for row in store.rows(after_id, limit):
            writer.writerow([row["id"], row["experiment_id"], row["cycle"],
                             row["scheduled_at"],row["acquired_at"],row["simulated"],
                             *(row["values"].get(f) for f in FIELDS),row["errors_json"]])
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=pilzbase.csv"})

    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.api_host, port=cfg.api_port, workers=1)


if __name__ == "__main__":
    main()
