from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.providers.apiplatform.client import APIPLATFORMClient


def test_fetch_option_chain_uses_browser_expiry_format_and_parameter_names():
    auth = MagicMock()
    response = MagicMock()
    response.json.return_value = {"OptionChain": []}
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth)

    result = client.fetch_option_chain("NIFTY", "2026-11-09", 65)

    assert result == {"OptionChain": []}
    args, kwargs = auth.session.request.call_args
    assert args == ("GET", "https://apiplatform.ltpcalculator.com/optionChain/fetch-data")
    assert kwargs["params"] == {
        "symbol": "NIFTY",
        "expiry": "09-11-2026",
        "lotSize": 65,
    }
    auth.ensure_authenticated.assert_called_once()
    response.raise_for_status.assert_called_once()


def test_fetch_option_chain_preserves_browser_expiry_format():
    auth = MagicMock()
    response = MagicMock()
    response.json.return_value = {"OptionChain": []}
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth)

    client.fetch_option_chain("NIFTY", "09-11-2026", 65)

    assert auth.session.request.call_args.kwargs["params"]["expiry"] == "09-11-2026"


def test_fetch_option_chain_rejects_unsupported_expiry_format():
    auth = MagicMock()
    client = APIPLATFORMClient(auth)

    with pytest.raises(ValueError, match="Expiry must use"):
        client.fetch_option_chain("NIFTY", "09/11/2026", 65)

    auth.session.request.assert_not_called()


def test_client_uses_configured_base_url():
    auth = MagicMock()
    response = MagicMock(status_code=200)
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth, base_url="https://example.test/api/")

    client.get("/health")

    args, _kwargs = auth.session.request.call_args
    assert args == ("GET", "https://example.test/api/health")


def test_registry_uses_base_url_from_application_settings(monkeypatch):
    from app.providers.apiplatform import registry

    auth = MagicMock()
    monkeypatch.setattr(
        registry,
        "get_settings",
        lambda: SimpleNamespace(apiplatform_base_url="https://configured.example/api"),
    )
    monkeypatch.setattr(registry, "APIPLATFORMAuth", lambda: auth)

    client = registry.configure_apiplatform()

    try:
        assert client.base_url == "https://configured.example/api/"
    finally:
        registry.shutdown_apiplatform()


def test_client_reauthenticates_once_after_unauthorized_response():
    auth = MagicMock()
    first = MagicMock(status_code=401)
    second = MagicMock(status_code=200)
    auth.session.request.side_effect = [first, second]
    auth.token = "old-token"
    client = APIPLATFORMClient(auth)

    assert client.get("/health") is second
    auth.invalidate.assert_called_once_with(expected_token="old-token")
    auth.login.assert_called_once()
    assert auth.session.request.call_count == 2


def test_fetch_all_symbols_uses_live_type_and_authenticated_client():
    auth = MagicMock()
    response = MagicMock()
    response.json.return_value = {"symbols": [{"symbol": "NIFTY", "lotSize": 65}]}
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth)

    result = client.fetch_all_symbols()

    assert result == {"symbols": [{"symbol": "NIFTY", "lotSize": 65}]}
    args, kwargs = auth.session.request.call_args
    assert args == ("GET", "https://apiplatform.ltpcalculator.com/optionChain/all-symbol")
    assert kwargs["params"] == {"type": "LIVE"}
    auth.ensure_authenticated.assert_called_once()
    response.raise_for_status.assert_called_once()


def test_fetch_all_symbols_rejects_empty_response():
    auth = MagicMock()
    response = MagicMock()
    response.json.return_value = []
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth)

    with pytest.raises(Exception, match="empty or unexpected payload"):
        client.fetch_all_symbols()
