import pytest

from app.services.apiplatform_symbol_master import _normalize_symbols


def test_normalize_symbols_returns_one_uppercase_row_per_symbol():
    payload = [
        {"symbol": "rvnl", "category": "stock"},
        {"symbol": "MIDCPNIFTY", "category": "INDEX"},
        {"symbol": "HINDALCO", "category": "STOCK"},
    ]

    assert _normalize_symbols(payload) == [
        {"symbol": "HINDALCO", "category": "STOCK"},
        {"symbol": "MIDCPNIFTY", "category": "INDEX"},
        {"symbol": "RVNL", "category": "STOCK"},
    ]


def test_normalize_symbols_deduplicates_identical_symbols():
    payload = [
        {"symbol": "NIFTY", "category": "INDEX"},
        {"symbol": "NIFTY", "category": "INDEX"},
    ]

    assert _normalize_symbols(payload) == [
        {"symbol": "NIFTY", "category": "INDEX"},
    ]


@pytest.mark.parametrize("payload", [None, {}, [], "symbols"])
def test_normalize_symbols_rejects_non_array_or_empty_payload(payload):
    with pytest.raises(RuntimeError, match="non-empty JSON array"):
        _normalize_symbols(payload)


def test_normalize_symbols_rejects_missing_symbol_or_category():
    with pytest.raises(RuntimeError, match="has no symbol"):
        _normalize_symbols([{"category": "STOCK"}])

    with pytest.raises(RuntimeError, match="has no category"):
        _normalize_symbols([{"symbol": "RVNL"}])


def test_normalize_symbols_rejects_conflicting_duplicate_categories():
    payload = [
        {"symbol": "ABC", "category": "STOCK"},
        {"symbol": "ABC", "category": "INDEX"},
    ]

    with pytest.raises(RuntimeError, match="conflicting categories"):
        _normalize_symbols(payload)
