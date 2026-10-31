from unittest.mock import MagicMock

from app.providers.autotrender.auth import (
    API_BASE_URL,
    SESSION_VALIDATION_ENDPOINTS,
    AutoTrenderAuth,
)


def _response(status_code: int, endpoint: str = "/api/telegram"):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.headers = {"Content-Type": "application/json"}
    response.url = API_BASE_URL + endpoint
    response.text = '{"ok": true}'
    response.json.return_value = {"ok": True}
    return response


def test_validate_session_uses_telegram_first_and_stops_on_success() -> None:
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(return_value=_response(200, "/api/telegram"))

    assert auth._validate_session() is True

    auth.session.get.assert_called_once()
    args, kwargs = auth.session.get.call_args
    assert args[0] == API_BASE_URL + "/api/telegram"
    assert kwargs["headers"]["Origin"] == "https://theautotrender.com"
    assert kwargs["headers"]["Referer"] == "https://theautotrender.com/"
    auth.shutdown()


def test_validate_session_falls_back_sequentially_after_endpoint_failure() -> None:
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(
        side_effect=[
            _response(400, "/api/telegram"),
            _response(200, "/api/market-summary"),
        ]
    )

    assert auth._validate_session() is True

    requested_urls = [call.args[0] for call in auth.session.get.call_args_list]
    assert requested_urls == [
        API_BASE_URL + "/api/telegram",
        API_BASE_URL + "/api/market-summary",
    ]
    auth.shutdown()


def test_validate_session_tries_all_endpoints_in_configured_order() -> None:
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(
        side_effect=[_response(400, endpoint) for endpoint in SESSION_VALIDATION_ENDPOINTS]
    )

    assert auth._validate_session() is False

    requested_urls = [call.args[0] for call in auth.session.get.call_args_list]
    assert requested_urls == [
        API_BASE_URL + endpoint for endpoint in SESSION_VALIDATION_ENDPOINTS
    ]
    assert auth.session.cookies.get("authcookie") == "test-cookie"
    assert auth._last_validation_all_rejected is False
    auth.shutdown()


def test_validate_session_clears_cookie_only_when_every_endpoint_rejects(
    monkeypatch, tmp_path
) -> None:
    import app.providers.autotrender.auth as auth_module

    monkeypatch.setattr(auth_module, "SESSION_FILE", tmp_path / "session.json")
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(
        side_effect=[_response(401, endpoint) for endpoint in SESSION_VALIDATION_ENDPOINTS]
    )

    assert auth._validate_session() is False
    assert auth.authenticated is False
    assert auth.session.cookies.get("authcookie") is None
    assert auth._last_validation_all_rejected is True
    auth.shutdown()
