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


def test_notification_status_route_is_registered():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/v1/notifications/status" in paths


def test_notification_status_preflight_allows_vite_origin():
    response = TestClient(app).options(
        "/api/v1/notifications/status",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_notification_dispatch_test_route_is_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/notifications/test-dispatch" in paths


def test_notification_dispatch_test_preflight_allows_vite_origin():
    response = TestClient(app).options(
        "/api/v1/notifications/test-dispatch",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]
