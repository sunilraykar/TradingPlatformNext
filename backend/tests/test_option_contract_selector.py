from datetime import date

import pytest
from fastapi import HTTPException

from app.services import option_contract_selector as selector


def test_rolls_to_next_expiry_on_expiry_day(monkeypatch):
    monkeypatch.setattr(selector, "_previous_trading_session", lambda expiry, exchange: date(2026, 11, 18))
    nearest, chosen, used_next, rule = selector._resolve_expiry(
        [date(2026, 11, 19), date(2026, 11, 26)],
        date(2026, 11, 19),
        "NFO",
    )
    assert nearest == date(2026, 11, 19)
    assert chosen == date(2026, 11, 26)
    assert used_next is True
    assert "expiry day" in rule


def test_rolls_on_last_trading_session_before_expiry_including_holiday_gap(monkeypatch):
    # Wednesday is an exchange holiday; Tuesday is the last trading session.
    monkeypatch.setattr(selector, "_previous_trading_session", lambda expiry, exchange: date(2026, 11, 17))
    nearest, chosen, used_next, rule = selector._resolve_expiry(
        [date(2026, 11, 19), date(2026, 11, 26)],
        date(2026, 11, 17),
        "NFO",
    )
    assert nearest == date(2026, 11, 19)
    assert chosen == date(2026, 11, 26)
    assert used_next is True
    assert "last trading session" in rule


def test_keeps_nearest_expiry_more_than_one_session_away(monkeypatch):
    monkeypatch.setattr(selector, "_previous_trading_session", lambda expiry, exchange: date(2026, 11, 18))
    nearest, chosen, used_next, _ = selector._resolve_expiry(
        [date(2026, 11, 19), date(2026, 11, 26)],
        date(2026, 11, 17),
        "NFO",
    )
    assert chosen == nearest == date(2026, 11, 19)
    assert used_next is False


def test_refuses_expiring_contract_when_next_expiry_is_missing(monkeypatch):
    monkeypatch.setattr(selector, "_previous_trading_session", lambda expiry, exchange: date(2026, 11, 18))
    with pytest.raises(HTTPException, match="503"):
        selector._resolve_expiry([date(2026, 11, 19)], date(2026, 11, 19), "NFO")


def test_drops_already_expired_contracts(monkeypatch):
    monkeypatch.setattr(selector, "_previous_trading_session", lambda expiry, exchange: date(2026, 11, 25))
    nearest, chosen, used_next, _ = selector._resolve_expiry(
        [date(2026, 11, 19), date(2026, 11, 26), date(2026, 12, 3)],
        date(2026, 11, 27),
        "NFO",
    )
    assert nearest == chosen == date(2026, 12, 3)
    assert used_next is False
