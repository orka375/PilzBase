"""Measurement state machine, with restart-resumable experiments."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import math
from threading import RLock
from .storage import Storage, utc_now
from .sensors import FIELDS, SensorSource

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, storage: Storage, source: SensorSource, station_id: str,
                 interval_seconds: float, duration_days: float):
        self.storage = storage
        self.source = source
        self.station_id = station_id
        self.interval_seconds = interval_seconds
        self.duration_days = duration_days
        self.lock = RLock()
        self._task = None
        self._shutdown = asyncio.Event()
        self.last_success_at = None

    def status(self) -> dict:
        with self.lock:
            exp = self.storage.current()
            return {"station_id": self.station_id,
                    "state": exp["state"] if exp else "READY",
                    "experiment": exp,
                    "measurements": self.storage.count(),
                    "last_measurement_at": self.last_success_at,
                    "sensor_mode": "simulated"}

    def start(self) -> dict:
        with self.lock:
            existing = self.storage.current()
            if existing and existing["state"] == "MEASURING":
                raise ValueError("An experiment is already running")
            now = datetime.now(timezone.utc)
            started = now.isoformat(timespec="microseconds")
            ends = (now + timedelta(days=self.duration_days)).isoformat(timespec="microseconds")
            exp = self.storage.start(self.station_id, started, ends)
            self.storage.log("INFO", "START", f"Experiment {exp['id']} started")
            return exp

    def stop(self) -> dict:
        with self.lock:
            exp = self.storage.current()
            if not exp or exp["state"] != "MEASURING":
                raise ValueError("No experiment is running")
            self.storage.set_state("STOPPED")
            self.storage.log("INFO", "STOP", f"Experiment {exp['id']} stopped")
            return self.storage.current()

    def tick(self) -> bool:
        """Take at most one measurement. Exceptions bubble up for supervised retry."""
        with self.lock:
            exp = self.storage.current()
            if not exp or exp["state"] != "MEASURING":
                return False
            now = datetime.now(timezone.utc)
            if now >= datetime.fromisoformat(exp["ends_at"]):
                self.storage.set_state("FINISHED")
                self.storage.log("INFO", "FINISHED", f"Experiment {exp['id']} completed")
                return False
            cycle = self.storage.next_cycle(exp["id"])
            scheduled = utc_now()
            errors = {}
            try:
                values = self.source.read()
            except Exception as exc:
                logger.exception("Sensor source failed")
                values = {}
                errors["source"] = f"{type(exc).__name__}: {exc}"[:300]
            cleaned = {}
            for field in FIELDS:
                value = values.get(field)
                if value is None:
                    cleaned[field] = None
                    errors.setdefault(field, "missing reading")
                elif isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
                    cleaned[field] = None
                    errors[field] = "invalid reading"
                else:
                    cleaned[field] = float(value)
            self.storage.save(exp["id"], cycle, scheduled, cleaned, errors)
            if errors:
                self.storage.log("WARNING", "SENSOR_ERROR", str(errors)[:900])
            self.last_success_at = utc_now()
            return True

    async def run(self):
        """Schedule against monotonic time; skip late slots rather than bunching reads."""
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while not self._shutdown.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("Measurement cycle failed; retrying")
            next_at += self.interval_seconds
            if next_at <= loop.time():
                next_at = loop.time() + self.interval_seconds
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=max(0, next_at - loop.time()))
            except asyncio.TimeoutError:
                pass

    def shutdown(self):
        self._shutdown.set()
