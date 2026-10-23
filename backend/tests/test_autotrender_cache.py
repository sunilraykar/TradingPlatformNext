from __future__ import annotations

from typing import Any

from app.services.autotrender_cache import AutoTrenderCache


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeAuth:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def get(self, endpoint: str) -> FakeResponse:
        assert endpoint == "/api/index-price"
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream unavailable")
        return FakeResponse({"data": {"nifty": {"price": "22600.00"}}})


def test_index_price_is_cached_in_memory() -> None:
    auth = FakeAuth()
    cache = AutoTrenderCache(auth, ttl_seconds=30)

    first = cache.get_index_price()
    second = cache.get_index_price()

    assert auth.calls == 1
    assert first["data"]["nifty"]["price"] == "22600.00"
    assert second["cache"]["source"] == "memory"
    assert second["cache"]["stale"] is False


def test_stale_data_is_returned_when_refresh_fails() -> None:
    auth = FakeAuth()
    cache = AutoTrenderCache(auth, ttl_seconds=0.1)
    cache.get_index_price()
    auth.fail = True
    cache._fetched_at = 0  # force the cached entry to expire without sleeping

    result = cache.get_index_price()

    assert result["cache"]["stale"] is True
    assert "upstream unavailable" in result["cache"]["last_refresh_error"]
