import pytest
from laptop.collector import Collector


def test_retries_and_commit_cursor(tmp_path):
    db = Collector(tmp_path / "records.sqlite")
    rows = [{"id": 1, "acquired_at": "2026-11-20T12:00:00Z", "values": {"co2_ppm": 700}},
            {"id": 2, "acquired_at": "2026-11-20T12:00:15Z", "values": {"co2_ppm": 701}}]
    assert db.ingest("UNIT-01", "flash-a", rows) == 2
    assert db.ingest("UNIT-01", "flash-a", rows) == 2
    assert db.count("UNIT-01") == 2
    assert db.cursor("UNIT-01") == {"generation": "flash-a", "last_id": 2}
    db.close()
    db = Collector(tmp_path / "records.sqlite")
    assert db.cursor("UNIT-01")["last_id"] == 2
    assert db.rows("UNIT-01")[1]["values"]["co2_ppm"] == 701
    db.close()


def test_gap_and_generation_rejected(tmp_path):
    db = Collector(tmp_path / "records.sqlite")
    with pytest.raises(ValueError, match="Gap"):
        db.ingest("UNIT-01", "flash-a", [{"id": 2, "values": {}}])
    db.ingest("UNIT-01", "flash-a", [{"id": 1, "values": {}}])
    with pytest.raises(ValueError, match="generation"):
        db.ingest("UNIT-01", "flash-b", [{"id": 2, "values": {}}])
    db.close()


def test_sync_acknowledges_only_committed_records(tmp_path, monkeypatch):
    import laptop.server as server
    calls = []
    def fake(sid, url, path, method="GET", **kw):
        calls.append((path, method, kw.get("payload")))
        if path.endswith("/status"):
            return {"station_id": sid, "storage_generation": "flash-a", "state": "MEASURING"}
        if path.startswith("/api/v1/measurements?"):
            after = int(path.split("after_id=")[1].split("&")[0])
            return {"storage_generation": "flash-a", "rows": ([{"id": 1, "values": {"co2_ppm": 700}}] if after == 0 else [])}
        if path.endswith("/ack"):
            return {"acknowledged": kw["payload"]["through_id"]}
        return {}
    monkeypatch.setattr(server, "fetch_station", fake)
    from fastapi.testclient import TestClient
    cfg = {"stations": {"UNIT-01": "http://192.168.1.101:8000"}, "database_path": str(tmp_path / "db.sqlite"),
           "tokens": {"UNIT-01": "abcdefghijklmnop"}}
    with TestClient(server.create_app(cfg)) as client:
        result = client.post("/api/stations/UNIT-01/sync")
        assert result.status_code == 200
        assert client.get("/api/stations/UNIT-01/measurements").json()["rows"][0]["id"] == 1
        assert client.get("/api/stations/UNIT-01/measurements.csv").status_code == 200
    assert any(path.endswith("/ack") and payload["through_id"] == 1 for path, _, payload in calls if payload)
