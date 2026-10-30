from unittest.mock import MagicMock

from app.providers.autotrender.auth import API_BASE_URL, AutoTrenderAuth


def _response(status_code: int):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.headers = {"Content-Type": "application/json"}
    response.url = API_BASE_URL + "/api/market-summary"
    response.text = '{"ok": true}'
    response.json.return_value = {"ok": True}
    return response


def test_validate_session_uses_market_summary_endpoint() -> None:
    auth = AutoTrenderAuth()
    auth.authenticated = True
    response = _response(200)
    auth.session.get = MagicMock(return_value=response)

    assert auth._validate_session() is True

    args, kwargs = auth.session.get.call_args
    assert args[0] == API_BASE_URL + "/api/market-summary"
    assert kwargs["headers"]["Origin"] == "https://theautotrender.com"
    assert kwargs["headers"]["Referer"] == "https://theautotrender.com/"
    auth.shutdown()


def test_validate_session_marks_unauthorized_cookie_invalid(monkeypatch, tmp_path) -> None:
    import app.providers.autotrender.auth as auth_module

    monkeypatch.setattr(auth_module, "SESSION_FILE", tmp_path / "session.json")
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(return_value=_response(401))

    assert auth._validate_session() is False
    assert auth.authenticated is False
    assert auth.session.cookies.get("authcookie") is None
    auth.shutdown()


def test_validate_session_does_not_classify_bad_request_as_auth_rejection() -> None:
    auth = AutoTrenderAuth()
    auth.authenticated = True
    auth.session.cookies.set("authcookie", "test-cookie")
    auth.session.get = MagicMock(return_value=_response(400))

    assert auth._validate_session() is False
    # A 400 is a failed validation request, but not proof of an invalid cookie.
    assert auth.session.cookies.get("authcookie") == "test-cookie"
    auth.shutdown()
