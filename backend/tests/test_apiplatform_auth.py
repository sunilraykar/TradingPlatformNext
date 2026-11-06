import base64
import json
import time
from unittest.mock import MagicMock

from app.providers.apiplatform.auth import APIPLATFORMAuth, _decode_jwt_expiry, _find_token


def _jwt(expiry: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expiry}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_token_extraction_supports_provider_response_shapes():
    assert _find_token({"token": "abc"}) == "abc"
    assert _find_token({"data": {"access_token": "def"}}) == "def"
    assert _find_token({"data": {"jwt": "ghi"}}) == "ghi"
    assert _find_token({"message": "ok"}) is None


def test_jwt_expiry_is_decoded_without_verifying_signature():
    expiry = int(time.time()) + 3600
    assert _decode_jwt_expiry(_jwt(expiry)) == expiry
    assert _decode_jwt_expiry("not-a-jwt") is None


def test_persisted_session_restores_authorization_header(monkeypatch, tmp_path):
    import app.providers.apiplatform.auth as auth_module

    monkeypatch.setattr(auth_module, "SESSION_FILE", tmp_path / "session.json")
    expiry = time.time() + 3600
    (tmp_path / "session.json").write_text(
        json.dumps({"token": "saved-token", "expires_at": expiry}),
        encoding="utf-8",
    )

    auth = APIPLATFORMAuth()

    assert auth.token == "saved-token"
    assert auth.session.headers["Authorization"] == "Bearer saved-token"
    assert auth.status()["authenticated"] is True
    auth.shutdown()


def test_login_posts_empty_json_with_basic_auth_and_browser_cookies(monkeypatch, tmp_path):
    import app.providers.apiplatform.auth as auth_module

    auth = APIPLATFORMAuth()
    cookies = [{"name": "cf_clearance", "value": "test-clearance", "domain": ".ltpcalculator.com"}]
    monkeypatch.setattr(auth, "_browser_cookies", lambda: (cookies, "test-agent"))
    monkeypatch.setattr(auth_module, "SESSION_FILE", tmp_path / "session.json")
    from types import SimpleNamespace

    monkeypatch.setattr(
        auth_module, "get_settings",
        lambda: SimpleNamespace(apiplatform_basic_auth="Basic test-value", apiplatform_headless=True),
    )

    response = MagicMock()
    response.json.return_value = {"data": {"token": _jwt(int(time.time()) + 3600)}}
    response.raise_for_status.return_value = None
    auth.session.post = MagicMock(return_value=response)

    result = auth.login()

    assert result["authenticated"] is True
    args, kwargs = auth.session.post.call_args
    assert args[0] == auth_module.LOGIN_URL
    assert kwargs["json"] == {}
    assert kwargs["headers"]["authorization"] == "Basic test-value"
    assert kwargs["headers"]["user-agent"] == "test-agent"
    assert auth.session.cookies.get("cf_clearance") == "test-clearance"
    assert auth.session.headers["Authorization"].startswith("Bearer ")
    auth.invalidate()
    auth.shutdown()
