"""Weekly APIPLATFORM LIVE symbol-master snapshot, checked once during backend startup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.session import get_engine
from app.providers.apiplatform.client import APIPLATFORMClient
from app.providers.apiplatform.registry import get_apiplatform_client

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
REFRESH_INTERVAL = timedelta(days=7)
SNAPSHOT_TYPE = "APIPLATFORM_MASTER"
SNAPSHOT_KEY = "all-symbol:LIVE"
MYSQL_LOCK_NAME = "trading_platform_apiplatform_all_symbols_live"


def refresh_symbol_master(
    *,
    client: APIPLATFORMClient | None = None,
    engine: Any | None = None,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and append a complete LIVE symbol-master snapshot if seven days are due.

    A MySQL advisory lock serializes startup checks across multiple backend workers.
    The previous successful snapshot is never deleted or overwritten on API failure.
    """
    db_engine = engine or get_engine()
    api_client = client or get_apiplatform_client()
    captured = (fetched_at or datetime.now(IST)).astimezone(IST)
    captured_db = captured.replace(tzinfo=None)

    with db_engine.connect() as connection:
        lock_result = connection.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": MYSQL_LOCK_NAME},
        ).scalar_one()
        if lock_result != 1:
            return {"saved": False, "reason": "refresh_already_running"}
        # GET_LOCK is session-scoped; commit the implicit SQLAlchemy transaction
        # without releasing the advisory lock held by this connection.
        connection.commit()

        try:
            latest = connection.execute(
                text(
                    "SELECT snapshot_id, captured_at FROM market_snapshots "
                    "WHERE snapshot_type = :snapshot_type AND snapshot_key = :snapshot_key "
                    "ORDER BY captured_at DESC LIMIT 1"
                ),
                {"snapshot_type": SNAPSHOT_TYPE, "snapshot_key": SNAPSHOT_KEY},
            ).mappings().first()
            connection.commit()

            if latest is not None:
                last_fetched = latest["captured_at"]
                if last_fetched.tzinfo is None:
                    last_fetched = last_fetched.replace(tzinfo=IST)
                else:
                    last_fetched = last_fetched.astimezone(IST)
                age = captured - last_fetched
                if age < REFRESH_INTERVAL:
                    return {
                        "saved": False,
                        "reason": "refreshed_within_seven_days",
                        "last_fetched_at": last_fetched.isoformat(),
                        "next_refresh_at": (last_fetched + REFRESH_INTERVAL).isoformat(),
                    }

            payload = api_client.fetch_all_symbols("LIVE")
            if not isinstance(payload, (dict, list)) or not payload:
                raise RuntimeError("APIPLATFORM returned an empty LIVE symbol master.")

            serialized = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
            )
            payload_hash = hashlib.sha256(serialized.encode("utf-8")).digest()
            with connection.begin():
                result = connection.execute(
                    text(
                        "INSERT INTO market_snapshots "
                        "(snapshot_type, snapshot_key, trading_date, captured_at, payload, payload_hash) "
                        "VALUES (:snapshot_type, :snapshot_key, :trading_date, :captured_at, "
                        "CAST(:payload AS JSON), :payload_hash)"
                    ),
                    {
                        "snapshot_type": SNAPSHOT_TYPE,
                        "snapshot_key": SNAPSHOT_KEY,
                        "trading_date": captured.date(),
                        "captured_at": captured_db,
                        "payload": serialized,
                        "payload_hash": payload_hash,
                    },
                )

            outcome = {
                "saved": True,
                "snapshot_id": result.lastrowid,
                "record_type": SNAPSHOT_TYPE,
                "snapshot_key": SNAPSHOT_KEY,
                "fetched_at": captured.isoformat(),
                "payload_bytes": len(serialized.encode("utf-8")),
            }
            logger.info("APIPLATFORM LIVE symbol master stored: %s", outcome)
            return outcome
        finally:
            try:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": MYSQL_LOCK_NAME},
                )
            except Exception:
                logger.exception("Could not release APIPLATFORM symbol-master MySQL lock")


async def run_startup_symbol_master_refresh(
    client: APIPLATFORMClient | None = None,
) -> None:
    """Check the seven-day freshness window once at startup; retry failures every five minutes."""
    while True:
        try:
            result = await asyncio.to_thread(refresh_symbol_master, client=client)
            logger.info("APIPLATFORM startup symbol-master check: %s", result)
            if result.get("saved") or result.get("reason") in {
                "refreshed_within_seven_days",
                "refresh_already_running",
            }:
                return
            # A lock collision means another worker is refreshing. It is safe to
            # stop this worker's startup check; the database remains authoritative.
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "APIPLATFORM symbol-master refresh failed; retrying in five minutes"
            )
            await asyncio.sleep(300)
