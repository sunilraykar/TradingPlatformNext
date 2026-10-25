from datetime import datetime
from zoneinfo import ZoneInfo

import app.services.autotrender_paper_trade_worker as worker
from app.services.autotrender_paper_trade_worker import (
    _entry_window,
    _poll_interval_seconds,
    _max_signal_age_seconds,
    enabled_indices,
    latest_intraday_row,
    signal_freshness,
)

IST = ZoneInfo("Asia/Kolkata")


def test_latest_intraday_row_accepts_data_array_and_returns_last_row():
    payload = {"data": [{"call": 60, "put": 40}, {"call": 80, "put": 20}]}
    assert latest_intraday_row(payload) == {"call": 80, "put": 20}


def test_latest_intraday_row_accepts_wrapped_single_row():
    payload = {"data": {"trend": {"call": 80, "put": 20}}}
    assert latest_intraday_row(payload) == {"call": 80, "put": 20}


def test_enabled_indices_normalizes_saved_dashboard_map():
    assert enabled_indices({
        "Nifty": True,
        "BankNifty": False,
        "FinNifty": True,
        "Sensex": False,
    }) == ["NIFTY", "FINNIFTY"]


def test_enabled_indices_accepts_list_and_defaults_when_unset():
    assert enabled_indices(["nifty", "SENSEX", "not-an-index"]) == ["NIFTY", "SENSEX"]
    assert enabled_indices(None) == ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


def test_entry_window_includes_start_and_end_boundaries():
    settings = {"autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "15:25"}
    assert _entry_window(datetime(2026, 10, 26, 10, 0, tzinfo=IST), settings) == (True, "inside_entry_window")
    assert _entry_window(datetime(2026, 10, 26, 15, 24, 59, tzinfo=IST), settings) == (True, "inside_entry_window")
    assert _entry_window(datetime(2026, 10, 26, 15, 25, tzinfo=IST), settings) == (False, "outside_entry_window")


def test_entry_window_rejects_outside_hours_and_weekends():
    settings = {"autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "14:00"}
    assert _entry_window(datetime(2026, 10, 26, 9, 59, tzinfo=IST), settings) == (False, "outside_entry_window")
    assert _entry_window(datetime(2026, 10, 25, 11, 0, tzinfo=IST), settings) == (False, "weekend")


def test_worker_interval_uses_configured_seconds_and_safe_bounds():
    assert _poll_interval_seconds(15) == 15
    assert _poll_interval_seconds("2.9") == 2
    assert _poll_interval_seconds(0) == 1
    assert _poll_interval_seconds(99999) == 3600
    assert _poll_interval_seconds("invalid") == 5


def test_signal_freshness_rejects_missing_stale_and_future_timestamps():
    now = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    assert signal_freshness({"call": 80}, now, 300)[:2] == (False, "signal_timestamp_missing_or_invalid")
    assert signal_freshness({"timestamp": "2026-10-26T09:54:59+05:30"}, now, 300)[:2] == (False, "stale_signal")
    assert signal_freshness({"timestamp": "2026-10-26T10:01:00+05:30"}, now, 300)[:2] == (False, "signal_timestamp_in_future")
    assert signal_freshness({"timestamp": "2026-10-26T10:00:20+05:30"}, now, 300)[:2] == (False, "signal_timestamp_in_future")


def test_signal_freshness_accepts_recent_timestamp():
    now = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    fresh, reason, timestamp = signal_freshness({"timestamp": "2026-10-26T09:59:00+05:30"}, now, 300)
    assert fresh is True
    assert reason == "fresh_signal"
    assert timestamp == datetime(2026, 10, 26, 9, 59, tzinfo=IST)


def test_run_cycle_creates_paper_trade_for_fresh_eligible_signal(monkeypatch):
    now = datetime(2026, 10, 26, 10, 1, tzinfo=IST)
    row = {
        "timestamp": now.isoformat(),
        "call": 80, "put": 20, "price": 25000, "vwap": 24990,
        "pcr": 1.2, "vWapSignal": "BUY", "optionSignal": "BUY",
    }
    settings = {
        "autoTrenderDashboardIndices": {"Nifty": True},
        "callPutStrengthDiff": 10, "callPcr": 1, "putPcr": 1,
        "vwapPriceDifferenceBuffer": 0.3,
        "autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "15:25",
        "autoTrenderWorkerIntervalSeconds": 5, "autoTrenderMaxSignalAgeSeconds": 300,
        "targetFallbackPercent": 1,
    }
    monkeypatch.setattr(worker, "_read_settings", lambda: settings)
    monkeypatch.setattr(worker, "_fetch_intraday", lambda index: {"data": [row]})
    monkeypatch.setattr(worker, "_create_paper_trade", lambda *args: {"trade_id": "paper-123"})
    worker._LAST_SIGNAL_BY_INDEX.clear()

    result = worker.run_cycle(now)

    assert result["indices"]["NIFTY"]["state"] == "paper_trade_created"
    assert result["indices"]["NIFTY"]["trade_id"] == "paper-123"


def test_run_cycle_fails_closed_for_unknown_live_timestamp_field(monkeypatch):
    now = datetime(2026, 10, 26, 10, 1, tzinfo=IST)
    row = {
        "createdOn": now.isoformat(),
        "call": 80, "put": 20, "price": 25000, "vwap": 24990,
        "pcr": 1.2, "vWapSignal": "BUY", "optionSignal": "BUY",
    }
    settings = {
        "autoTrenderDashboardIndices": {"Nifty": True},
        "callPutStrengthDiff": 10, "callPcr": 1, "putPcr": 1,
        "vwapPriceDifferenceBuffer": 0.3,
        "autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "15:25",
        "autoTrenderWorkerIntervalSeconds": 5, "autoTrenderMaxSignalAgeSeconds": 300,
        "targetFallbackPercent": 1,
    }
    monkeypatch.setattr(worker, "_read_settings", lambda: settings)
    monkeypatch.setattr(worker, "_fetch_intraday", lambda index: {"data": [row]})
    monkeypatch.setattr(worker, "_create_paper_trade", lambda *args: (_ for _ in ()).throw(AssertionError("must not create trade")))
    worker._LAST_SIGNAL_BY_INDEX.clear()

    result = worker.run_cycle(now)

    outcome = result["indices"]["NIFTY"]
    assert outcome["state"] == "rejected"
    assert outcome["reason"] == "signal_timestamp_missing_or_invalid"
    assert "createdOn" in outcome["source_fields"]
    assert outcome["recognized_timestamp_fields"] == []



def test_max_signal_age_is_validated_independently_from_poll_interval():
    assert _max_signal_age_seconds(300) == 300
    assert _max_signal_age_seconds("120") == 120
    assert _max_signal_age_seconds(0) == 1
    assert _max_signal_age_seconds(99999) == 3600
    assert _max_signal_age_seconds("invalid") == 300
