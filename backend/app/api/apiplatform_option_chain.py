"""APIPLATFORM option-chain data with Kite WebSocket quote enrichment."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.providers.apiplatform.client import APIPLATFORMClientError
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
    """Fetch APIPLATFORM intelligence and register matching Kite WebSocket contracts."""
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
                    ORDER BY instrument_version DESC
                    LIMIT 1
                """), {"exchange": index_spec[0], "symbol": index_spec[1]}).mappings().first()
                if spot_row:
                    spot_token = int(spot_row["instrument_token"])

            future_row = connection.execute(text("""
                SELECT instrument_token
                FROM instruments
                WHERE source = 'KITE' AND is_active = TRUE
                  AND exchange = :exchange AND instrument_type = 'FUT'
                  AND expiry = :expiry AND tradingsymbol LIKE :prefix
                ORDER BY instrument_version DESC
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

        tokens = [item["instrument_token"] for item in instruments]
        if spot_token:
            tokens.append(spot_token)
        if future_token:
            tokens.append(future_token)
        if tokens:
            live_market_data.subscribe(tokens, mode="full")
    except SQLAlchemyError:
        logger.exception("Could not map APIPLATFORM option strikes to Kite instruments")

    return {
        "data": payload,
        "kite_instruments": instruments,
        "kite_spot_instrument_token": spot_token,
        "kite_future_instrument_token": future_token,
        "kite_market_data": live_market_data.status(),
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
    """Return today's fixed five-minute Kite candles; no historical date navigation."""
    key = _symbol(symbol)
    index_spec = _INDEX_INSTRUMENTS.get(key)
    if not index_spec:
        return {"symbol": key, "candles": [], "message": "Intraday chart is available for supported index underlyings."}

    try:
        with get_engine().connect() as connection:
            if price_mode == "SPOT":
                row = connection.execute(text("""
                    SELECT instrument_token, tradingsymbol
                    FROM instruments
                    WHERE source = 'KITE' AND is_active = TRUE
                      AND exchange = :exchange AND tradingsymbol = :symbol
                    ORDER BY instrument_version DESC LIMIT 1
                """), {"exchange": index_spec[0], "symbol": index_spec[1]}).mappings().first()
            else:
                if not expiry:
                    raise HTTPException(status_code=422, detail="Select an expiry for the futures chart.")
                row = connection.execute(text("""
                    SELECT instrument_token, tradingsymbol
                    FROM instruments
                    WHERE source = 'KITE' AND is_active = TRUE
                      AND exchange = :exchange AND instrument_type = 'FUT'
                      AND expiry = :expiry AND tradingsymbol LIKE :prefix
                    ORDER BY instrument_version DESC LIMIT 1
                """), {"exchange": "BFO" if key in {"SENSEX", "BANKEX"} else "NFO",
                      "expiry": _expiry(expiry), "prefix": f"{key}%"}).mappings().first()
        if not row:
            return {"symbol": key, "candles": [], "message": "The selected Kite instrument is not available in the instrument master."}

        now = datetime.now(IST)
        start = datetime.combine(now.date(), time(9, 15), tzinfo=IST)
        candles = get_kite_provider().historical_data(
            int(row["instrument_token"]), start, now, "5minute"
        )
        normalized = [{
            "time": item.get("date").isoformat() if hasattr(item.get("date"), "isoformat") else str(item.get("date", "")),
            "open": item.get("open"), "high": item.get("high"),
            "low": item.get("low"), "close": item.get("close"),
            "volume": item.get("volume"),
        } for item in candles]
        return {"symbol": key, "instrument_token": int(row["instrument_token"]), "candles": normalized}
    except HTTPException:
        raise
    except (KiteProviderError, SQLAlchemyError) as exc:
        logger.exception("Could not load today's Kite candles for %s", key)
        raise HTTPException(status_code=502, detail="Unable to load today's intraday candles from Kite.") from exc
