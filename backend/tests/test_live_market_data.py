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


def test_on_connect_applies_desired_subscriptions(monkeypatch):
    calls = []

    class FakeTicker:
        def subscribe(self, tokens, mode="quote"):
            calls.append((list(tokens), mode))

        def close(self):
            pass

    live_market_data._ticker = FakeTicker()
    live_market_data._subscriptions.update({101: "quote", 202: "ltp", 303: "quote"})
    live_market_data._state = "connecting"

    live_market_data._on_state("connected")

    assert ([202], "ltp") in calls
    assert ([101, 303], "quote") in calls


def test_subscribe_does_not_send_to_socket_before_connected(monkeypatch):
    calls = []

    class FakeTicker:
        def subscribe(self, tokens, mode="quote"):
            calls.append((list(tokens), mode))

        def close(self):
            pass

    live_market_data._ticker = FakeTicker()
    live_market_data._state = "connecting"

    result = live_market_data.subscribe([789], mode="quote")

    assert result["subscribed_tokens"] == [789]
    assert calls == []


def test_autotrender_indices_are_registered_as_default_subscriptions(monkeypatch):
    from app.db import session as db_session

    symbols = {
        ("NSE", "NIFTY 50"): 101,
        ("NSE", "NIFTY BANK"): 102,
        ("NSE", "NIFTY FIN SERVICE"): 103,
        ("NSE", "NIFTY MID SELECT"): 104,
        ("BSE", "SENSEX"): 105,
        ("BSE", "BANKEX"): 106,
    }

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params):
            token = symbols.get((params["exchange"], params["symbol"]))
            row = None if token is None else {
                "instrument_token": token,
                "exchange": params["exchange"],
                "tradingsymbol": params["symbol"],
            }
            return Result(row)

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(db_session, "get_engine", lambda: Engine())

    result = live_market_data.subscribe_autotrender_indices()

    assert result["registered"] == 6
    assert result["missing_symbols"] == []
    assert live_market_data.status()["subscribed_tokens"] == [101, 102, 103, 104, 105, 106]
    assert all(item["mode"] == "quote" for item in live_market_data.status()["subscriptions"])


def test_reset_stops_ticker_clears_subscriptions_and_quote_cache(monkeypatch):
    live_market_data.live_quotes.update(123, {"last_price": 42.0})
    live_market_data._subscriptions[123] = "quote"

    live_market_data.reset()

    assert live_market_data.status()["subscribed_tokens"] == []
    assert live_market_data.live_quotes.get(123) is None
    assert live_market_data.status()["state"] == "stopped"
