from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.autotrender_cache_snapshot import (
    INDEX_INSTRUMENTS,
    _fetch_kite_index_prices,
    _next_run,
    persist_cache_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")


def test_next_run_uses_1530_ist_and_moves_to_next_day_after_cutoff() -> None:
    before = datetime(2026, 10, 26, 15, 29, tzinfo=IST)
    after = datetime(2026, 10, 26, 15, 31, tzinfo=IST)

    assert _next_run(before) == datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    assert _next_run(after) == datetime(2026, 10, 27, 15, 30, tzinfo=IST)


class FakeResult:
    def __init__(self, row=None) -> None:
        self.row = row

    def first(self):
        return self.row

    def mappings(self):
        return self


class FakeConnection:
    def __init__(self, already_saved: bool = False, existing_payload=None) -> None:
        self.already_saved = already_saved
        self.existing_payload = existing_payload if existing_payload is not None else {
            "index_price": {"source": "AUTOTRENDER", "data": {"nifty": {"price": "23000"}}}
        }
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if statement.text.startswith("SELECT snapshot_id"):
            return FakeResult(
                {"snapshot_id": 1, "payload": self.existing_payload} if self.already_saved else None
            )
        if "FROM instruments" in statement.text:
            exchange, symbol = params["exchange"], params["symbol"]
            index = next(key for key, value in INDEX_INSTRUMENTS.items() if value[:2] == (exchange, symbol))
            token = list(INDEX_INSTRUMENTS).index(index) + 100
            return FakeResult({
                "instrument_token": token,
                "exchange": exchange,
                "tradingsymbol": symbol,
            })
        return FakeResult()


class FakeEngine:
    def __init__(self, already_saved: bool = False, existing_payload=None) -> None:
        self.connection = FakeConnection(already_saved, existing_payload)

    def connect(self):
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeKiteProvider:
    def __init__(self):
        self.requested = []

    def quotes(self, instruments):
        self.requested = list(instruments)
        return {
            instrument: {
                "last_price": 22000.0 + index,
                "ohlc": {"open": 21900.0, "high": 22100.0, "low": 21800.0, "close": 21950.0},
            }
            for index, instrument in enumerate(instruments)
        }


def test_fetch_kite_index_prices_requests_all_six_spot_indices() -> None:
    engine = FakeEngine()
    provider = FakeKiteProvider()

    result = _fetch_kite_index_prices(engine, provider)

    assert len(provider.requested) == 6
    assert result["source"] == "KITE"
    assert set(result["data"]) == {"nifty", "banknifty", "finnifty", "midcpnifty", "sensex", "bankex"}
    assert result["data"]["nifty"]["price"] == 22000.0


def test_persist_cache_snapshot_saves_kite_prices_without_autotrend_index_price() -> None:
    engine = FakeEngine()
    provider = FakeKiteProvider()
    result = persist_cache_snapshot(
        captured_at=datetime(2026, 10, 26, 15, 30, tzinfo=IST),
        engine=engine,
        kite_provider=provider,
    )

    assert result["saved"] is True
    assert result["index_price_source"] == "KITE"
    insert_sql, params = engine.connection.calls[-1]
    assert "INSERT INTO market_snapshots" in insert_sql
    assert '"source":"KITE"' in params["payload"]
    assert '"nifty"' in params["payload"]
    assert '"bankex"' in params["payload"]
    assert len(params["payload_hash"]) == 32


def test_persist_cache_snapshot_repairs_legacy_snapshot_in_place() -> None:
    engine = FakeEngine(already_saved=True)
    result = persist_cache_snapshot(
        captured_at=datetime(2026, 10, 26, 15, 30, tzinfo=IST),
        engine=engine,
        kite_provider=FakeKiteProvider(),
    )

    assert result["saved"] is True
    assert result["repaired"] is True
    update_sql, params = engine.connection.calls[-1]
    assert "UPDATE market_snapshots" in update_sql
    assert params["snapshot_id"] == 1
    assert '"source":"KITE"' in params["payload"]


def test_persist_cache_snapshot_does_not_replace_complete_kite_snapshot() -> None:
    complete = {
        "index_price": {
            "source": "KITE",
            "data": {key: {"price": 1} for key in ("nifty", "banknifty", "finnifty", "midcpnifty", "sensex", "bankex")},
        }
    }
    engine = FakeEngine(already_saved=True, existing_payload=complete)
    result = persist_cache_snapshot(
        captured_at=datetime(2026, 10, 26, 15, 30, tzinfo=IST),
        engine=engine,
        kite_provider=FakeKiteProvider(),
    )

    assert result["saved"] is False
    assert result["reason"] == "already_saved_for_date"
    assert not any("UPDATE market_snapshots" in sql for sql, _ in engine.connection.calls)
