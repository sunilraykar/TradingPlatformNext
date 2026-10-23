from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app


def test_health_endpoint():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_kite_login_url_requires_api_key(monkeypatch):
    from app.api import routes
    from app.core.config import Settings

    monkeypatch.setattr(routes, "get_settings", lambda: Settings(kite_api_key=""))
    response = TestClient(app).get("/api/v1/kite/login-url")
    assert response.status_code == 503


def test_database_health_success():
    mock_connection = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_connection

    with patch("app.api.routes.get_engine", return_value=mock_engine):
        response = TestClient(app).get("/api/v1/health/db")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
    }
    mock_connection.execute.assert_called_once()


def test_database_health_failure():
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = OperationalError(
        "SELECT 1", {}, Exception("connection failed")
    )

    with patch("app.api.routes.get_engine", return_value=mock_engine):
        response = TestClient(app).get("/api/v1/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
