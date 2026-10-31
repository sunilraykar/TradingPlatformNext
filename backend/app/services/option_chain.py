""""Kite option-chain assembly with quote normalization and freshness metadata."""

from datetime import UTC, date, datetime
from time import monotonic
from zoneinfo import ZoneInfo
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
REST_SNAPSHOT_TTL_SECONDS = 5.0
QUOTE_STALE_AFTER_SECONDS = 15.0
_rest_snapshots: dict[int, dict[str, Any]] = {}
_rest_snapshot_at = 0.0


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _normalize_quote(quote: dict[str, Any] | None, source: str) -> dict[str, Any] | None:
    if not quote:
        return None
    # Kite REST quote and WebSocket tick use different volume field names.
    normalized = dict(quote)
    normalized["volume_traded"] = quote.get("volume_traded", quote.get("volume"))
    normalized["oi"] = quote.get("oi", quote.get("open_interest"))
    normalized["exchange_timestamp"] = quote.get("exchange_timestamp", quote.get("timestamp"))
    normalized["last_trade_time"] = quote.get("last_trade_time", quote.get("last_trade_time"))
    normalized["source"] = quote.get("source", source)
    normalized["received_at"] = quote.get("received_at") or datetime.now(UTC).isoformat()
    return normalized


def _quote_age_seconds(quote: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not quote or not quote.get("received_at"):
        return None
    try:
        received = datetime.fromisoformat(str(quote["received_at"]).replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        return max(0.0, ((now or datetime.now(UTC)) - received.astimezone(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _get_rest_snapshots(instruments: dict[int, str]) -> dict[int, dict[str, Any]]:
    """Seed instruments that have not emitted a WebSocket tick yet, in one quote call."""
    global _rest_snapshot_at, _rest_snapshots
    now = monotonic()
    missing = {token: key for token, key in instruments.items() if token not in _rest_snapshots}
    if not missing or now - _rest_snapshot_at < REST_SNAPSHOT_TTL_SECONDS:
        return {token: _rest_snapshots[token] for token in instruments if token in _rest_snapshots}
    try:
        response = get_kite_provider().quotes(list(missing.values()))
    except KiteProviderError:
        return {token: _rest_snapshots[token] for token in instruments if token in _rest_snapshots}
    received_at = datetime.now(UTC).isoformat()
    for token, quote_key in missing.items():
        quote = response.get(quote_key)
        if quote:
            normalized = _normalize_quote({**quote, "received_at": received_at}, "REST_SNAPSHOT")
            if normalized:
                _rest_snapshots[token] = normalized
    _rest_snapshot_at = now
    return {token: _rest_snapshots[token] for token in tokens if token in _rest_snapshots}


def _get_quote(token: int, snapshots: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    cached = _normalize_quote(live_quotes.get(token), "WEBSOCKET")
    if cached:
        return cached
    return snapshots.get(token)


def _quote_price(token: int, quote_key: str) -> float | None:
    cached = live_quotes.get(token)
    if cached and cached.get("last_price") is not None:
        return float(cached["last_price"])
    try:
        quote = get_kite_provider().quotes([quote_key]).get(quote_key, {})
    except KiteProviderError:
        return None
    price = quote.get("last_price")
    return float(price) if price is not None else None


def get_option_chain(
    underlying: str, expiry: str | None = None, strike_count: int = 10
) -> dict[str, Any]:
    """Return a strike-centered chain, seeding missing quotes and reporting tick age."""
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
                "AND tradingsymbol LIKE :prefix AND expiry >= :today "
                "ORDER BY expiry"
            ),
            {"prefix": f"{spec['option_prefix']}%", "today": datetime.now(ZoneInfo('Asia/Kolkata')).date()},
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
    spot = _quote_price(index_token, f"NSE:{spec['index_symbol']}")
    strikes = sorted({float(row["strike"]) for row in options if row["strike"] is not None})
    if not strikes:
        raise RuntimeError(f"No strikes found for {key} expiry {chosen_expiry.isoformat()}")
    center_index = len(strikes) // 2 if spot is None else min(
        range(len(strikes)), key=lambda i: abs(strikes[i] - spot)
    )
    start = max(0, center_index - strike_count)
    end = min(len(strikes), center_index + strike_count + 1)
    selected_strikes = strikes[start:end]
    selected_rows = [row for row in options if float(row["strike"]) in selected_strikes]
    tokens = [int(row["instrument_token"]) for row in selected_rows]
    subscription = live_market_data.subscribe(tokens, mode="full")
    quote_keys = {int(row["instrument_token"]): f"NFO:{row['tradingsymbol']}" for row in selected_rows}
    snapshots = _get_rest_snapshots(quote_keys)

    now = datetime.now(UTC)
    by_strike: dict[float, dict[str, Any]] = {
        strike: {"strike": strike, "CE": None, "PE": None} for strike in selected_strikes
    }
    fresh_count = stale_count = missing_count = 0
    for row in selected_rows:
        token = int(row["instrument_token"])
        quote = _get_quote(token, snapshots)
        side = str(row["instrument_type"])
        age = _quote_age_seconds(quote, now)
        freshness = "MISSING" if quote is None else (
            "FRESH" if age is not None and age <= QUOTE_STALE_AFTER_SECONDS else "STALE"
        )
        if freshness == "FRESH":
            fresh_count += 1
        elif freshness == "STALE":
            stale_count += 1
        else:
            missing_count += 1
        by_strike[float(row["strike"])][side] = {
            "instrument_token": token,
            "tradingsymbol": row["tradingsymbol"],
            "lot_size": row["lot_size"],
            "last_price": quote.get("last_price") if quote else None,
            "volume": quote.get("volume_traded") if quote else None,
            "oi": quote.get("oi") if quote else None,
            "exchange_timestamp": _as_iso(quote.get("exchange_timestamp")) if quote else None,
            "last_trade_time": _as_iso(quote.get("last_trade_time")) if quote else None,
            "received_at": quote.get("received_at") if quote else None,
            "quote_age_seconds": round(age, 2) if age is not None else None,
            "quote_freshness": freshness,
            "quote_source": quote.get("source") if quote else None,
        }

    feed = live_market_data.status()
    return {
        "underlying": key,
        "index_symbol": spec["index_symbol"],
        "index_token": index_token,
        "spot_price": spot,
        "expiry": chosen_expiry.isoformat(),
        "available_expiries": [item.isoformat() for item in expiries],
        "strike_count_each_side": strike_count,
        "live_subscription": subscription,
        "market_data": {
            "websocket_connected": feed.get("connected", False),
            "websocket_state": feed.get("state", "unknown"),
            "fresh_quotes": fresh_count,
            "stale_quotes": stale_count,
            "missing_quotes": missing_count,
            "total_quotes": len(selected_rows),
            "quote_stale_after_seconds": QUOTE_STALE_AFTER_SECONDS,
            "checked_at": now.isoformat(),
        },
        "strikes": list(by_strike.values()),
    }
