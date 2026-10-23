from types import SimpleNamespace

import pytest

from app.services import live_market_data


@pytest.fixture(autouse=True)
def clean_market_data(monkeypatch):
    live_market_data.reset()
    live_market_data._subscriptions.clear()
    monkeypatch.setattr(
        live_market_data,
        "get_settings",
        lambda: SimpleNamespace(kite_api_key="test-key"),
    )
    monkeypatch.setattr(live_market_data, "_active_token", lambda: "")
    yield
    live_market_data.reset()
    live_market_data._subscriptions.clear()


def test_start_without_token_requires_login():
    result = live_market_data.start()

    assert result["state"] == "login_required"
    assert result["connected"] is False


def test_subscribe_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode must be ltp, quote, or full"):
        live_market_data.subscribe([123], mode="invalid")


@pytest.mark.parametrize("tokens", [[], [0], [-1]])
def test_subscribe_rejects_empty_or_non_positive_tokens(tokens):
    with pytest.raises(ValueError, match="positive integer tokens"):
        live_market_data.subscribe(tokens)


def test_subscribe_deduplicates_and_retains_desired_tokens_without_login():
    result = live_market_data.subscribe([123, 123, 456], mode="ltp")

    assert result["state"] == "login_required"
    assert result["subscribed_tokens"] == [123, 456]
    assert result["subscription_count"] == 2


def test_reset_stops_ticker_clears_subscriptions_and_quote_cache(monkeypatch):
    live_market_data.live_quotes.update(123, {"last_price": 42.0})
    live_market_data._subscriptions[123] = "quote"

    live_market_data.reset()

    assert live_market_data.status()["subscribed_tokens"] == []
    assert live_market_data.live_quotes.get(123) is None
    assert live_market_data.status()["state"] == "stopped"
