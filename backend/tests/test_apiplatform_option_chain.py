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
