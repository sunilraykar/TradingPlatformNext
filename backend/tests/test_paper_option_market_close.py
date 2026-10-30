from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import paper_option_market_close as worker

IST = ZoneInfo("Asia/Kolkata")
CLOSE = datetime(2026, 10, 26, 15, 27, 10, tzinfo=IST)


def _trade(**changes):
    trade = {
        "trade_id": "paper-1",
        "execution_mode": "PAPER",
        "status": "OPEN",
        "exchange": "NFO",
        "instrument_symbol": "NIFTY26OCT25000CE",
        "instrument_type": "CE",
        "direction": "BUY",
        "quantity": 50,
        "open_quantity": 50,
        "entry_price": 100,
        "last_price": 90,
        "last_market_data_at": "2026-10-26T15:26:30+05:30",
    }
    trade.update(changes)
    return trade


def test_market_close_closes_losing_paper_option(monkeypatch):
    trade = _trade()
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [trade])
    monkeypatch.setattr(worker.trade_terminal, "get_trade", lambda trade_id: trade)
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["enabled"] is True
    assert result["closed"] == ["paper-1"]
    changes = calls[0][0][1]
    assert changes["status"] == "CLOSED"
    assert changes["open_quantity"] == 0
    assert changes["exit_price"] == 90
    assert changes["auto_exit_reason"] == "MARKET_CLOSE"
    assert changes["exit_method"] == "PAPER_MARKET_CLOSE_SIMULATION"
    assert calls[0][1]["event_type"] == "PAPER_OPTION_MARKET_CLOSE"


def test_market_close_skips_live_trade(monkeypatch):
    live = _trade(execution_mode="LIVE", trade_id="live-1")
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [live])
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["closed"] == []
    assert calls == []


def test_market_close_skips_stale_quote(monkeypatch):
    trade = _trade(last_market_data_at="2026-10-26T15:20:00+05:30")
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [trade])
    monkeypatch.setattr(worker.trade_terminal, "get_trade", lambda trade_id: trade)
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["closed"] == []
    assert result["skipped"] == [{"trade_id": "paper-1", "reason": "missing_or_stale_quote"}]
    assert calls == []


def test_market_close_does_not_run_outside_window(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not inspect trades")))

    result = worker.run_market_close_cycle(datetime(2026, 10, 26, 15, 31, tzinfo=IST))

    assert result["enabled"] is False
    assert result["reason"] == "outside_market_close_window"


def test_market_close_uses_latest_trade_snapshot_for_exit_and_pnl(monkeypatch):
    listed = _trade(last_price=90)
    current = _trade(last_price=95, last_market_data_at="2026-10-26T15:27:00+05:30")
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [listed])
    monkeypatch.setattr(worker.trade_terminal, "get_trade", lambda trade_id: current)
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["closed"] == ["paper-1"]
    changes = calls[0][0][1]
    assert changes["exit_price"] == 95
    assert changes["gross_realized_pnl"] == -250


def test_market_close_skips_if_quote_changes_before_final_validation(monkeypatch):
    listed = _trade(last_price=90)
    first_read = _trade(last_price=95, last_market_data_at="2026-10-26T15:27:00+05:30")
    changed_read = _trade(last_price=96, last_market_data_at="2026-10-26T15:27:05+05:30")
    reads = iter([first_read, changed_read])
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [listed])
    monkeypatch.setattr(worker.trade_terminal, "get_trade", lambda trade_id: next(reads))
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["closed"] == []
    assert result["skipped"] == [{"trade_id": "paper-1", "reason": "quote_changed_during_close"}]
    assert calls == []


def test_market_close_rechecks_current_trade_mode_and_status(monkeypatch):
    live = _trade(execution_mode="LIVE")
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [_trade()])
    monkeypatch.setattr(worker.trade_terminal, "get_trade", lambda trade_id: live)
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_market_close_cycle(CLOSE)

    assert result["closed"] == []
    assert calls == []


def test_market_close_skips_weekends(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not inspect trades")))

    result = worker.run_market_close_cycle(datetime(2026, 10, 25, 15, 27, tzinfo=IST))

    assert result["enabled"] is False
    assert result["reason"] == "outside_market_close_window"


def test_trade_terminal_close_guard_is_atomic(monkeypatch):
    from app.services import trade_terminal

    trade_id = "market-close-guard"
    monkeypatch.setitem(
        trade_terminal._cache,
        trade_id,
        {
            "trade_id": trade_id,
            "execution_mode": "PAPER",
            "status": "OPEN",
            "last_price": 96,
            "last_market_data_at": "2026-10-26T15:27:05+05:30",
            "quantity": 50,
            "open_quantity": 50,
            "version": 1,
        },
    )
    monkeypatch.setattr(trade_terminal, "_persist", lambda *args, **kwargs: None)

    import pytest
    with pytest.raises(ValueError, match="changed before update"):
        trade_terminal.update_trade(
            trade_id,
            {"status": "CLOSED", "open_quantity": 0},
            event_type="PAPER_OPTION_MARKET_CLOSE",
            expected_quote={
                "status": "OPEN",
                "execution_mode": "PAPER",
                "last_price": 95,
                "last_market_data_at": "2026-10-26T15:27:00+05:30",
            },
        )

    assert trade_terminal.get_trade(trade_id)["status"] == "OPEN"
