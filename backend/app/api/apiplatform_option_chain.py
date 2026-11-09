"""APIPLATFORM option-chain data with Kite WebSocket quote enrichment."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.providers.apiplatform.registry import get_apiplatform_client
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services import live_market_data
from app.services.live_quotes import live_quotes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apiplatform/option-chain", tags=["APIPLATFORM Option Chain"])
IST = ZoneInfo("Asia/Kolkata")

_INDEX_INSTRUMENTS = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "BANKNIFTY": ("NSE", "NIFTY BANK"),
    "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
    "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"),
    "SENSEX": ("BSE", "SENSEX"),
    "BANKEX": ("BSE", "BANKEX"),
}


def _symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9&_-]{2,30}", normalized):
        raise HTTPException(status_code=422, detail="Invalid option-chain symbol.")
    return normalized


def _expiry(value: str) -> date:
    for pattern in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    raise HTTPException(status_code=422, detail="Expiry must use DD-MM-YYYY.")


def _expiry_label(value: Any) -> str:
    return value.strftime("%d-%m-%Y") if hasattr(value, "strftime") else str(value)


def _select_atm_instruments(
    instruments: list[dict[str, Any]], underlying_price: Any, strikes_each_side: int = 3,
) -> tuple[list[dict[str, Any]], float | None]:
    """Keep CE/PE contracts for the ATM strike and a bounded window around it."""
    if not instruments or underlying_price in (None, ""):
        return [], None
    try:
        price = float(str(underlying_price).replace(",", "").strip())
    except (TypeError, ValueError):
        return [], None
    if price <= 0:
        return [], None

    strikes = sorted({float(item["strike"]) for item in instruments if item.get("strike") is not None})
    if not strikes:
        return [], None
    atm_index = min(range(len(strikes)), key=lambda index: abs(strikes[index] - price))
    atm_strike = strikes[atm_index]
    start = max(0, atm_index - strikes_each_side)
    end = min(len(strikes), atm_index + strikes_each_side + 1)
    selected = set(strikes[start:end])
    return [item for item in instruments if float(item["strike"]) in selected], atm_strike


@router.get("/symbols")
def option_chain_symbols() -> dict[str, Any]:
    """Return the latest stored APIPLATFORM LIVE symbol master."""
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(text("""
                SELECT symbol, category
                FROM All_symbols_MI
                WHERE fetched_at = (SELECT MAX(fetched_at) FROM All_symbols_MI)
                ORDER BY symbol
            """)).mappings().all()
        if rows:
            symbols = [{"symbol": str(row["symbol"]), "category": str(row["category"])} for row in rows]
        else:
            symbols = []
    except SQLAlchemyError:
        logger.exception("Could not read the APIPLATFORM symbol master from MySQL")
        symbols = []

    if not symbols:
        try:
            payload = get_apiplatform_client().fetch_all_symbols("LIVE")
            symbols = [
                {"symbol": str(row["symbol"]).strip().upper(), "category": str(row.get("category", "")).strip().upper()}
                for row in payload
                if isinstance(row, dict) and row.get("symbol")
            ]
        except Exception as exc:
            logger.warning("APIPLATFORM symbol master is unavailable: %s", type(exc).__name__)
            symbols = [{"symbol": key, "category": "INDEX"} for key in _INDEX_INSTRUMENTS]

    # Keep symbols with actual option contracts first, but preserve the full provider list.
    return {"symbols": symbols, "source": "APIPLATFORM LIVE symbol master"}


@router.get("/metadata")
def option_chain_metadata(symbol: str = Query(default="NIFTY", max_length=30)) -> dict[str, Any]:
    """Resolve Kite-master expiries and lot size for the selected derivative symbol."""
    key = _symbol(symbol)
    exchange = "BFO" if key in {"SENSEX", "BANKEX"} else "NFO"
    today = datetime.now(IST).date()
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(text("""
                SELECT expiry, MAX(lot_size) AS lot_size
                FROM instruments
                WHERE source = 'KITE' AND is_active = TRUE
                  AND exchange = :exchange
                  AND instrument_type IN ('CE', 'PE')
                  AND tradingsymbol LIKE :prefix
                  AND expiry >= :today
                GROUP BY expiry
                ORDER BY expiry
            """), {"exchange": exchange, "prefix": f"{key}%", "today": today}).mappings().all()
        expiries = [{"expiry": _expiry_label(row["expiry"]), "lot_size": int(row["lot_size"] or 0)} for row in rows]
    except SQLAlchemyError as exc:
        logger.exception("Could not resolve option expiries for %s", key)
        raise HTTPException(status_code=503, detail="Kite instrument master is unavailable.") from exc

    return {
        "symbol": key,
        "exchange": exchange,
        "expiries": expiries,
        "default_lot_size": expiries[0]["lot_size"] if expiries else None,
    }


@router.get("/fetch")
def fetch_apiplatform_option_chain(
    symbol: str = Query(default="NIFTY", max_length=30),
    expiry: str = Query(..., min_length=8, max_length=10),
    lot_size: int = Query(..., alias="lotSize", ge=1, le=100000),
) -> dict[str, Any]:
    """Fetch APIPLATFORM intelligence and replace this Option Chain view's Kite subscriptions."""
    key = _symbol(symbol)
    expiry_date = _expiry(expiry)
    expiry_text = expiry_date.strftime("%d-%m-%Y")
    try:
        payload = get_apiplatform_client().fetch_option_chain(key, expiry_text, lot_size)
    except Exception as exc:
        logger.exception("APIPLATFORM option-chain fetch failed for %s %s", key, expiry_text)
        raise HTTPException(status_code=502, detail="APIPLATFORM option-chain request failed. Check provider authentication and connectivity.") from exc

    instruments: list[dict[str, Any]] = []
    spot_token: int | None = None
    future_token: int | None = None
    try:
        exchange = "BFO" if key in {"SENSEX", "BANKEX"} else "NFO"
        with get_engine().connect() as connection:
            rows = connection.execute(text("""
                SELECT instrument_token, tradingsymbol, instrument_type, strike, lot_size
                FROM instruments
                WHERE source = 'KITE' AND is_active = TRUE
                  AND exchange = :exchange
                  AND instrument_type IN ('CE', 'PE')
                  AND expiry = :expiry
                  AND tradingsymbol LIKE :prefix
            """), {"exchange": exchange, "expiry": expiry_date, "prefix": f"{key}%"}).mappings().all()

            index_spec = _INDEX_INSTRUMENTS.get(key)
            if index_spec:
                spot_row = connection.execute(text("""
                    SELECT instrument_token
                    FROM instruments
                    WHERE source = 'KITE' AND is_active = TRUE
                      AND exchange = :exchange AND tradingsymbol = :symbol
                    LIMIT 1
                """), {"exchange": index_spec[0], "symbol": index_spec[1]}).mappings().first()
                if spot_row:
                    spot_token = int(spot_row["instrument_token"])
            else:
                # Individual equity symbols (e.g. RELIANCE, TCS) also need their
                # cash-market instrument subscribed, not just their option contracts.
                spot_row = connection.execute(text("""
                    SELECT instrument_token
                    FROM instruments
                    WHERE source = 'KITE' AND is_active = TRUE
                      AND exchange = 'NSE' AND tradingsymbol = :symbol
                      AND instrument_type = 'EQ'
                    ORDER BY instrument_version DESC
                    LIMIT 1
                """), {"symbol": key}).mappings().first()
                if not spot_row:
                    spot_row = connection.execute(text("""
                        SELECT instrument_token
                        FROM instruments
                        WHERE source = 'KITE' AND is_active = TRUE
                          AND exchange = 'BSE' AND tradingsymbol = :symbol
                          AND instrument_type = 'EQ'
                        ORDER BY instrument_version DESC
                        LIMIT 1
                    """), {"symbol": key}).mappings().first()
                if spot_row:
                    spot_token = int(spot_row["instrument_token"])

            future_row = connection.execute(text("""
                SELECT instrument_token
                FROM instruments
                WHERE source = 'KITE' AND is_active = TRUE
                  AND exchange = :exchange AND instrument_type = 'FUT'
                  AND expiry = :expiry AND tradingsymbol LIKE :prefix
                LIMIT 1
            """), {"exchange": exchange, "expiry": expiry_date, "prefix": f"{key}%"}).mappings().first()
            if future_row:
                future_token = int(future_row["instrument_token"])

        for row in rows:
            instruments.append({
                "instrument_token": int(row["instrument_token"]),
                "tradingsymbol": str(row["tradingsymbol"]),
                "side": str(row["instrument_type"]).upper(),
                "strike": float(row["strike"]),
                "lot_size": int(row["lot_size"] or lot_size),
            })

        # Use the current APIPLATFORM spot value first, then the cached Kite tick
        # when the provider payload does not include a usable spot price.
        spot_quote = live_quotes.get(spot_token) if spot_token else None
        underlying_price = payload.get("Price_S") if isinstance(payload, dict) else None
        if underlying_price in (None, "") and spot_quote:
            underlying_price = spot_quote.get("last_price")
        selected_instruments, atm_strike = _select_atm_instruments(instruments, underlying_price, 3)

        # Keep option contracts and underlying instruments in separate owners.
        # Both use QUOTE mode; other consumers retain their own requested modes.
        live_market_data.replace_subscription_group(
            "apiplatform_option_chain_options",
            [item["instrument_token"] for item in selected_instruments],
            mode="quote",
        )
        underlying_tokens = [token for token in (spot_token, future_token) if token]
        live_market_data.replace_subscription_group(
            "apiplatform_option_chain_underlying", underlying_tokens, mode="quote"
        )
        # Remove the previous single-group registration when upgrading a running process.
        live_market_data.replace_subscription_group("apiplatform_option_chain", [], mode="quote")
    except SQLAlchemyError:
        logger.exception("Could not map APIPLATFORM option strikes to Kite instruments")

    return {
        "data": payload,
        "kite_instruments": selected_instruments,
        "kite_atm_strike": atm_strike,
        "kite_subscribed_strikes": sorted(
            {item["strike"] for item in selected_instruments}
        ),
        "kite_spot_instrument_token": spot_token,
        "kite_future_instrument_token": future_token,
        "kite_market_data": live_market_data.status(),
    }


@router.get("/levels")
def option_chain_market_levels(
    symbol: str = Query(default="NIFTY", max_length=30),
    expiry: str = Query(..., min_length=8, max_length=10),
    lot_size: int = Query(..., alias="lotSize", ge=1, le=100000),
) -> dict[str, Any]:
    """Fetch only APIPLATFORM chart levels without registering Kite quote subscriptions."""
    key = _symbol(symbol)
    expiry_text = _expiry(expiry).strftime("%d-%m-%Y")
    try:
        payload = get_apiplatform_client().fetch_option_chain(key, expiry_text, lot_size)
    except Exception as exc:
        logger.exception("APIPLATFORM market-level fetch failed for %s %s", key, expiry_text)
        raise HTTPException(status_code=502, detail="APIPLATFORM market-level request failed.") from exc

    rs_status = payload.get("RS_Status", []) if isinstance(payload, dict) else []
    direction = payload.get("symbolMarketDirection", {}) if isinstance(payload, dict) else {}
    return {
        "symbol": key,
        "expiry": expiry_text,
        "RS_Status": rs_status,
        "symbolMarketDirection": direction,
        "fetch_time": payload.get("FetchTime") if isinstance(payload, dict) else None,
    }


@router.get("/live-quotes")
def option_chain_live_quotes(
    instrument_tokens: list[int] = Query(..., min_length=1, max_length=500),
) -> dict[str, Any]:
    """Read only the latest Kite WebSocket cache entries for requested tokens."""
    quotes: dict[str, Any] = {}
    for token in dict.fromkeys(instrument_tokens):
        quote = live_quotes.get(int(token))
        if not quote:
            continue
        quotes[str(token)] = {
            "instrument_token": int(token),
            "last_price": quote.get("last_price"),
            "volume": quote.get("volume_traded", quote.get("volume")),
            "oi": quote.get("oi", quote.get("open_interest")),
            "ohlc": quote.get("ohlc"),
            "exchange_timestamp": quote.get("exchange_timestamp"),
            "received_at": quote.get("received_at"),
            "source": "KITE_WEBSOCKET",
        }
    return {
        "source": "kite_websocket_cache",
        "connected": live_market_data.status().get("connected", False),
        "quotes": quotes,
    }


@router.get("/candles")
def option_chain_intraday_candles(
    symbol: str = Query(default="NIFTY", max_length=30),
    expiry: str | None = Query(default=None, max_length=10),
    price_mode: str = Query(default="SPOT", pattern="^(SPOT|FUTURE)$"),
) -> dict[str, Any]:
    """Return recent five-minute Kite candles, falling back to the latest trading session on weekends/holidays."""
    key = _symbol(symbol)
    index_spec = _INDEX_INSTRUMENTS.get(key)

    try:
        with get_engine().connect() as connection:
            if price_mode == "SPOT":
                if index_spec:
                    row = connection.execute(text("""
                        SELECT instrument_token, tradingsymbol
                        FROM instruments
                        WHERE source = 'KITE' AND is_active = TRUE
                          AND exchange = :exchange AND tradingsymbol = :symbol
                        LIMIT 1
                    """), {"exchange": index_spec[0], "symbol": index_spec[1]}).mappings().first()
                else:
                    # APIPLATFORM swing-trade symbols are equities. Resolve the
                    # underlying cash instrument so stock charts use NSE/BSE candles.
                    row = connection.execute(text("""
                        SELECT instrument_token, exchange, tradingsymbol
                        FROM instruments
                        WHERE source = 'KITE' AND is_active = TRUE
                          AND exchange IN ('NSE', 'BSE')
                          AND tradingsymbol = :symbol
                          AND instrument_type = 'EQ'
                        ORDER BY CASE exchange WHEN 'NSE' THEN 0 ELSE 1 END
                        LIMIT 1
                    """), {"symbol": key}).mappings().first()
            else:
                if not expiry:
                    raise HTTPException(status_code=422, detail="Select an expiry for the futures chart.")
                row = connection.execute(text("""
                    SELECT instrument_token, exchange, tradingsymbol
                    FROM instruments
                    WHERE source = 'KITE' AND is_active = TRUE
                      AND exchange = :exchange AND instrument_type = 'FUT'
                      AND expiry = :expiry AND tradingsymbol LIKE :prefix
                    ORDER BY instrument_token
                    LIMIT 1
                """), {"exchange": "BFO" if key in {"SENSEX", "BANKEX"} else "NFO",
                      "expiry": _expiry(expiry), "prefix": f"{key}%"}).mappings().first()
        if not row:
            return {
                "symbol": key, "candles": [],
                "message": "The selected spot/futures instrument is not available in the Kite instrument master.",
            }

        now = datetime.now(IST)
        # A same-day-only request returns an empty list on weekends, exchange holidays,
        # or before the first candle. Look back seven calendar days so the chart can
        # display the latest completed trading session while the market is closed.
        start_date = now.date() - timedelta(days=7)
        start = datetime.combine(start_date, time(9, 15), tzinfo=IST)
        candles = get_kite_provider().historical_data(
            int(row["instrument_token"]), start, now, "5minute"
        )
        normalized = [{
            "time": item.get("date").isoformat() if hasattr(item.get("date"), "isoformat") else str(item.get("date", "")),
            "open": item.get("open"), "high": item.get("high"),
            "low": item.get("low"), "close": item.get("close"),
            "volume": item.get("volume"),
        } for item in candles]
        return {
            "symbol": key,
            "instrument_token": int(row["instrument_token"]),
            "tradingsymbol": row.get("tradingsymbol"),
            "exchange": row.get("exchange") or (index_spec[0] if index_spec else None),
            "candles": normalized,
            "requested_from": start.isoformat(),
            "requested_to": now.isoformat(),
            "session_fallback": now.weekday() >= 5 or not normalized,
        }
    except HTTPException:
        raise
    except (KiteProviderError, SQLAlchemyError) as exc:
        logger.exception("Could not load today's Kite candles for %s", key)
        raise HTTPException(status_code=502, detail="Unable to load today's intraday candles from Kite.") from exc
