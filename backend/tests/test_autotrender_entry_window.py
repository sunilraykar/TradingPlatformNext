from datetime import datetime as RealDateTime

import pytest
from fastapi import HTTPException

from app.api import trades


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return FakeResult(self.rows)


class FakeEngine:
    def __init__(self, rows):
        self.rows = rows

    def connect(self):
        return FakeConnection(self.rows)


def configured_rows(start="10:00", end="14:00"):
    return [
        {"setting_key": "ui.autoTrenderEntryStartTime", "setting_value": f'"{start}"'},
        {"setting_key": "ui.autoTrenderEntryEndTime", "setting_value": f'"{end}"'},
    ]


def set_ist_time(monkeypatch, hour, minute):
    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return RealDateTime(2026, 10, 24, hour, minute, tzinfo=tz)

    monkeypatch.setattr(trades, "datetime", FakeDateTime)


@pytest.mark.parametrize(("hour", "minute"), [(10, 0), (14, 0)])
def test_autotrender_entry_window_includes_both_boundaries(monkeypatch, hour, minute):
    monkeypatch.setattr(trades, "get_engine", lambda: FakeEngine(configured_rows()))
    set_ist_time(monkeypatch, hour, minute)

    trades._enforce_autotrender_entry_window("AUTOTRENDER")


@pytest.mark.parametrize(("hour", "minute"), [(9, 59), (14, 1)])
def test_autotrender_entry_window_blocks_outside_window(monkeypatch, hour, minute):
    monkeypatch.setattr(trades, "get_engine", lambda: FakeEngine(configured_rows()))
    set_ist_time(monkeypatch, hour, minute)

    with pytest.raises(HTTPException) as exc:
        trades._enforce_autotrender_entry_window("AUTO_TRENDER")
    assert exc.value.status_code == 422


def test_manual_trade_is_not_restricted_by_autotrender_window(monkeypatch):
    def fail_if_read():
        raise AssertionError("Manual trade should not read AutoTrender settings")

    monkeypatch.setattr(trades, "get_engine", fail_if_read)
    trades._enforce_autotrender_entry_window("MANUAL")


def test_invalid_saved_window_fails_closed(monkeypatch):
    monkeypatch.setattr(trades, "get_engine", lambda: FakeEngine(configured_rows("14:00", "10:00")))
    set_ist_time(monkeypatch, 12, 0)

    with pytest.raises(HTTPException) as exc:
        trades._enforce_autotrender_entry_window("AUTOTRENDER")
    assert exc.value.status_code == 503
