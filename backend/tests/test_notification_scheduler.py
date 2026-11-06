from app.services.notification_scheduler import _trade_pnl


def test_paper_trade_pnl_uses_direction_and_quantity():
    buy, _ = _trade_pnl({
        "execution_mode": "PAPER", "direction": "BUY",
        "entry_price": 100, "last_price": 110, "quantity": 25,
    })
    sell, _ = _trade_pnl({
        "execution_mode": "PAPER", "direction": "SELL",
        "entry_price": 100, "last_price": 90, "quantity": 25,
    })
    assert buy == 250
    assert sell == 250


def test_live_trade_pnl_uses_broker_reported_value():
    pnl, label = _trade_pnl({"execution_mode": "LIVE", "broker_pnl": -375.5})
    assert pnl == -375.5
    assert label == "Broker-reported P&L"


def test_missing_or_invalid_pnl_is_not_guessed():
    assert _trade_pnl({"execution_mode": "PAPER", "entry_price": 100})[0] is None
    assert _trade_pnl({"execution_mode": "LIVE", "broker_pnl": "not-a-number"})[0] is None

from app.api.telegram import TELEGRAM_NOTIFICATION_DEFAULTS


def test_telegram_notification_defaults_cover_all_worker_controls():
    assert TELEGRAM_NOTIFICATION_DEFAULTS["telegramPnlAlertThreshold"] == 1000
    assert TELEGRAM_NOTIFICATION_DEFAULTS["telegramActiveSummaryIntervalMinutes"] == 15
    assert TELEGRAM_NOTIFICATION_DEFAULTS["telegramDailySummaryTime"] == "15:45"
    assert TELEGRAM_NOTIFICATION_DEFAULTS["telegramSystemAlertSeverity"] == "ERROR"
    assert TELEGRAM_NOTIFICATION_DEFAULTS["telegramLiveFillPollSeconds"] == 15


def test_live_pnl_is_never_derived_from_last_price():
    pnl, _ = _trade_pnl({
        "execution_mode": "LIVE", "entry_price": 100,
        "last_price": 150, "quantity": 10,
    })
    assert pnl is None
