from app.services.live_quotes import LiveQuoteCache


def test_live_quote_cache_updates_and_returns_copy():
    cache = LiveQuoteCache()
    cache.update(256265, {"last_price": 22000.5})
    result = cache.get(256265)
    assert result is not None
    assert result["instrument_token"] == 256265
    assert result["last_price"] == 22000.5
    result["last_price"] = -1
    assert cache.get(256265)["last_price"] == 22000.5


def test_missing_quote_returns_none():
    assert LiveQuoteCache().get(1) is None
