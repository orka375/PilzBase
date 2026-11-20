"""Durable, idempotent ESP32 measurement import.

The device only reclaims data after the laptop commits rows and acknowledges
the highest contiguous imported ID. Replaying downloads is always safe.
"""
import json
import sqlite3
from pathlib import Path
from threading import RLock


class Collector:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                station_id TEXT NOT NULL,
                generation TEXT NOT NULL,
                id INTEGER NOT NULL,
                acquired_at TEXT,
                values_json TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                experiment_id INTEGER,
                PRIMARY KEY(station_id, generation, id)
            );
            CREATE TABLE IF NOT EXISTS cursors (
                station_id TEXT PRIMARY KEY,
                generation TEXT NOT NULL,
                last_id INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.db.commit()

    def cursor(self, station):
        with self.lock:
            row = self.db.execute("SELECT generation,last_id FROM cursors WHERE station_id=?", (station,)).fetchone()
            return {"generation": row[0], "last_id": row[1]} if row else None

    def ingest(self, station, generation, rows):
        if not isinstance(generation, str) or not generation or len(generation) > 80:
            raise ValueError("Invalid device generation")
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError("Invalid measurement batch")
        with self.lock, self.db:
            current = self.cursor(station)
            if current and current["generation"] != generation:
                raise ValueError("Device storage generation changed; manual reconciliation required")
            last = current["last_id"] if current else 0
            for row in rows:
                record_id = row.get("id")
                if type(record_id) is not int or record_id <= 0:
                    raise ValueError("Invalid record ID")
                if record_id > last + 1:
                    raise ValueError("Gap in measurement sequence")
                if not isinstance(row.get("values"), dict) or not isinstance(row.get("errors", {}), dict):
                    raise ValueError("Invalid record contents")
                if record_id <= last:
                    continue
                self.db.execute("""INSERT OR IGNORE INTO records
                    (station_id,generation,id,acquired_at,values_json,errors_json,experiment_id)
                    VALUES (?,?,?,?,?,?,?)""", (station, generation, record_id,
                    row.get("acquired_at"), json.dumps(row["values"], allow_nan=False),
                    json.dumps(row.get("errors", {})), row.get("experiment_id")))
                last = record_id
            self.db.execute("""INSERT INTO cursors(station_id,generation,last_id) VALUES (?,?,?)
                ON CONFLICT(station_id) DO UPDATE SET generation=excluded.generation,last_id=excluded.last_id""",
                (station, generation, last))
            return last

    def rows(self, station, after_id=0, limit=240):
        with self.lock:
            result = self.db.execute("""SELECT id,acquired_at,values_json,errors_json,experiment_id
                 FROM records WHERE station_id=? AND id>? ORDER BY id LIMIT ?""",
                 (station, after_id, limit)).fetchall()
        return [{"id": r[0], "acquired_at": r[1], "values": json.loads(r[2]),
                 "errors": json.loads(r[3]), "experiment_id": r[4]} for r in result]

    def count(self, station):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM records WHERE station_id=?", (station,)).fetchone()[0]

    def close(self):
        self.db.close()
