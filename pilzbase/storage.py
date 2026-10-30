"""SQLite storage: one writer; durable commits for scientific time series."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from .sensors import FIELDS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Storage:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('MEASURING','STOPPED','FINISHED')),
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                stopped_at TEXT,
                station_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER NOT NULL REFERENCES experiments(id),
                scheduled_at TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                cycle INTEGER NOT NULL,
                simulated INTEGER NOT NULL,
                values_json TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                UNIQUE(experiment_id, cycle)
            );
            CREATE INDEX IF NOT EXISTS ix_measurements_exp ON measurements(experiment_id,id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                level TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL
            );
        """)
        self.db.commit()

    def log(self, level: str, code: str, message: str):
        with self.db:
            self.db.execute("INSERT INTO events (occurred_at,level,code,message) VALUES (?,?,?,?)",
                            (utc_now(),level,code,message[:1000]))

    def current(self):
        row = self.db.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def start(self, station_id: str, started: str, ends: str) -> dict:
        with self.db:
            self.db.execute("INSERT INTO experiments (state,started_at,ends_at,station_id) VALUES ('MEASURING',?,?,?)",
                            (started,ends,station_id))
        return self.current()

    def set_state(self, state: str):
        with self.db:
            self.db.execute("UPDATE experiments SET state=?,stopped_at=? WHERE id=(SELECT MAX(id) FROM experiments)",
                            (state, utc_now()))

    def next_cycle(self, experiment_id: int) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(cycle),0)+1 FROM measurements WHERE experiment_id=?", (experiment_id,)).fetchone()
        return row[0]

    def save(self, experiment_id: int, cycle: int, scheduled: str, values: dict, errors: dict):
        normalized = {name: values.get(name) for name in FIELDS}
        with self.db:
            self.db.execute("""INSERT INTO measurements
                (experiment_id,scheduled_at,acquired_at,cycle,simulated,values_json,errors_json)
                VALUES (?,?,?,?,1,?,?)""",
                (experiment_id,scheduled,utc_now(),cycle,json.dumps(normalized,allow_nan=False),json.dumps(errors)))

    def rows(self, after_id: int = 0, limit: int = 1000):
        result = self.db.execute("SELECT * FROM measurements WHERE id>? ORDER BY id LIMIT ?", (after_id,limit)).fetchall()
        return [{**dict(row), "values": json.loads(row["values_json"]),
                 "errors": json.loads(row["errors_json"])} for row in result]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]

    def recent_events(self, limit: int = 100):
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def close(self):
        self.db.close()
