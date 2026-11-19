from __future__ import annotations

from datetime import datetime as RealDateTime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import autotrender_market_data as market_data
from app.main import app


def _set_market_data_time(monkeypatch, hour=10, minute=0):
    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 15, hour, minute, tzinfo=tz)

    monkeypatch.setattr(market_data, "datetime", FixedDateTime)




def test_fin_nifty_intraday_uses_supported_upstream_category(monkeypatch) -> None:
    _set_market_data_time(monkeypatch)
    auth = MagicMock()
    auth.get.return_value.status_code = 200
    auth.get.return_value.headers = {"Content-Type": "application/json"}
    auth.get.return_value.json.return_value = {
        "title": "FINNIFTY Option Data Weekly",
        "data": [{"price": "24365", "pcr": {"value": "0.08"}}],
    }
    app.state.autotrender_auth = auth
    try:
        response = TestClient(app).get("/api/v1/autotrender/indices/FINNIFTY/intraday?mins=5")
    finally:
        del app.state.autotrender_auth

    assert response.status_code == 200
    assert response.json()["data"][0]["price"] == "24365"
    auth.get.assert_called_once_with(
        "/api/derivatives/intra-day",
        params={"category": "FinNifty", "mins": 5},
    )


def test_sensex_oi_graph_uses_supported_upstream_category(monkeypatch) -> None:
    _set_market_data_time(monkeypatch)
    auth = MagicMock()
    auth.get.return_value.status_code = 200
    auth.get.return_value.headers = {"Content-Type": "application/json"}
    auth.get.return_value.json.return_value = {
        "definition": {"title": "Sensex"},
        "data": {"graph": {"option": [{"value": "-1561440", "time": "1530"}]}},
    }
    app.state.autotrender_auth = auth
    try:
        response = TestClient(app).get("/api/v1/autotrender/indices/SENSEX/oi-graph")
    finally:
        del app.state.autotrender_auth

    assert response.status_code == 200
    assert response.json()["data"]["graph"]["option"][0]["value"] == "-1561440"
    auth.get.assert_called_once_with(
        "/api/derivatives/table/graph",
        params={"category": "Sensex"},
    )


def test_unsupported_autotrender_index_is_rejected(monkeypatch) -> None:
    _set_market_data_time(monkeypatch)
    response = TestClient(app).get("/api/v1/autotrender/indices/UNKNOWN/intraday")
    assert response.status_code == 404
def test_banknifty_intraday_uses_supported_upstream_category(monkeypatch) -> None:
    _set_market_data_time(monkeypatch)
    auth = MagicMock()
    auth.get.return_value.status_code = 200
    auth.get.return_value.headers = {"Content-Type": "application/json"}
    auth.get.return_value.json.return_value = {"data": []}
    app.state.autotrender_auth = auth
    try:
        response = TestClient(app).get("/api/v1/autotrender/indices/BANKNIFTY/intraday")
    finally:
        del app.state.autotrender_auth

    assert response.status_code == 200
    auth.get.assert_called_once_with(
        "/api/derivatives/intra-day",
        params={"category": "BankNifty", "mins": 5},
    )


def test_nifty_oi_graph_uses_supported_upstream_category(monkeypatch) -> None:
    _set_market_data_time(monkeypatch)
    auth = MagicMock()
    auth.get.return_value.status_code = 200
    auth.get.return_value.headers = {"Content-Type": "application/json"}
    auth.get.return_value.json.return_value = {"data": {"graph": {"option": [], "future": []}}}
    app.state.autotrender_auth = auth
    try:
        response = TestClient(app).get("/api/v1/autotrender/indices/NIFTY/oi-graph")
    finally:
        del app.state.autotrender_auth

    assert response.status_code == 200
    auth.get.assert_called_once_with(
        "/api/derivatives/table/graph",
        params={"category": "Nifty"},
    )



@pytest.mark.parametrize("path", [
    "/api/v1/autotrender/indices/NIFTY/intraday",
    "/api/v1/autotrender/indices/NIFTY/oi-graph",
])
def test_market_data_is_blocked_before_930_ist(monkeypatch, path):
    _set_market_data_time(monkeypatch, hour=9, minute=29)
    response = TestClient(app).get(path)
    assert response.status_code == 425
