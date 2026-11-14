from unittest.mock import MagicMock

from app.api import routes
from app.providers.kite.client import KiteAuthenticationError, KiteProviderError
from app.services.kite_sessions import KiteSessionStoreError


def test_session_status_reports_login_required_without_token(monkeypatch):
    monkeypatch.setattr(routes, "get_active_kite_token", lambda: "")

    result = routes.kite_session_status()

    assert result == {
        "authenticated": False,
        "session_configured": False,
        "api_available": False,
        "status": "login_required",
    }


def test_session_status_distinguishes_temporary_api_failure(monkeypatch):
    provider = MagicMock()
    provider.profile.side_effect = KiteProviderError("Kite profile request failed: 502 Bad Gateway")
    monkeypatch.setattr(routes, "get_active_kite_token", lambda: "configured-token")
    monkeypatch.setattr(routes, "get_kite_provider", lambda: provider)

    result = routes.kite_session_status()

    assert result["authenticated"] is None
    assert result["session_configured"] is True
    assert result["api_available"] is False
    assert result["status"] == "api_unavailable"


def test_session_status_reports_expired_session(monkeypatch):
    provider = MagicMock()
    provider.profile.side_effect = KiteAuthenticationError("Kite session validation failed")
    monkeypatch.setattr(routes, "get_active_kite_token", lambda: "rejected-token")
    monkeypatch.setattr(routes, "get_kite_provider", lambda: provider)

    result = routes.kite_session_status()

    assert result["authenticated"] is False
    assert result["session_configured"] is False
    assert result["api_available"] is True
    assert result["status"] == "login_required"


def test_reset_clears_local_session_and_preserves_desired_subscription_registry(monkeypatch):
    calls = []
    monkeypatch.setattr(routes.live_market_data, "invalidate_session", lambda: calls.append("invalidate_socket"))
    monkeypatch.setattr(routes, "clear_active_kite_session", lambda: calls.append("clear_memory_token"))
    monkeypatch.setattr(routes.live_quotes, "clear", lambda: calls.append("clear_quote_cache"))
    monkeypatch.setattr(routes, "reset_sessions", lambda: calls.append("expire_persisted_session"))

    result = routes.reset_kite_session()

    assert calls == [
        "invalidate_socket",
        "clear_memory_token",
        "clear_quote_cache",
        "expire_persisted_session",
    ]
    assert result["status"] == "reset"
    assert result["authenticated"] is False
    assert "Existing broker orders were not changed." in result["message"]


def test_reset_reports_database_failure_after_clearing_runtime_session(monkeypatch):
    calls = []
    monkeypatch.setattr(routes.live_market_data, "invalidate_session", lambda: calls.append("invalidate_socket"))
    monkeypatch.setattr(routes, "clear_active_kite_session", lambda: calls.append("clear_memory_token"))
    monkeypatch.setattr(routes.live_quotes, "clear", lambda: calls.append("clear_quote_cache"))

    def fail_reset():
        raise KiteSessionStoreError("database unavailable")

    monkeypatch.setattr(routes, "reset_sessions", fail_reset)

    try:
        routes.reset_kite_session()
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("Expected reset to report a persistence failure")

    assert calls == ["invalidate_socket", "clear_memory_token", "clear_quote_cache"]
