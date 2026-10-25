from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.autotrender_cache_snapshot import _next_run, persist_cache_snapshot

IST = ZoneInfo("Asia/Kolkata")


def test_next_run_uses_1530_ist_and_moves_to_next_day_after_cutoff() -> None:
    before = datetime(2026, 10, 26, 15, 29, tzinfo=IST)
    after = datetime(2026, 10, 26, 15, 31, tzinfo=IST)

    assert _next_run(before) == datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    assert _next_run(after) == datetime(2026, 10, 27, 15, 30, tzinfo=IST)


class FakeCache:
    def get_index_price(self) -> dict:
        return {
            "title": "Index Price",
            "data": {"nifty": {"price": "22600.00"}, "sensex": {"price": "81000.00"}},
            "cache": {"source": "memory", "age_seconds": 0.2},
        }


class FakeResult:
    def __init__(self, row=None) -> None:
        self.row = row

    def first(self):
        return self.row


class FakeConnection:
    def __init__(self, already_saved: bool = False) -> None:
        self.already_saved = already_saved
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if statement.text.startswith("SELECT"):
            return FakeResult((1,) if self.already_saved else None)
        return FakeResult()


class FakeEngine:
    def __init__(self, already_saved: bool = False) -> None:
        self.connection = FakeConnection(already_saved)
        self.transaction = self

    def begin(self):
        return self

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_persist_cache_snapshot_saves_full_payload_without_cache_metadata() -> None:
    engine = FakeEngine()
    result = persist_cache_snapshot(
        FakeCache(),
        captured_at=datetime(2026, 10, 26, 15, 30, tzinfo=IST),
        engine=engine,
    )

    assert result["saved"] is True
    assert result["trading_date"] == "2026-10-26"
    assert len(engine.connection.calls) == 2
    insert_sql, params = engine.connection.calls[1]
    assert "INSERT INTO market_snapshots" in insert_sql
    assert '"nifty"' in params["payload"]
    assert '"sensex"' in params["payload"]
    assert '"cache"' not in params["payload"]
    assert len(params["payload_hash"]) == 32


def test_persist_cache_snapshot_does_not_duplicate_same_date() -> None:
    engine = FakeEngine(already_saved=True)
    result = persist_cache_snapshot(
        FakeCache(),
        captured_at=datetime(2026, 10, 26, 15, 30, tzinfo=IST),
        engine=engine,
    )

    assert result == {
        "saved": False,
        "reason": "already_saved_for_date",
        "trading_date": "2026-10-26",
    }
    assert len(engine.connection.calls) == 1
