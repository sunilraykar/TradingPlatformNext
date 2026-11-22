from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.autotrender_exit_monitor import _evaluate_trade

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 23, 10, 4, tzinfo=IST)


def trade(direction="BUY"):
    return {
        "trade_id": "test-trade",
        "strategy": f"AutoTrender {direction} {'CE' if direction == 'BUY' else 'PE'}",
        "entry_signal_snapshot": {"signal_direction": direction},
    }


def row(minute, *, call="60", put="40", diff="-100", pcr="1.5",
        vwap="BUY", option="BUY"):
    return {
        "time": f"10:{minute:02d}",
        "call": call,
        "put": put,
        "diff": {"value": diff},
        "pcr": {"value": pcr},
        "vWapSignal": {"value": vwap},
        "optionSignal": {"type": option},
    }


def apply(tr, source_row):
    updates, reason = _evaluate_trade(tr, source_row, NOW)
    if updates:
        tr.update(updates)
    return reason


def test_buy_dominance_four_adverse_readings_triggers_exit():
    tr = trade("BUY")
    assert apply(tr, row(0, diff="-100")) is None
    assert apply(tr, row(1, diff="-90")) is None
    assert apply(tr, row(2, diff="-80")) is None
    assert apply(tr, row(3, diff="-70")) is None
    assert apply(tr, row(4, diff="-60")) == "AUTOTRENDER_DOMINANCE_DETERIORATION_4_READINGS"


def test_sell_dominance_four_adverse_readings_triggers_exit():
    tr = trade("SELL")
    assert apply(tr, row(0, diff="100", pcr="1.0", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(1, diff="90", pcr="1.0", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(2, diff="80", pcr="1.0", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(3, diff="70", pcr="1.0", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(4, diff="60", pcr="1.0", vwap="SELL", option="SELL")) == "AUTOTREND_DOMINANCE_DETERIORATION_4_READINGS"


def test_favourable_dominance_reading_resets_counter():
    tr = trade("BUY")
    apply(tr, row(0, diff="-100"))
    apply(tr, row(1, diff="-90"))
    apply(tr, row(2, diff="-80"))
    apply(tr, row(3, diff="-110"))
    assert tr["autotrend_exit_monitor"]["counters"]["dominance"] == 0
    assert apply(tr, row(4, diff="-100")) is None


def test_duplicate_timestamp_is_ignored():
    tr = trade("BUY")
    apply(tr, row(0, diff="-100"))
    apply(tr, row(1, diff="-90"))
    before = tr["autotrend_exit_monitor"]
    updates, reason = _evaluate_trade(tr, row(1, diff="-80"), NOW)
    assert updates == {}
    assert reason is None
    assert tr["autotrend_exit_monitor"] == before


def test_missing_diff_does_not_increment_or_replace_last_valid_baseline():
    tr = trade("BUY")
    apply(tr, row(0, diff="-100"))
    apply(tr, row(1, diff="-90"))
    apply(tr, row(2, diff=None))
    assert tr["autotrend_exit_monitor"]["counters"]["dominance"] == 1
    assert tr["autotrend_exit_monitor"]["last_valid_reading"]["diff"] == -90
    apply(tr, row(3, diff="-80"))
    assert tr["autotrend_exit_monitor"]["counters"]["dominance"] == 2


def test_immediate_signal_mismatch_exits_without_waiting_for_counters():
    tr = trade("BUY")
    assert apply(tr, row(0, diff="-100", vwap="BUY", option="SELL")) == "AUTOTRENDER_DIRECTIONAL_SIGNAL_MISMATCH"


def test_price_vwap_is_not_an_exit_input():
    tr = trade("BUY")
    source = row(0, diff="-100")
    source["price"] = {"value": "100"}
    source["vWap"] = {"value": "200"}
    assert apply(tr, source) is None
