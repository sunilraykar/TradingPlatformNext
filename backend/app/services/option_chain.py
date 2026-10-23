"""Live option-chain assembly from the imported Kite instrument master."""

from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services import live_market_data
from app.services.live_quotes import live_quotes

UNDERLYINGS = {
    "NIFTY": {"index_symbol": "NIFTY 50", "option_prefix": "NIFTY"},
    "BANKNIFTY": {"index_symbol": "NIFTY BANK", "option_prefix": "BANKNIFTY"},
}


def _quote_price(token: int) -> float | None:
    cached = live_quotes.get(token)
    if cached and cached.get("last_price") is not None:
        return float(cached["last_price"])
    try:
        quote = get_kite_provider().quote(token)
    except KiteProviderError:
        return None
    price = quote.get("last_price")
    return float(price) if price is not None else None


def get_option_chain(
    underlying: str, expiry: str | None = None, strike_count: int = 10
) -> dict[str, Any]:
    """Return a strike-centered chain and subscribe its tokens for live updates."""
    key = underlying.upper()
    if key not in UNDERLYINGS:
        raise ValueError("underlying must be NIFTY or BANKNIFTY")
    if not 1 <= strike_count <= 30:
        raise ValueError("strike_count must be between 1 and 30")
    expiry_date = None
    if expiry:
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError as exc:
            raise ValueError("expiry must use YYYY-MM-DD format") from exc

    spec = UNDERLYINGS[key]
    with get_engine().connect() as connection:
        index_row = connection.execute(
            text(
                "SELECT instrument_token, tradingsymbol FROM instruments "
                "WHERE exchange = 'NSE' AND tradingsymbol = :symbol AND is_active = TRUE LIMIT 1"
            ),
            {"symbol": spec["index_symbol"]},
        ).mappings().first()
        if index_row is None:
            raise RuntimeError(
                f"{spec['index_symbol']} index is missing; import the Kite instrument master first"
            )
        expiries = connection.execute(
            text(
                "SELECT DISTINCT expiry FROM instruments WHERE is_active = TRUE "
                "AND exchange = 'NFO' AND instrument_type IN ('CE', 'PE') "
                "AND tradingsymbol LIKE :prefix AND expiry >= CURRENT_DATE "
                "ORDER BY expiry"
            ),
            {"prefix": f"{spec['option_prefix']}%"},
        ).scalars().all()
        if not expiries:
            raise RuntimeError(
                f"No active {key} options found; import the Kite instrument master first"
            )
        chosen_expiry = expiry_date or expiries[0]
        if chosen_expiry not in expiries:
            raise ValueError(f"expiry is not an available {key} option expiry")

        options = connection.execute(
            text(
                "SELECT instrument_token, tradingsymbol, instrument_type, strike, expiry, "
                "lot_size FROM instruments WHERE is_active = TRUE AND exchange = 'NFO' "
                "AND instrument_type IN ('CE', 'PE') AND tradingsymbol LIKE :prefix "
                "AND expiry = :expiry ORDER BY strike, instrument_type"
            ),
            {"prefix": f"{spec['option_prefix']}%", "expiry": chosen_expiry},
        ).mappings().all()

    index_token = int(index_row["instrument_token"])
    spot = _quote_price(index_token)
    strikes = sorted({float(row["strike"]) for row in options if row["strike"] is not None})
    if not strikes:
        raise RuntimeError(f"No strikes found for {key} expiry {chosen_expiry.isoformat()}")
    if spot is None:
        center_index = len(strikes) // 2
    else:
        center_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    start = max(0, center_index - strike_count)
    end = min(len(strikes), center_index + strike_count + 1)
    selected_strikes = strikes[start:end]
    selected_rows = [row for row in options if float(row["strike"]) in selected_strikes]
    tokens = [int(row["instrument_token"]) for row in selected_rows]
    subscription = live_market_data.subscribe(tokens, mode="quote")

    by_strike: dict[float, dict[str, Any]] = {
        strike: {"strike": strike, "CE": None, "PE": None} for strike in selected_strikes
    }
    for row in selected_rows:
        token = int(row["instrument_token"])
        quote = live_quotes.get(token)
        side = str(row["instrument_type"])
        by_strike[float(row["strike"])][side] = {
            "instrument_token": token,
            "tradingsymbol": row["tradingsymbol"],
            "lot_size": row["lot_size"],
            "last_price": quote.get("last_price") if quote else None,
            "volume": quote.get("volume_traded") if quote else None,
            "oi": quote.get("oi") if quote else None,
            "received_at": quote.get("received_at") if quote else None,
        }

    return {
        "underlying": key,
        "index_symbol": spec["index_symbol"],
        "index_token": index_token,
        "spot_price": spot,
        "expiry": chosen_expiry.isoformat(),
        "available_expiries": [item.isoformat() for item in expiries],
        "strike_count_each_side": strike_count,
        "live_subscription": subscription,
        "strikes": list(by_strike.values()),
    }
