from datetime import date

import pytest

from app.services.instrument_master import normalize_instrument


def test_normalize_instrument_maps_kite_fields():
    row = normalize_instrument(
        {
            "instrument_token": 256265,
            "exchange": "NSE",
            "tradingsymbol": "NIFTY 50",
            "name": "NIFTY 50",
            "instrument_type": "EQ",
            "segment": "INDICES",
            "expiry": "",
            "strike": 0,
            "lot_size": 1,
            "tick_size": 0.05,
        },
        version=123,
    )

    assert row["instrument_token"] == 256265
    assert row["exchange"] == "NSE"
    assert row["tradingsymbol"] == "NIFTY 50"
    assert row["expiry"] is None
    assert row["instrument_version"] == 123


def test_normalize_instrument_parses_expiry_date():
    row = normalize_instrument(
        {
            "instrument_token": 123,
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26DEC25000CE",
            "expiry": "2026-12-31",
            "strike": 25000,
        },
        version=456,
    )

    assert row["expiry"] == date(2026, 12, 31)


@pytest.mark.parametrize(
    "record",
    [
        {"exchange": "NSE", "tradingsymbol": "ABC"},
        {"instrument_token": 123, "tradingsymbol": "ABC"},
        {"instrument_token": 123, "exchange": "NSE"},
        {"instrument_token": 0, "exchange": "NSE", "tradingsymbol": "ABC"},
    ],
)
def test_normalize_instrument_rejects_missing_required_fields(record):
    with pytest.raises(ValueError, match="missing token, exchange, or trading symbol"):
        normalize_instrument(record, version=1)
