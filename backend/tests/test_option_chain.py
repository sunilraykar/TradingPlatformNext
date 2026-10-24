import pytest

from app.services.option_chain import get_option_chain


@pytest.mark.parametrize("underlying", ["", "SENSEX", "niftybank"])
def test_option_chain_rejects_unknown_underlying(underlying):
    with pytest.raises(ValueError, match="underlying must be NIFTY or BANKNIFTY"):
        get_option_chain(underlying)


@pytest.mark.parametrize("strike_count", [0, 31, -1])
def test_option_chain_rejects_invalid_strike_count(strike_count):
    with pytest.raises(ValueError, match="strike_count must be between 1 and 30"):
        get_option_chain("NIFTY", strike_count=strike_count)


def test_option_chain_rejects_invalid_expiry_before_database_access():
    with pytest.raises(ValueError, match="expiry must use YYYY-MM-DD format"):
        get_option_chain("BANKNIFTY", expiry="next Thursday")


def test_option_chain_uses_full_mode_for_open_interest(monkeypatch):
    from datetime import date

    from app.services import live_market_data, option_chain
    from app.services.live_quotes import live_quotes

    expiry = date(2026, 10, 29)
    index_row = {"instrument_token": 999, "tradingsymbol": "NIFTY 50"}
    option_rows = [
        {
            "instrument_token": 1,
            "tradingsymbol": "NIFTY262925100CE",
            "instrument_type": "CE",
            "strike": 25100,
            "expiry": expiry,
            "lot_size": 65,
        },
        {
            "instrument_token": 2,
            "tradingsymbol": "NIFTY262925100PE",
            "instrument_type": "PE",
            "strike": 25100,
            "expiry": expiry,
            "lot_size": 65,
        },
    ]

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def scalars(self):
            return self

        def first(self):
            return self.rows[0] if self.rows else None

        def all(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params):
            sql = str(statement)
            if "SELECT instrument_token, tradingsymbol FROM instruments" in sql:
                return Result([index_row])
            if "SELECT DISTINCT expiry" in sql:
                return Result([expiry])
            return Result(option_rows)

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(option_chain, "get_engine", lambda: Engine())
    monkeypatch.setattr(option_chain, "_quote_price", lambda _token: 25100.0)
    quote_values = {
        1: {
            "last_price": 125.5,
            "volume_traded": 1000,
            "oi": 25000,
            "received_at": "2026-10-24T10:00:00+00:00",
        },
        2: {
            "last_price": 130.0,
            "volume_traded": 1200,
            "oi": 30000,
            "received_at": "2026-10-24T10:00:00+00:00",
        },
    }
    monkeypatch.setattr(live_quotes, "get", lambda token: quote_values.get(token))
    calls = []

    def fake_subscribe(tokens, mode="quote"):
        calls.append((tokens, mode))
        return {"state": "connected", "subscription_count": len(tokens)}

    monkeypatch.setattr(live_market_data, "subscribe", fake_subscribe)

    result = option_chain.get_option_chain("NIFTY", expiry=expiry.isoformat(), strike_count=1)

    assert calls == [([1, 2], "full")]
    assert result["strikes"][0]["CE"]["oi"] == 25000
    assert result["strikes"][0]["PE"]["oi"] == 30000
