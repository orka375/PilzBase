"""Station HTTP API. Use a reverse proxy/TLS or trusted LAN/VPN for remote control."""
from contextlib import asynccontextmanager
import asyncio
import csv
import io
import secrets
import logging
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn
from .config import Config, load_config
from .engine import Engine
from .sensors import SimulatedSensors, FIELDS
from .storage import Storage

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def create_app(cfg: Config) -> FastAPI:
    store = Storage(cfg.database_path)
    engine = Engine(store, SimulatedSensors(), cfg.station_id, cfg.interval_seconds, cfg.duration_days)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.log("INFO", "BOOT", "Station software started")
        task = asyncio.create_task(engine.run())
        try:
            yield
        finally:
            engine.shutdown()
            await task
            store.log("INFO", "SHUTDOWN", "Station software stopped")
            store.close()

    app = FastAPI(title="PilzBase Station", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if cfg.api_token and (credentials is None or
                              not secrets.compare_digest(credentials.credentials, cfg.api_token)):
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        return True

    @app.get("/health")
    def health():
        return {"ok": True, "station_id": cfg.station_id}

    @app.get("/api/v1/status", dependencies=[Depends(authorize)])
    def status():
        return engine.status()

    @app.post("/api/v1/experiments/start", dependencies=[Depends(authorize)])
    def start():
        try:
            return engine.start()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/experiments/stop", dependencies=[Depends(authorize)])
    def stop():
        try:
            return engine.stop()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/measurements", dependencies=[Depends(authorize)])
    def measurements(after_id: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000)):
        return {"station_id": cfg.station_id, "rows": store.rows(after_id, limit)}

    @app.get("/api/v1/events", dependencies=[Depends(authorize)])
    def events(limit: int = Query(100, ge=1, le=1000)):
        return {"rows": store.recent_events(limit)}

    @app.get("/api/v1/measurements.csv", dependencies=[Depends(authorize)])
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
