from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient

from app.api import autotrender_market_data
from app.main import app


class FakeResult:
    def __init__(self, scalar=None, rows=None, row=None):
        self._scalar = scalar
        self._rows = rows or []
        self._row = row

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._row


class FakeConnection:
    def execute(self, statement, params):
        if "COUNT(*)" in str(statement):
            return FakeResult(scalar=1)
        return FakeResult(rows=[{
            "snapshot_id": 7,
            "trading_date": date(2026, 10, 26),
            "captured_at": datetime(2026, 10, 26, 15, 30),
            "created_at": datetime(2026, 10, 26, 15, 30),
            "payload_bytes": 128,
        }])


class FakeEngine:
    def connect(self):
        return self

    def __enter__(self):
        return FakeConnection()

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_historical_cache_endpoint_lists_mysql_snapshots(monkeypatch) -> None:
    monkeypatch.setattr(autotrender_market_data, "get_engine", lambda: FakeEngine())

    response = TestClient(app).get("/api/v1/autotrender/historical?start_date=2026-10-01&end_date=2026-10-31")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["snapshots"][0]["snapshot_id"] == 7
    assert payload["snapshots"][0]["trading_date"] == "2026-10-26"
    assert payload["snapshots"][0]["payload_bytes"] == 128


def test_historical_cache_endpoint_rejects_invalid_date() -> None:
    response = TestClient(app).get("/api/v1/autotrender/historical?start_date=not-a-date")
    assert response.status_code == 422
