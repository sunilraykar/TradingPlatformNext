"""Expiry-aware ATM option selection for AutoTrender indices."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services.live_quotes import live_quotes

IST = ZoneInfo("Asia/Kolkata")

# Exchange and Kite instrument-master identifiers for each supported AutoTrender index.
INDEX_SPECS: dict[str, dict[str, str]] = {
    "NIFTY": {"exchange": "NSE", "spot_symbol": "NIFTY 50", "option_exchange": "NFO", "option_name": "NIFTY"},
    "BANKNIFTY": {"exchange": "NSE", "spot_symbol": "NIFTY BANK", "option_exchange": "NFO", "option_name": "BANKNIFTY"},
    "FINNIFTY": {"exchange": "NSE", "spot_symbol": "NIFTY FIN SERVICE", "option_exchange": "NFO", "option_name": "FINNIFTY"},
    "MIDCPNIFTY": {"exchange": "NSE", "spot_symbol": "NIFTY MID SELECT", "option_exchange": "NFO", "option_name": "MIDCPNIFTY"},
    "SENSEX": {"exchange": "BSE", "spot_symbol": "SENSEX", "option_exchange": "BFO", "option_name": "SENSEX"},
    "BANKEX": {"exchange": "BSE", "spot_symbol": "BANKEX", "option_exchange": "BFO", "option_name": "BANKEX"},
}


def _previous_weekday(day: date) -> date:
    """Return the previous Monday-Friday date; exchange holidays are not inferred."""
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _spot_price(token: int) -> float | None:
    cached = live_quotes.get(token)
    if cached and cached.get("last_price") is not None and cached.get("received_at"):
        try:
            received_at = datetime.fromisoformat(str(cached["received_at"]))
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=UTC)
            if (datetime.now(UTC) - received_at.astimezone(UTC)).total_seconds() <= 120:
                return float(cached["last_price"])
        except (TypeError, ValueError):
            pass
    try:
        value = get_kite_provider().quote(token).get("last_price")
        return float(value) if value is not None else None
    except (KiteProviderError, TypeError, ValueError):
        return None


def select_atm_contract(index: str, signal: str | None = None) -> dict[str, Any]:
    """Select the listed strike nearest spot and the correct expiry/type for a signal.

    One weekday before the nearest expiry, the selector moves to the next listed
    expiry. Exact exchange holidays are not embedded in the application, so this
    weekday-based pre-expiry check is a best-effort calendar rule.
    """
    key = str(index or "").upper()
    if key not in INDEX_SPECS:
        raise HTTPException(status_code=400, detail="Unsupported index")
    normalized_signal = str(signal or "").strip().upper()
    if normalized_signal not in {"BUY", "SELL"}:
        return {
            "index": key,
            "status": "WAITING_FOR_SIGNAL",
            "message": "A BUY or SELL option signal is required to select CE or PE.",
            "signal": normalized_signal or None,
        }

    spec = INDEX_SPECS[key]
    with get_engine().connect() as connection:
        spot_row = connection.execute(
            text(
                "SELECT instrument_token FROM instruments WHERE source = 'KITE' "
                "AND is_active = TRUE AND exchange = :exchange "
                "AND tradingsymbol = :symbol ORDER BY instrument_version DESC LIMIT 1"
            ),
            {"exchange": spec["exchange"], "symbol": spec["spot_symbol"]},
        ).mappings().first()
        if not spot_row:
            raise HTTPException(status_code=503, detail=f"{spec['spot_symbol']} is missing from the Kite instrument master")
        expiries = connection.execute(
            text(
                "SELECT DISTINCT expiry FROM instruments WHERE source = 'KITE' AND is_active = TRUE "
                "AND exchange = :exchange AND name = :name AND instrument_type IN ('CE', 'PE') "
                "AND expiry >= :today ORDER BY expiry"
            ),
            {
                "exchange": spec["option_exchange"],
                "name": spec["option_name"],
                "today": datetime.now(IST).date(),
            },
        ).scalars().all()

    today = datetime.now(IST).date()
    expiries = sorted({item for item in expiries if item is not None and item >= today})
    if not expiries:
        raise HTTPException(status_code=503, detail=f"No future {key} option expiries found; refresh the Kite instrument master")

    current_expiry = expiries[0]
    pre_expiry_switch = today == _previous_weekday(current_expiry)
    chosen_expiry = expiries[1] if pre_expiry_switch and len(expiries) > 1 else current_expiry
    if pre_expiry_switch and len(expiries) == 1:
        raise HTTPException(status_code=503, detail=f"Next expiry for {key} is unavailable; refusing to select the expiring contract")

    spot = _spot_price(int(spot_row["instrument_token"]))
    if spot is None or spot <= 0:
        raise HTTPException(status_code=503, detail=f"Live spot price for {key} is unavailable")

    option_type = "CE" if normalized_signal == "BUY" else "PE"
    with get_engine().connect() as connection:
        candidates = connection.execute(
            text(
                "SELECT instrument_token, tradingsymbol, strike, expiry, lot_size, exchange "
                "FROM instruments WHERE source = 'KITE' AND is_active = TRUE "
                "AND exchange = :exchange AND name = :name AND instrument_type = :option_type "
                "AND expiry = :expiry AND strike IS NOT NULL ORDER BY strike"
            ),
            {
                "exchange": spec["option_exchange"],
                "name": spec["option_name"],
                "option_type": option_type,
                "expiry": chosen_expiry,
            },
        ).mappings().all()

    if not candidates:
        raise HTTPException(status_code=503, detail=f"No {option_type} contracts found for {key} expiry {chosen_expiry.isoformat()}")

    # Use actual listed contracts, not a hard-coded strike interval. In an exact
    # tie, prefer the lower strike for deterministic selection.
    chosen = min(candidates, key=lambda row: (abs(float(row["strike"]) - spot), float(row["strike"])))
    return {
        "index": key,
        "status": "SELECTED",
        "spot_price": spot,
        "signal": normalized_signal,
        "option_type": option_type,
        "strike": float(chosen["strike"]),
        "expiry": chosen_expiry.isoformat(),
        "nearest_expiry": current_expiry.isoformat(),
        "used_next_expiry": bool(pre_expiry_switch),
        "expiry_rule": "Next listed expiry selected one weekday before nearest expiry" if pre_expiry_switch else "Nearest listed expiry",
        "tradingsymbol": str(chosen["tradingsymbol"]),
        "instrument_token": int(chosen["instrument_token"]),
        "lot_size": int(chosen["lot_size"]) if chosen["lot_size"] is not None else None,
        "exchange": str(chosen["exchange"]),
        "price_distance": abs(float(chosen["strike"]) - spot),
        "calendar_note": "Pre-expiry switch uses weekdays; exchange holidays are not included in the calendar calculation.",
    }
