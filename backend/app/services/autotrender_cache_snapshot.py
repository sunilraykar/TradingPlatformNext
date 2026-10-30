"""Persist daily Kite index prices and AutoTrender derivatives data at 15:30 IST."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from app.db.session import get_engine
from app.providers.autotrender.auth import AutoTrenderAuth
from app.providers.kite.registry import get_kite_provider

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_TIME = time(15, 30)
SNAPSHOT_TYPE = "AUTOTRENDER_CACHE"
SNAPSHOT_KEY = "index-price"

# Resolve these exact spot-index instruments from the daily Kite instrument master.
INDEX_INSTRUMENTS = {
    "NIFTY": ("NSE", "NIFTY 50", "nifty"),
    "BANKNIFTY": ("NSE", "NIFTY BANK", "banknifty"),
    "FINNIFTY": ("NSE", "NIFTY FIN SERVICE", "finnifty"),
    "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT", "midcpnifty"),
    "SENSEX": ("BSE", "SENSEX", "sensex"),
    "BANKEX": ("BSE", "BANKEX", "bankex"),
}
INDEX_CATEGORIES = {
    "NIFTY": "Nifty",
    "BANKNIFTY": "BankNifty",
    "FINNIFTY": "FinNifty",
    "MIDCPNIFTY": "MidcpNifty",
    "SENSEX": "Sensex",
    "BANKEX": "Bankex",
}


def _next_run(now: datetime) -> datetime:
    """Return the next 15:30 IST run time, never a past time."""
    local_now = now.astimezone(IST)
    target = datetime.combine(local_now.date(), SNAPSHOT_TIME, tzinfo=IST)
    if local_now >= target:
        target += timedelta(days=1)
    return target


def _fetch_kite_index_prices(engine: Any | None = None, kite_provider: Any | None = None) -> dict[str, Any]:
    """Fetch all six underlying spot-index quotes directly from Kite REST API."""
    db_engine = engine or get_engine()
    provider = kite_provider or get_kite_provider()

    resolved: dict[str, dict[str, Any]] = {}
    with db_engine.connect() as connection:
        for index, (exchange, symbol, _) in INDEX_INSTRUMENTS.items():
            row = connection.execute(
                text(
                    "SELECT instrument_token, exchange, tradingsymbol "
                    "FROM instruments WHERE source = 'KITE' AND is_active = TRUE "
                    "AND exchange = :exchange AND tradingsymbol = :symbol "
                    "ORDER BY instrument_version DESC LIMIT 1"
                ),
                {"exchange": exchange, "symbol": symbol},
            ).mappings().first()
            if row is None:
                raise RuntimeError(
                    f"Kite instrument master is missing {exchange}:{symbol}; "
                    "refresh the Kite instrument master before snapshotting"
                )
            resolved[index] = {
                "instrument_token": int(row["instrument_token"]),
                "exchange": str(row["exchange"]),
                "tradingsymbol": str(row["tradingsymbol"]),
            }

    # Kite's quote endpoint accepts exchange:tradingsymbol identifiers.
    instruments = [
        f"{item['exchange']}:{item['tradingsymbol']}"
        for item in resolved.values()
    ]
    quotes = provider.quotes(instruments)
    data: dict[str, Any] = {}
    missing: list[str] = []
    for index, (_, _, payload_key) in INDEX_INSTRUMENTS.items():
        instrument = resolved[index]
        quote_key = f"{instrument['exchange']}:{instrument['tradingsymbol']}"
        quote = quotes.get(quote_key) if isinstance(quotes, dict) else None
        if not isinstance(quote, dict) or quote.get("last_price") is None:
            missing.append(quote_key)
            continue
        data[payload_key] = {
            "price": quote["last_price"],
            "ohlc": quote.get("ohlc"),
            "instrument_token": instrument["instrument_token"],
            "exchange": instrument["exchange"],
            "tradingsymbol": instrument["tradingsymbol"],
        }

    if missing:
        raise RuntimeError("Kite did not return quotes for: " + ", ".join(missing))
    return {"title": "Index Price", "source": "KITE", "data": data}


def persist_cache_snapshot(
    *,
    captured_at: datetime | None = None,
    engine: Any | None = None,
    auth: AutoTrenderAuth | None = None,
    kite_provider: Any | None = None,
) -> dict[str, Any]:
    """Store Kite spot-index prices and available AutoTrender derivatives datasets once per IST date.

    Index prices are sourced exclusively from Kite. AutoTrender remains the source
    for its intraday option-strength and open-interest graph datasets.
    """
    captured = (captured_at or datetime.now(IST)).astimezone(IST)
    db_engine = engine or get_engine()

    # Do not insert a partial index-price snapshot if Kite is unavailable.
    index_price = _fetch_kite_index_prices(db_engine, kite_provider)
    upstream_payload: dict[str, Any] = {
        "index_price": index_price,
        "indices": {},
        "capture_interval_minutes": 5,
    }

    if auth is not None:
        for index, category in INDEX_CATEGORIES.items():
            entry: dict[str, Any] = {}
            datasets = [("intraday", "/api/derivatives/intra-day", {"category": category, "mins": 5})]
            if index in {"FINNIFTY", "SENSEX"}:
                datasets.append(("oi_graph", "/api/derivatives/table/graph", {"category": category}))
            for dataset, endpoint, params in datasets:
                try:
                    response = auth.get(endpoint, params=params)
                    response.raise_for_status()
                    value = response.json()
                    entry[dataset] = value if isinstance(value, dict) else {"error": "Unexpected non-object response"}
                except (RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
                    entry[dataset] = {"error": f"{type(exc).__name__}: {exc}"}
            upstream_payload["indices"][index] = entry

    serialized = json.dumps(upstream_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_hash = hashlib.sha256(serialized.encode("utf-8")).digest()
    trading_date: date = captured.date()

    with db_engine.begin() as connection:
        existing = connection.execute(
            text(
                "SELECT snapshot_id, payload FROM market_snapshots "
                "WHERE snapshot_type = :snapshot_type AND snapshot_key = :snapshot_key "
                "AND trading_date = :trading_date LIMIT 1 FOR UPDATE"
            ),
            {
                "snapshot_type": SNAPSHOT_TYPE,
                "snapshot_key": SNAPSHOT_KEY,
                "trading_date": trading_date,
            },
        ).mappings().first()
        if existing:
            old_payload = existing["payload"]
            if isinstance(old_payload, str):
                try:
                    old_payload = json.loads(old_payload)
                except (TypeError, ValueError):
                    old_payload = {}
            old_index_price = old_payload.get("index_price") if isinstance(old_payload, dict) else None
            old_data = old_index_price.get("data") if isinstance(old_index_price, dict) else None
            expected_keys = {item[2] for item in INDEX_INSTRUMENTS.values()}
            if (
                isinstance(old_index_price, dict)
                and old_index_price.get("source") == "KITE"
                and isinstance(old_data, dict)
                and expected_keys.issubset(old_data.keys())
            ):
                return {
                    "saved": False,
                    "reason": "already_saved_for_date",
                    "trading_date": trading_date.isoformat(),
                }

            # Replace only an incomplete/legacy snapshot, and only after all six
            # Kite quotes and the new payload have been fetched successfully.
            connection.execute(
                text(
                    "UPDATE market_snapshots SET captured_at = :captured_at, "
                    "payload = CAST(:payload AS JSON), payload_hash = :payload_hash "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {
                    "snapshot_id": existing["snapshot_id"],
                    "captured_at": captured.replace(tzinfo=None),
                    "payload": serialized,
                    "payload_hash": payload_hash,
                },
            )
            logger.info("Repaired legacy/incomplete market snapshot %s for %s",
                        existing["snapshot_id"], trading_date.isoformat())
            return {
                "saved": True,
                "repaired": True,
                "snapshot_id": existing["snapshot_id"],
                "trading_date": trading_date.isoformat(),
                "record_type": SNAPSHOT_TYPE,
                "snapshot_key": SNAPSHOT_KEY,
                "index_price_source": "KITE",
                "payload_bytes": len(serialized.encode("utf-8")),
            }

        connection.execute(
            text(
                "INSERT INTO market_snapshots "
                "(snapshot_type, snapshot_key, trading_date, captured_at, payload, payload_hash) "
                "VALUES (:snapshot_type, :snapshot_key, :trading_date, :captured_at, "
                "CAST(:payload AS JSON), :payload_hash)"
            ),
            {
                "snapshot_type": SNAPSHOT_TYPE,
                "snapshot_key": SNAPSHOT_KEY,
                "trading_date": trading_date,
                "captured_at": captured.replace(tzinfo=None),
                "payload": serialized,
                "payload_hash": payload_hash,
            },
        )

    logger.info(
        "Persisted Kite index-price snapshot for %s (%s bytes)",
        trading_date.isoformat(),
        len(serialized.encode("utf-8")),
    )
    return {
        "saved": True,
        "trading_date": trading_date.isoformat(),
        "record_type": SNAPSHOT_TYPE,
        "snapshot_key": SNAPSHOT_KEY,
        "index_price_source": "KITE",
        "payload_bytes": len(serialized.encode("utf-8")),
    }


async def run_daily_snapshot_scheduler(auth: AutoTrenderAuth | None = None) -> None:
    """Capture at 15:30 IST; catch up on startup and retry failures every five minutes."""
    retry_seconds = 300
    while True:
        now = datetime.now(IST)
        target = datetime.combine(now.date(), SNAPSHOT_TIME, tzinfo=IST)
        if now < target:
            await asyncio.sleep((target - now).total_seconds())
            now = datetime.now(IST)
            target = datetime.combine(now.date(), SNAPSHOT_TIME, tzinfo=IST)
            if now < target:
                continue
        try:
            # If startup is after the scheduled time, capture immediately rather
            # than silently skipping the day. A failed attempt is retried below.
            result = await asyncio.to_thread(
                persist_cache_snapshot,
                captured_at=now,
                auth=auth,
            )
            logger.info("Daily market snapshot result: %s", result)
            if result.get("saved") or result.get("reason") == "already_saved_for_date":
                next_target = _next_run(datetime.now(IST))
                await asyncio.sleep(max(1.0, (next_target - datetime.now(IST)).total_seconds()))
            else:
                await asyncio.sleep(retry_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily market snapshot failed; retrying in %s seconds", retry_seconds)
            await asyncio.sleep(retry_seconds)
