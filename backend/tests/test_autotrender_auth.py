from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.main import app


def test_autotrender_auth_status_reports_authenticated_session(monkeypatch) -> None:
    auth = MagicMock()
    auth.is_authenticated.return_value = True
    monkeypatch.setattr(app.state, "autotrender_auth", auth, raising=False)

    response = TestClient(app).get("/api/v1/autotrender/auth/status")

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    auth.is_authenticated.assert_called_once_with()


def test_autotrender_login_returns_success_from_auth_provider(monkeypatch) -> None:
    auth = MagicMock()
    auth.login.return_value = {
        "authenticated": True,
        "message": "AutoTrender login successful.",
    }
    monkeypatch.setattr(app.state, "autotrender_auth", auth, raising=False)

    response = TestClient(app).post("/api/v1/autotrender/auth/login")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "message": "AutoTrender login successful.",
    }
    auth.login.assert_called_once_with()


def test_autotrender_login_maps_provider_runtime_error_to_502(monkeypatch) -> None:
    auth = MagicMock()
    auth.login.side_effect = RuntimeError("AutoTrender login failed")
    monkeypatch.setattr(app.state, "autotrender_auth", auth, raising=False)

    response = TestClient(app).post("/api/v1/autotrender/auth/login")

    assert response.status_code == 502
    assert response.json()["detail"] == "AutoTrender login failed"


def test_autotrender_index_price_uses_shared_cache_service(monkeypatch) -> None:
    cache = MagicMock()
    cache.get_index_price.return_value = {
        "data": {"nifty": {"price": "22600.00"}},
        "cache": {"source": "memory", "stale": False},
    }
    monkeypatch.setattr(app.state, "autotrender_cache", cache, raising=False)

    response = TestClient(app).get("/api/v1/autotrender/index-price")

    assert response.status_code == 200
    assert response.json()["data"]["nifty"]["price"] == "22600.00"
    assert response.json()["cache"]["source"] == "memory"
    cache.get_index_price.assert_called_once_with()
