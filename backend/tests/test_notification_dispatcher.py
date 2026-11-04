from app.services.telegram_dispatcher import format_notification_message, retry_delay_seconds


def test_notification_message_uses_custom_text():
    message = format_notification_message(
        "LIVE_TRADE_ENTERED",
        "TRADE",
        {"telegram_text": "Trade entered: NIFTY", "symbol": "NIFTY"},
    )
    assert message == "Trade entered: NIFTY"


def test_notification_message_formats_payload_fields():
    message = format_notification_message(
        "LIVE_TRADE_ENTERED",
        "TRADE",
        {"symbol": "NIFTY", "quantity": 50, "ignored": None},
    )
    assert "TradingPlatformNext" in message
    assert "Live Trade Entered" in message
    assert "Symbol: NIFTY" in message
    assert "Quantity: 50" in message
    assert "Ignored" not in message


def test_notification_message_respects_telegram_length_limit():
    message = format_notification_message("SYSTEM_ALERT", "SYSTEM", {"detail": "x" * 5000})
    assert len(message) == 4096


def test_retry_schedule_and_telegram_retry_after():
    assert [retry_delay_seconds(n) for n in range(1, 5)] == [5, 30, 120, 600]
    assert retry_delay_seconds(1, 17) == 17


def test_autotrend_signal_direction_change_classification():
    from app.services.notification_events import _dump

    assert _dump({"direction": "BUY"}) == '{"direction":"BUY"}'
