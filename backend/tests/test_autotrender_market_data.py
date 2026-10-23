from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.main import app


def test_fin_nifty_intraday_uses_supported_upstream_category() -> None:
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


def test_sensex_oi_graph_uses_supported_upstream_category() -> None:
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


def test_unsupported_autotrender_index_is_rejected() -> None:
    response = TestClient(app).get("/api/v1/autotrender/indices/BANKNIFTY/intraday")
    assert response.status_code == 404
