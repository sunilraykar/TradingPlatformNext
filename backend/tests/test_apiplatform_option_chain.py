import pytest
from fastapi import HTTPException

from app.api.apiplatform_option_chain import _expiry, _symbol


def test_option_chain_symbol_is_normalized():
    assert _symbol(" nifty ") == "NIFTY"


def test_option_chain_symbol_rejects_unsafe_characters():
    with pytest.raises(HTTPException) as error:
        _symbol("NIFTY; DROP TABLE instruments")
    assert error.value.status_code == 422


def test_option_chain_expiry_accepts_browser_date_format():
    assert _expiry("09-11-2026").isoformat() == "2026-11-09"


def test_option_chain_expiry_accepts_iso_date():
    assert _expiry("2026-11-09").isoformat() == "2026-11-09"


def test_option_chain_expiry_rejects_invalid_date():
    with pytest.raises(HTTPException) as error:
        _expiry("2026/11/09")
    assert error.value.status_code == 422


from app.api.apiplatform_option_chain import _select_atm_instruments


def _contracts():
    return [
        {"instrument_token": strike * 10 + suffix, "strike": float(strike), "side": side}
        for strike in range(100, 1100, 100)
        for side, suffix in (("CE", 1), ("PE", 2))
    ]


def test_select_atm_instruments_keeps_three_strikes_each_side_and_both_sides():
    selected, atm = _select_atm_instruments(_contracts(), 560, strikes_each_side=3)

    assert atm == 600.0
    assert sorted({item["strike"] for item in selected}) == [300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0]
    assert len(selected) == 14
    assert {item["side"] for item in selected} == {"CE", "PE"}


def test_select_atm_instruments_clamps_window_at_chain_edges():
    selected, atm = _select_atm_instruments(_contracts(), 100, strikes_each_side=3)

    assert atm == 100.0
    assert sorted({item["strike"] for item in selected}) == [100.0, 200.0, 300.0, 400.0]


def test_select_atm_instruments_returns_empty_without_valid_underlying_price():
    assert _select_atm_instruments(_contracts(), None, strikes_each_side=3) == ([], None)
    assert _select_atm_instruments(_contracts(), "not-a-price", strikes_each_side=3) == ([], None)
