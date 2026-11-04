from fastapi.testclient import TestClient

from app.main import app


def test_telegram_config_preflight_allows_vite_origin():
    response = TestClient(app).options(
        "/api/v1/telegram/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


def test_telegram_api_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/telegram/config" in paths
    assert "/api/v1/telegram/test-message" in paths
