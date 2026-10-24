from app.services import trade_terminal


def setup_function():
    trade_terminal._cache.clear()
    trade_terminal._dirty.clear()
    trade_terminal._persistence_error = None


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
        "version": 2, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    updated = trade_terminal.update_trade("trade-1", {"status": "CLOSED", "realized_pnl": 120}, event_type="TRADE_LIFECYCLE_CHANGED")
    assert updated["version"] == 3
    assert updated["status"] == "CLOSED"
    assert calls[0][1] == "TRADE_LIFECYCLE_CHANGED"


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
