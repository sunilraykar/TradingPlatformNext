from app.services import trade_terminal


def setup_function():
    trade_terminal._cache.clear()
    trade_terminal._dirty.clear()
    trade_terminal._persistence_error = None


def test_database_payload_excludes_live_quote_and_subscription_cache_fields():
    trade = {
        "trade_id": "trade-cache-only",
        "execution_mode": "PAPER",
        "instrument_symbol": "NIFTY26OCT25000CE",
        "status": "OPEN",
        "version": 1,
        "created_at": "2026-10-24T09:00:00+00:00",
        "updated_at": "2026-10-24T09:00:00+00:00",
        "last_price": 105.0,
        "unrealized_pnl": 250.0,
        "last_market_data_at": "2026-10-24T09:00:01+00:00",
        "quote_state": "LIVE",
        "subscription_mode": "quote",
        "subscribed": True,
    }

    row = trade_terminal._db_row(trade)
    payload = row["payload"]

    assert '"last_price"' not in payload
    assert '"unrealized_pnl"' not in payload
    assert '"last_market_data_at"' not in payload
    assert '"quote_state"' not in payload
    assert '"subscription_mode"' not in payload
    assert '"subscribed"' not in payload
    assert '"instrument_symbol"' in payload


def test_market_updates_do_not_persist(monkeypatch):
    trade_terminal._cache["trade-1"] = {
        "trade_id": "trade-1", "execution_mode": "PAPER", "status": "OPEN",
        "version": 1, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: calls.append(args))
    result = trade_terminal.update_market_state("trade-1", last_price=101.5, unrealized_pnl=15)
    assert result["last_price"] == 101.5
    assert result["unrealized_pnl"] == 15
    assert calls == []


def test_quote_updates_matching_open_trade_in_memory_and_publishes(monkeypatch):
    trade_terminal._cache["trade-quote"] = {
        "trade_id": "trade-quote", "execution_mode": "PAPER", "status": "OPEN",
        "instrument_token": 123, "direction": "BUY", "quantity": 10,
        "entry_price": 100, "unrealized_pnl": 0,
    }
    persisted = []
    published = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: persisted.append(args))
    monkeypatch.setattr(trade_terminal, "publish", lambda event: published.append(event))

    count = trade_terminal.apply_quote_to_open_trades({
        "instrument_token": 123, "last_price": 105, "received_at": "2026-10-24T09:00:00+00:00",
    })

    trade = trade_terminal.get_trade("trade-quote")
    assert count == 1
    assert trade["last_price"] == 105
    assert trade["unrealized_pnl"] == 50
    assert trade["quote_state"] == "LIVE"
    assert persisted == []
    assert published[-1]["type"] == "trade_quote"


def test_create_trade_is_paper_only_and_persists(monkeypatch):
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append((trade.copy(), event, payload)))
    trade = trade_terminal.create_trade({
        "execution_mode": "PAPER", "source": "MANUAL", "instrument_symbol": "NIFTY26OCT25000CE",
        "direction": "BUY", "quantity": 50,
    })
    assert trade["trade_id"]
    assert trade["execution_mode"] == "PAPER"
    assert trade["status"] == "OPEN"
    assert len(calls) == 1
    assert calls[0][1] == "TRADE_CREATED"


def test_live_creation_is_blocked(monkeypatch):
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: None)
    try:
        trade_terminal.create_trade({
            "execution_mode": "LIVE", "instrument_symbol": "NIFTY26OCT25000CE",
            "direction": "BUY", "quantity": 50,
        })
        assert False, "Live creation must be blocked"
    except ValueError as exc:
        assert "read-only" in str(exc)


def test_lifecycle_update_increments_version_and_persists(monkeypatch):
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append((trade.copy(), event, payload)))
    trade_terminal._cache["trade-1"] = {
        "trade_id": "trade-1", "execution_mode": "PAPER", "status": "OPEN",
        "direction": "BUY", "quantity": 10, "open_quantity": 10, "entry_price": 100,
        "version": 2, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    updated = trade_terminal.update_trade("trade-1", {
        "status": "CLOSED", "exit_price": 112, "average_exit_price": 112,
        "closed_quantity": 10, "open_quantity": 0, "realized_pnl": 999,
    }, event_type="TRADE_LIFECYCLE_CHANGED")
    assert updated["version"] == 3
    assert updated["status"] == "CLOSED"
    assert updated["closed_quantity"] == 10
    assert updated["realized_pnl"] == 120
    assert calls[0][1] == "TRADE_LIFECYCLE_CHANGED"


def test_backend_calculates_realized_pnl_for_closed_sell(monkeypatch):
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: None)
    trade_terminal._cache["trade-sell-close"] = {
        "trade_id": "trade-sell-close", "execution_mode": "PAPER", "status": "OPEN",
        "direction": "SELL", "quantity": 25, "open_quantity": 25, "entry_price": 200,
        "version": 1, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    updated = trade_terminal.update_trade("trade-sell-close", {
        "status": "CLOSED", "exit_price": 180, "average_exit_price": 180,
        "closed_quantity": 25, "open_quantity": 0, "realized_pnl": -500,
    })
    assert updated["realized_pnl"] == 500


def test_backend_rejects_close_without_valid_prices_or_quantity(monkeypatch):
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: None)
    trade_terminal._cache["trade-invalid-close"] = {
        "trade_id": "trade-invalid-close", "execution_mode": "PAPER", "status": "OPEN",
        "direction": "BUY", "quantity": 10, "open_quantity": 10,
        "version": 1, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    try:
        trade_terminal.update_trade("trade-invalid-close", {"status": "CLOSED", "realized_pnl": 0})
        assert False, "Closing without valid entry and exit prices must fail"
    except ValueError as exc:
        assert "valid entry price" in str(exc)


def test_dirty_trade_retries(monkeypatch):
    trade_terminal._cache["trade-1"] = {
        "trade_id": "trade-1", "execution_mode": "PAPER", "status": "OPEN",
        "version": 1, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    trade_terminal._dirty.add("trade-1")
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append(trade["trade_id"]))
    result = trade_terminal.retry_dirty()
    assert result["persisted"] == 1
    assert calls == ["trade-1"]


def test_live_position_reconciliation_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append((trade["trade_id"], event)))
    position = {
        "exchange": "NFO", "tradingsymbol": "NIFTY26OCT25000CE",
        "product": "MIS", "quantity": 50, "average_price": 100, "last_price": 105,
        "pnl": 250,
    }
    first = trade_terminal.reconcile_live_positions([position])
    second = trade_terminal.reconcile_live_positions([position])
    assert first["created"] == 1
    assert second["created"] == 0
    assert len([t for t in trade_terminal._cache.values() if t.get("source") == "KITE_POSITION"]) == 1
    assert len(calls) == 1
    trade = next(t for t in trade_terminal._cache.values() if t.get("source") == "KITE_POSITION")
    assert trade["execution_mode"] == "LIVE"
    assert trade["broker_pnl"] == 250


def test_flattened_live_position_is_durably_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append((trade.copy(), event)))
    position = {"exchange": "NFO", "tradingsymbol": "NIFTY26OCT25000CE", "product": "MIS", "quantity": 50}
    trade_terminal.reconcile_live_positions([position])
    result = trade_terminal.reconcile_live_positions([])
    trade = next(t for t in trade_terminal._cache.values() if t.get("source") == "KITE_POSITION")
    assert result["closed"] == 1
    assert trade["status"] == "CLOSED"
    assert trade["open_quantity"] == 0
    assert calls[-1][1] == "BROKER_POSITION_FLATTENED"


def test_create_trade_is_idempotent_for_same_request_key(monkeypatch):
    calls = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: calls.append(event))
    payload = {
        "idempotency_key": "manual-trade-request-001",
        "execution_mode": "PAPER", "source": "MANUAL",
        "instrument_symbol": "NIFTY26OCT25000CE", "direction": "BUY", "quantity": 50,
    }
    first = trade_terminal.create_trade(payload)
    second = trade_terminal.create_trade(payload)
    assert first["trade_id"] == second["trade_id"]
    assert len([t for t in trade_terminal._cache.values() if t.get("idempotency_key") == payload["idempotency_key"]]) == 1
    assert calls == ["TRADE_CREATED"]

def test_restore_open_trade_preserves_and_applies_existing_cached_quote(monkeypatch):
    """Restoring subscriptions must not wipe a valid LTP already in memory."""
    import app.services.trade_terminal as terminal
    from app.services.live_quotes import live_quotes

    live_quotes.clear()
    terminal._cache.clear()
    terminal._dirty.clear()
    terminal._cache["trade-cached-ltp"] = {
        "trade_id": "trade-cached-ltp",
        "execution_mode": "PAPER",
        "status": "OPEN",
        "instrument_symbol": "INFY",
        "exchange": "NSE",
        "instrument_token": 408065,
        "last_price": 1153.80,
        "direction": "BUY",
        "quantity": 1,
        "entry_price": 1100,
        "unrealized_pnl": 53.80,
    }
    live_quotes.update(408065, {"last_price": 1153.80})

    class Result:
        def first(self):
            return (408065,)

    class Connection:
        def execute(self, *args, **kwargs):
            return Result()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(terminal, "get_engine", lambda: Engine())
    monkeypatch.setattr(terminal.live_market_data, "subscribe", lambda *args, **kwargs: {"state": "connected"})
    monkeypatch.setattr(terminal, "publish", lambda event: None)

    result = terminal.subscribe_open_trades()
    restored = terminal.get_trade("trade-cached-ltp")

    assert result == {"subscribed": 1, "unresolved": 0}
    assert restored["instrument_token"] == 408065
    assert restored["last_price"] == 1153.80
    assert restored["quote_state"] == "LIVE"
    assert abs(restored["unrealized_pnl"] - 53.80) < 1e-9

    monkeypatch.undo()
    live_quotes.clear()
    terminal._cache.clear()
    terminal._dirty.clear()


def test_paper_market_close_rejects_stale_quote(monkeypatch):
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade_terminal, "publish", lambda event: None)
    trade_terminal._cache["stale-close"] = {
        "trade_id": "stale-close",
        "execution_mode": "PAPER",
        "status": "OPEN",
        "direction": "BUY",
        "quantity": 10,
        "open_quantity": 10,
        "entry_price": 100.0,
        "average_entry_price": 100.0,
        "last_price": 105.0,
        "last_market_data_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
    }

    import pytest
    with pytest.raises(ValueError, match="no valid quote from the last 30 seconds"):
        trade_terminal.update_trade("stale-close", {
            "status": "CLOSED",
            "exit_price": 100.0,
            "average_exit_price": 100.0,
            "closed_quantity": 10,
            "open_quantity": 0,
            "exit_method": "PAPER_MARKET_SIMULATION",
        })


def test_paper_market_close_uses_fresh_backend_quote_for_realized_pnl(monkeypatch):
    from datetime import UTC, datetime

    persisted = []
    monkeypatch.setattr(trade_terminal, "_persist", lambda trade, event, payload: persisted.append((trade.copy(), event)))
    monkeypatch.setattr(trade_terminal, "publish", lambda event: None)
    trade_terminal._cache["fresh-close"] = {
        "trade_id": "fresh-close",
        "execution_mode": "PAPER",
        "status": "OPEN",
        "direction": "BUY",
        "quantity": 10,
        "open_quantity": 10,
        "entry_price": 100.0,
        "average_entry_price": 100.0,
        "last_price": 112.5,
        "last_market_data_at": datetime.now(UTC).isoformat(),
    }

    closed = trade_terminal.update_trade("fresh-close", {
        "status": "CLOSED",
        "exit_price": 100.0,
        "average_exit_price": 100.0,
        "closed_quantity": 10,
        "open_quantity": 0,
        "exit_method": "PAPER_MARKET_SIMULATION",
    })

    assert closed["exit_price"] == 112.5
    assert closed["average_exit_price"] == 112.5
    assert closed["closed_quantity"] == 10
    assert closed["realized_pnl"] == 125.0
    assert persisted and persisted[-1][1] == "TRADE_UPDATED"
