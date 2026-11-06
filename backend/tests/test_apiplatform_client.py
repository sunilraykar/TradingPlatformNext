from unittest.mock import MagicMock

from app.providers.apiplatform.client import APIPLATFORMClient


def test_fetch_option_chain_uses_expected_endpoint_and_parameter_names():
    auth = MagicMock()
    response = MagicMock()
    response.json.return_value = {"OptionChain": []}
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth)

    result = client.fetch_option_chain("NIFTY", "2026-11-12", 65)

    assert result == {"OptionChain": []}
    args, kwargs = auth.session.request.call_args
    assert args == ("GET", "https://apiplatform.ltpcalculator.com/optionChain/fetch-data")
    assert kwargs["params"] == {
        "symbol": "NIFTY",
        "expiry": "2026-11-12",
        "lotSize": 65,
    }
    auth.ensure_authenticated.assert_called_once()
    response.raise_for_status.assert_called_once()


def test_client_uses_configured_base_url():
    auth = MagicMock()
    response = MagicMock(status_code=200)
    auth.session.request.return_value = response
    client = APIPLATFORMClient(auth, base_url="https://example.test/api/")

    client.get("/health")

    args, _kwargs = auth.session.request.call_args
    assert args == ("GET", "https://example.test/api/health")


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
