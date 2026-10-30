from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from pilzbase.config import Config
from pilzbase.app import create_app
from pilzbase.engine import Engine
from pilzbase.storage import Storage


def test_start_log_stop_and_restart(tmp_path):
    path = str(tmp_path / "db.sqlite3")
    class Faulty:
        def read(self):
            return {"pt100_1_c": 23.5, "co2_ppm": float("nan")}
    storage = Storage(path)
    engine = Engine(storage, Faulty(), "UNIT-01", 15, 56)
    assert engine.status()["state"] == "READY"
    engine.start()
    assert engine.tick()
    assert engine.status()["measurements"] == 1
    row = storage.rows()[0]
    assert row["values"]["pt100_1_c"] == 23.5
    assert row["values"]["co2_ppm"] is None
    assert "co2_ppm" in row["errors"]
    storage.close()
    storage2 = Storage(path)
    engine2 = Engine(storage2, Faulty(), "UNIT-01", 15, 56)
    assert engine2.status()["state"] == "MEASURING"
    assert engine2.tick()
    assert [r["cycle"] for r in storage2.rows()] == [1, 2]
    engine2.stop()
    assert not engine2.tick()
    storage2.close()


def test_api_and_bearer(tmp_path):
    cfg = Config(database_path=str(tmp_path / "api.sqlite3"), api_token="testtoken")
    app = create_app(cfg)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/status").status_code == 401
        headers = {"Authorization": "Bearer testtoken"}
        assert client.get("/api/v1/status", headers=headers).json()["state"] == "READY"
        assert client.post("/api/v1/experiments/start", headers=headers).status_code == 200
        assert client.post("/api/v1/experiments/start", headers=headers).status_code == 409
        assert client.get("/api/v1/measurements.csv", headers=headers).status_code == 200
        assert client.post("/api/v1/experiments/stop", headers=headers).status_code == 200


def test_expiry(tmp_path):
    s = Storage(str(tmp_path / "end.db"))
    class Empty:
        def read(self): return {}
    e = Engine(s, Empty(), "UNIT-01", 15, 56)
    e.start()
    with s.db:
        s.db.execute("UPDATE experiments SET ends_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    assert not e.tick()
    assert e.status()["state"] == "FINISHED"
    s.close()
