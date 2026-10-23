from fastapi.testclient import TestClient

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
