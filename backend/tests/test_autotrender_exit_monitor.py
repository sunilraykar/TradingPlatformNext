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


def row(minute, *, call="60", put="40", pcr="1.5", vwap="BUY", option="BUY"):
    return {
        "time": f"10:{minute:02d}",
        "timestamp": f"2026-11-23T10:{minute:02d}:00+05:30",
        "call": call,
        "put": put,
        "pcr": {"value": pcr},
        "vWapSignal": {"value": vwap},
        "optionSignal": {"type": option},
    }


def apply(tr, source_row):
    updates, reason = _evaluate_trade(tr, source_row, NOW)
    if updates:
        tr.update(updates)
    return reason


def test_buy_bullish_dominance_more_negative_is_favourable():
    tr = trade("BUY")
    assert apply(tr, row(0, call="40", put="60")) is None  # -20
    assert apply(tr, row(1, call="35", put="65")) is None  # -30
    assert tr["autotrend_exit_monitor"]["counters"]["call_put_strength_diff"] == 0


def test_buy_dominance_four_moves_towards_zero_trigger_exit():
    tr = trade("BUY")
    assert apply(tr, row(0, call="40", put="60")) is None  # -20
    assert apply(tr, row(1, call="45", put="55")) is None  # -10
    assert apply(tr, row(2, call="50", put="50")) is None  # 0
    assert apply(tr, row(3, call="55", put="45")) is None  # +10
    assert apply(tr, row(4, call="60", put="40")) == "AUTOTRENDER_DOMINANCE_DETERIORATION_4_READINGS"


def test_sell_bearish_dominance_more_positive_is_favourable():
    tr = trade("SELL")
    assert apply(tr, row(0, call="60", put="40", vwap="SELL", option="SELL")) is None  # +20
    assert apply(tr, row(1, call="65", put="35", vwap="SELL", option="SELL")) is None  # +30
    assert tr["autotrend_exit_monitor"]["counters"]["call_put_strength_diff"] == 0


def test_sell_dominance_four_moves_towards_zero_trigger_exit():
    tr = trade("SELL")
    assert apply(tr, row(0, call="60", put="40", vwap="SELL", option="SELL")) is None  # +20
    assert apply(tr, row(1, call="55", put="45", vwap="SELL", option="SELL")) is None  # +10
    assert apply(tr, row(2, call="50", put="50", vwap="SELL", option="SELL")) is None  # 0
    assert apply(tr, row(3, call="45", put="55", vwap="SELL", option="SELL")) is None  # -10
    assert apply(tr, row(4, call="40", put="60", vwap="SELL", option="SELL")) == "AUTOTRENDER_DOMINANCE_DETERIORATION_4_READINGS"


def test_favourable_dominance_reading_resets_counter():
    tr = trade("BUY")
    apply(tr, row(0, call="40", put="60"))
    apply(tr, row(1, call="45", put="55"))
    apply(tr, row(2, call="50", put="50"))
    apply(tr, row(3, call="35", put="65"))
    assert tr["autotrend_exit_monitor"]["counters"]["call_put_strength_diff"] == 0
    assert apply(tr, row(4, call="40", put="60")) is None


def test_duplicate_timestamp_is_ignored():
    tr = trade("BUY")
    apply(tr, row(0, call="40", put="60"))
    apply(tr, row(1, call="45", put="55"))
    before = tr["autotrend_exit_monitor"]
    updates, reason = _evaluate_trade(tr, row(1, call="50", put="50"), NOW)
    assert updates == {}
    assert reason is None
    assert tr["autotrend_exit_monitor"] == before


def test_missing_call_put_does_not_increment_or_replace_baseline():
    tr = trade("BUY")
    apply(tr, row(0, call="40", put="60"))
    apply(tr, row(1, call="45", put="55"))
    apply(tr, row(2, call=None, put=None))
    assert tr["autotrend_exit_monitor"]["counters"]["call_put_strength_diff"] == 1
    assert tr["autotrend_exit_monitor"]["last_valid_reading"]["call_put_strength_diff"] == -10
    apply(tr, row(3, call="50", put="50"))
    assert tr["autotrend_exit_monitor"]["counters"]["call_put_strength_diff"] == 2


def test_immediate_signal_mismatch_exits_without_waiting_for_counters():
    tr = trade("BUY")
    assert apply(tr, row(0, vwap="BUY", option="SELL")) == "AUTOTRENDER_DIRECTIONAL_SIGNAL_MISMATCH"


def test_price_vwap_is_not_an_exit_input():
    tr = trade("BUY")
    source = row(0)
    source["price"] = {"value": "100"}
    source["vWap"] = {"value": "200"}
    assert apply(tr, source) is None


def test_buy_pcr_deterioration_requires_four_consecutive_declines():
    tr = trade("BUY")
    assert apply(tr, row(0, pcr="1.5")) is None
    assert apply(tr, row(1, pcr="1.4")) is None
    assert apply(tr, row(2, pcr="1.3")) is None
    assert apply(tr, row(3, pcr="1.2")) is None
    assert apply(tr, row(4, pcr="1.1")) == "AUTOTRENDER_PCR_DETERIORATION_4_READINGS"


def test_sell_pcr_deterioration_requires_four_consecutive_increases():
    tr = trade("SELL")
    assert apply(tr, row(0, pcr="1.0", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(1, pcr="1.1", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(2, pcr="1.2", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(3, pcr="1.3", vwap="SELL", option="SELL")) is None
    assert apply(tr, row(4, pcr="1.4", vwap="SELL", option="SELL")) == "AUTOTRENDER_PCR_DETERIORATION_4_READINGS"
