from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services import auto_exit_profitable_trades as worker

IST = ZoneInfo("Asia/Kolkata")
CLOSE = datetime(2026, 10, 26, 15, 30, 10, tzinfo=IST)


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
        "last_price": 110,
        "last_market_data_at": "2026-10-26T15:29:00+05:30",
    }
    trade.update(changes)
    return trade


def test_zerodha_option_charges_include_round_trip_order_costs():
    charges = worker.calculate_zerodha_option_charges(
        exchange="NFO",
        buy_premium=Decimal(100),
        sell_premium=Decimal(110),
        quantity=50,
    )
    assert charges["brokerage"] == Decimal("40.00")
    assert charges["stt"] == Decimal("8.25")
    assert charges["exchange_transaction_charges"] > Decimal(0)
    assert charges["sebi_charges"] > Decimal(0)
    assert charges["stamp_duty"] == Decimal("0.15")
    assert charges["gst"] > Decimal(0)
    assert charges["total"] > charges["brokerage"]


def test_auto_exit_closes_only_net_profitable_paper_trade(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [_trade()])
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = worker.run_auto_exit_cycle(CLOSE)

    assert result["enabled"] is True
    assert result["closed"] == ["paper-1"]
    assert calls[0][0][1]["status"] == "CLOSED"
    assert calls[0][0][1]["open_quantity"] == 0
    assert calls[0][0][1]["net_realized_pnl"] > 0
    assert calls[0][1]["event_type"] == "AUTO_EXIT_PROFITABLE_TRADE"


def test_auto_exit_leaves_trade_open_when_net_profit_is_not_positive(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [_trade(last_price=101)])
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_auto_exit_cycle(CLOSE)

    assert result["closed"] == []
    assert result["skipped"] == [{"trade_id": "paper-1", "reason": "net_pnl_not_positive"}]
    assert calls == []


def test_auto_exit_does_nothing_when_setting_disabled(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: False)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not inspect trades")))

    result = worker.run_auto_exit_cycle(CLOSE)

    assert result["enabled"] is False
    assert result["reason"] == "setting_disabled_or_unavailable"


def test_live_trades_are_never_auto_closed(monkeypatch):
    live_trade = _trade(execution_mode="LIVE", trade_id="live-1")
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(worker.trade_terminal, "list_trades", lambda **kwargs: [live_trade])
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_auto_exit_cycle(CLOSE)

    assert result["closed"] == []
    assert calls == []


def test_stale_quotes_are_not_used_for_auto_exit(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    monkeypatch.setattr(
        worker.trade_terminal,
        "list_trades",
        lambda **kwargs: [_trade(last_market_data_at="2026-10-26T15:20:00+05:30")],
    )
    calls = []
    monkeypatch.setattr(worker.trade_terminal, "update_trade", lambda *args, **kwargs: calls.append(args))

    result = worker.run_auto_exit_cycle(CLOSE)

    assert result["closed"] == []
    assert result["skipped"] == [{"trade_id": "paper-1", "reason": "stale_or_missing_quote"}]
    assert calls == []


def test_auto_exit_runs_only_during_market_close_window(monkeypatch):
    monkeypatch.setattr(worker, "_enabled", lambda: True)
    result = worker.run_auto_exit_cycle(datetime(2026, 10, 26, 14, 59, tzinfo=IST))
    assert result["enabled"] is False
    assert result["reason"] == "outside_market_close_window"
