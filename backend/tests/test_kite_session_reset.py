from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.kite_session_reset import _reset_due


def test_reset_due_matches_configured_minute():
    now = datetime(2026, 10, 23, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert _reset_due(now, "23:00")
    assert not _reset_due(now, "22:59")


def test_reset_due_rejects_invalid_time():
    now = datetime(2026, 10, 23, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert not _reset_due(now, "25:00")
    assert not _reset_due(now, "23:60")
    assert not _reset_due(now, "bad")
