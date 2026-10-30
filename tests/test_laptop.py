import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from laptop.server import create_app, load_config


def test_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("stations:\n" + "".join(
        f"  - id: UNIT-{i:02d}\n    url: http://192.168.1.{100+i}:8000\n"
        for i in range(1, 6)))
    result = load_config(str(cfg))
    assert len(result["stations"]) == 5
    assert result["bind_host"] == "127.0.0.1"


def test_invalid_station_url(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("stations:\n" + "".join(
        f"  - id: UNIT-{i:02d}\n    url: {'https://example.com/path' if i == 1 else 'http://localhost:8000'}\n"
        for i in range(1, 6)))
    with pytest.raises(ValueError):
        load_config(str(cfg))


def test_dashboard_and_station_proxies(monkeypatch):
    import laptop.server as server
    cfg = {"stations": {f"UNIT-{i:02d}": f"http://192.168.1.{i}:8000" for i in range(1, 6)}}
    def fake(sid, url, path, method="GET", **kwargs):
        if path.endswith("/status"):
            return {"station_id": sid, "state": "MEASURING", "measurements": 1}
        if "/measurements?" in path:
            return {"rows": [{"id": 1, "acquired_at": "2026-10-30T12:00:00+00:00", "values": {"pt100_1_c": 24.0}}]}
        return {"state": "STOPPED"}
    monkeypatch.setattr(server, "fetch_station", fake)
    with TestClient(create_app(cfg)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Experiment overview" in page.text
        result = client.get("/api/stations")
        assert result.status_code == 200
        assert len(result.json()["stations"]) == 5
        assert all(r["online"] for r in result.json()["stations"])
        assert client.post("/api/stations/UNIT-03/stop").status_code == 200
        assert client.post("/api/stations/UNIT-99/start").status_code == 404
