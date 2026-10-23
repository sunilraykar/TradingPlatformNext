"""Kite instrument-master download and MySQL persistence."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.registry import get_kite_provider


UPSERT_INSTRUMENT = text(
    """
    INSERT INTO instruments (
        instrument_token, exchange, tradingsymbol, name, instrument_type,
        segment, expiry, strike, lot_size, tick_size, source, instrument_version, is_active
    ) VALUES (
        :instrument_token, :exchange, :tradingsymbol, :name, :instrument_type,
        :segment, :expiry, :strike, :lot_size, :tick_size, 'KITE', :instrument_version, TRUE
    )
    ON DUPLICATE KEY UPDATE
        exchange = VALUES(exchange),
        tradingsymbol = VALUES(tradingsymbol),
        name = VALUES(name),
        instrument_type = VALUES(instrument_type),
        segment = VALUES(segment),
        expiry = VALUES(expiry),
        strike = VALUES(strike),
        lot_size = VALUES(lot_size),
        tick_size = VALUES(tick_size),
        source = 'KITE',
        instrument_version = VALUES(instrument_version),
        is_active = TRUE
    """
)


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def normalize_instrument(item: dict[str, Any], version: int) -> dict[str, Any]:
    """Convert a Kite instrument record into a database-ready row."""
    token = int(item.get("instrument_token") or 0)
    exchange = str(item.get("exchange") or "").strip()
    symbol = str(item.get("tradingsymbol") or "").strip()
    if token <= 0 or not exchange or not symbol:
        raise ValueError("Kite instrument is missing token, exchange, or trading symbol")
    return {
        "instrument_token": token,
        "exchange": exchange,
        "tradingsymbol": symbol,
        "name": item.get("name") or None,
        "instrument_type": item.get("instrument_type") or None,
        "segment": item.get("segment") or None,
        "expiry": _as_date(item.get("expiry")),
        "strike": item.get("strike") or 0,
        "lot_size": item.get("lot_size") or None,
        "tick_size": item.get("tick_size") or None,
        "instrument_version": version,
    }


def import_instrument_master() -> dict[str, Any]:
    """Download the complete Kite master and atomically replace the active KITE set."""
    provider = get_kite_provider()
    records = provider.instruments()
    if not records:
        raise ValueError("Kite returned an empty instrument master; existing data was not changed")

    version = int(datetime.now(UTC).timestamp())
    rows = [normalize_instrument(item, version) for item in records]
    tokens = [row["instrument_token"] for row in rows]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Kite instrument master contains duplicate instrument tokens")

    try:
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE instruments SET is_active = FALSE WHERE source = 'KITE'"))
            connection.execute(UPSERT_INSTRUMENT, rows)
    except SQLAlchemyError as exc:
        raise RuntimeError("Could not persist Kite instrument master to MySQL") from exc

    return {
        "status": "completed",
        "source": "KITE",
        "instrument_version": version,
        "imported_count": len(rows),
        "imported_at": datetime.now(UTC).isoformat(),
    }
