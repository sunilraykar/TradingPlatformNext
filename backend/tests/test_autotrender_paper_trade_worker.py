from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.autotrender_paper_trade_worker import (
    _entry_window,
    _poll_interval_seconds,
    enabled_indices,
    latest_intraday_row,
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
    settings = {"autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "14:00"}
    assert _entry_window(datetime(2026, 10, 26, 10, 0, tzinfo=IST), settings) == (True, "inside_entry_window")
    assert _entry_window(datetime(2026, 10, 26, 14, 0, tzinfo=IST), settings) == (True, "inside_entry_window")


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
