"""Persist the complete AutoTrender index-price cache to MySQL once per day at 15:30 IST."""

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
from app.services.autotrender_cache import AutoTrenderCache

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_TIME = time(15, 30)
SNAPSHOT_TYPE = "AUTOTRENDER_CACHE"
SNAPSHOT_KEY = "index-price"
INDEX_CATEGORIES = {"NIFTY": "Nifty", "BANKNIFTY": "BankNifty", "FINNIFTY": "FinNifty", "MIDCPNIFTY": "MidcpNifty", "SENSEX": "Sensex", "BANKEX": "Bankex"}


def _next_run(now: datetime) -> datetime:
    """Return the next 15:30 IST run time, never a past time."""
    local_now = now.astimezone(IST)
    target = datetime.combine(local_now.date(), SNAPSHOT_TIME, tzinfo=IST)
    if local_now >= target:
        target += timedelta(days=1)
    return target


def persist_cache_snapshot(
    cache: AutoTrenderCache,
    *,
    captured_at: datetime | None = None,
    engine: Any | None = None,
    auth: AutoTrenderAuth | None = None,
) -> dict[str, Any]:
    """Store the entire upstream cache payload once per IST trading date.

    The cache's presentation-only metadata is excluded; the complete AutoTrender
    response payload itself is retained in market_snapshots.payload.
    """
    captured = (captured_at or datetime.now(IST)).astimezone(IST)
    payload = cache.get_index_price()
    upstream_payload: dict[str, Any] = {
        "index_price": {key: value for key, value in payload.items() if key != "cache"},
        "indices": {},
        "capture_interval_minutes": 5,
    }
    # Preserve the Live Data datasets for every supported index.
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
    db_engine = engine or get_engine()

    with db_engine.begin() as connection:
        existing = connection.execute(
            text(
                "SELECT snapshot_id FROM market_snapshots "
                "WHERE snapshot_type = :snapshot_type AND snapshot_key = :snapshot_key "
                "AND trading_date = :trading_date LIMIT 1"
            ),
            {
                "snapshot_type": SNAPSHOT_TYPE,
                "snapshot_key": SNAPSHOT_KEY,
                "trading_date": trading_date,
            },
        ).first()
        if existing:
            return {
                "saved": False,
                "reason": "already_saved_for_date",
                "trading_date": trading_date.isoformat(),
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
        "Persisted AutoTrender cache snapshot for %s (%s bytes)",
        trading_date.isoformat(),
        len(serialized.encode("utf-8")),
    )
    return {
        "saved": True,
        "trading_date": trading_date.isoformat(),
        "record_type": SNAPSHOT_TYPE,
        "snapshot_key": SNAPSHOT_KEY,
        "payload_bytes": len(serialized.encode("utf-8")),
    }


async def run_daily_snapshot_scheduler(cache: AutoTrenderCache, auth: AutoTrenderAuth | None = None) -> None:
    """Run the AutoTrender cache snapshot every day at 15:30 Asia/Kolkata."""
    while True:
        now = datetime.now(IST)
        target = _next_run(now)
        await asyncio.sleep(max(0.0, (target - now).total_seconds()))
        try:
            result = await asyncio.to_thread(
                persist_cache_snapshot,
                cache,
                captured_at=target,
                auth=auth,
            )
            logger.info("AutoTrender daily cache snapshot result: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AutoTrender daily cache snapshot failed")
