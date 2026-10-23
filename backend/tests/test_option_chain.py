import pytest

from app.services.option_chain import get_option_chain


@pytest.mark.parametrize("underlying", ["", "SENSEX", "niftybank"])
def test_option_chain_rejects_unknown_underlying(underlying):
    with pytest.raises(ValueError, match="underlying must be NIFTY or BANKNIFTY"):
        get_option_chain(underlying)


@pytest.mark.parametrize("strike_count", [0, 31, -1])
def test_option_chain_rejects_invalid_strike_count(strike_count):
    with pytest.raises(ValueError, match="strike_count must be between 1 and 30"):
        get_option_chain("NIFTY", strike_count=strike_count)


def test_option_chain_rejects_invalid_expiry_before_database_access():
    with pytest.raises(ValueError, match="expiry must use YYYY-MM-DD format"):
        get_option_chain("BANKNIFTY", expiry="next Thursday")
