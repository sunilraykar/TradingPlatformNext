"""Weekly APIPLATFORM LIVE symbol-master refresh into normalized rows."""

from __future__ import annotations

import asyncio
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
MYSQL_LOCK_NAME = "trading_platform_apiplatform_all_symbols_live"


def _normalize_symbols(payload: Any) -> list[dict[str, str]]:
    """Validate and normalize the API array into one row per symbol."""
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("APIPLATFORM LIVE symbol master must be a non-empty JSON array.")

    normalized: dict[str, str] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise RuntimeError(f"APIPLATFORM symbol entry at index {index} is not an object.")

        symbol = item.get("symbol")
        category = item.get("category")
        if not isinstance(symbol, str) or not symbol.strip():
            raise RuntimeError(f"APIPLATFORM symbol entry at index {index} has no symbol.")
        if not isinstance(category, str) or not category.strip():
            raise RuntimeError(f"APIPLATFORM symbol entry at index {index} has no category.")

        symbol = symbol.strip().upper()
        category = category.strip().upper()
        previous_category = normalized.get(symbol)
        if previous_category is not None and previous_category != category:
            raise RuntimeError(
                f"APIPLATFORM returned conflicting categories for symbol {symbol}."
            )
        normalized[symbol] = category

    return [
        {"symbol": symbol, "category": category}
        for symbol, category in sorted(normalized.items())
    ]


def refresh_symbol_master(
    *,
    client: APIPLATFORMClient | None = None,
    engine: Any | None = None,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and append a complete normalized LIVE symbol snapshot every seven days.

    Every API symbol is stored as an individual row. Rows from previous weekly
    snapshots are retained; fetched_at identifies the snapshot.
    """
    db_engine = engine or get_engine()
    api_client = client or get_apiplatform_client()
    captured = fetched_at or datetime.now(IST)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=IST)
    else:
        captured = captured.astimezone(IST)
    captured_db = captured.replace(tzinfo=None)

    with db_engine.connect() as connection:
        lock_result = connection.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": MYSQL_LOCK_NAME},
        ).scalar_one()
        if lock_result != 1:
            return {"saved": False, "reason": "refresh_already_running"}
        connection.commit()

        try:
            latest_fetched_at = connection.execute(
                text("SELECT MAX(fetched_at) FROM All_symbols_MI")
            ).scalar_one()
            connection.commit()

            if latest_fetched_at is not None:
                last_fetched = latest_fetched_at
                if last_fetched.tzinfo is None:
                    last_fetched = last_fetched.replace(tzinfo=IST)
                else:
                    last_fetched = last_fetched.astimezone(IST)
                if captured - last_fetched < REFRESH_INTERVAL:
                    return {
                        "saved": False,
                        "reason": "refreshed_within_seven_days",
                        "last_fetched_at": last_fetched.isoformat(),
                        "next_refresh_at": (last_fetched + REFRESH_INTERVAL).isoformat(),
                    }

            payload = api_client.fetch_all_symbols("LIVE")
            symbols = _normalize_symbols(payload)
            rows = [
                {
                    "symbol": item["symbol"],
                    "category": item["category"],
                    "fetched_at": captured_db,
                }
                for item in symbols
            ]

            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO All_symbols_MI (symbol, category, fetched_at) "
                        "VALUES (:symbol, :category, :fetched_at)"
                    ),
                    rows,
                )

            outcome = {
                "saved": True,
                "symbol_count": len(rows),
                "fetched_at": captured.isoformat(),
            }
            logger.info("APIPLATFORM LIVE symbol master stored as normalized rows: %s", outcome)
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
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "APIPLATFORM symbol-master refresh failed; retrying in five minutes"
            )
            await asyncio.sleep(300)
