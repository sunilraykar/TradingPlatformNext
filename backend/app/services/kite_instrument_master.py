"""Daily Kite instrument-master refresh into MySQL."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text

from app.db.session import get_engine
from app.providers.kite.registry import get_kite_provider

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
REFRESH_HOUR = 8
REFRESH_MINUTE = 0


def refresh_instrument_master() -> dict[str, object]:
    """Download the full Kite master and atomically replace KITE rows in MySQL.

    The existing master remains intact if download, validation, or insertion fails.
    The destination table is reflected at runtime to map fields supported by its schema.
    """
    records = get_kite_provider().instruments()
    if not records:
        raise RuntimeError("Kite returned an empty instrument master; refusing replacement")

    required = {"instrument_token", "tradingsymbol", "exchange"}
    if not required.issubset(records[0].keys()):
        raise RuntimeError("Kite instrument master is missing required fields")

    valid = []
    for item in records:
        try:
            token = int(item.get("instrument_token") or 0)
            symbol = str(item.get("tradingsymbol") or "").strip()
            exchange = str(item.get("exchange") or "").strip().upper()
        except (TypeError, ValueError):
            continue
        if token <= 0 or not symbol or not exchange:
            continue
        valid.append({**item, "instrument_token": token, "tradingsymbol": symbol, "exchange": exchange})

    if not valid or len(valid) < 1000:
        raise RuntimeError(f"Instrument master validation failed: only {len(valid)} usable rows")

    engine = get_engine()
    inspector = inspect(engine)
    if not inspector.has_table("instruments"):
        raise RuntimeError("MySQL instruments table does not exist")
    columns = {column["name"] for column in inspector.get_columns("instruments")}
    for required_column in ("instrument_token", "tradingsymbol", "exchange"):
        if required_column not in columns:
            raise RuntimeError(f"MySQL instruments table is missing {required_column}")

    with engine.begin() as connection:
        current_version = 1
        if "instrument_version" in columns:
            current_version = int(connection.execute(text(
                "SELECT COALESCE(MAX(instrument_version), 0) + 1 FROM instruments"
            )).scalar_one())
        rows = []
        for item in valid:
            row = {key: value for key, value in item.items() if key in columns}
            if "source" in columns:
                row["source"] = "KITE"
            if "is_active" in columns:
                row["is_active"] = True
            if "instrument_version" in columns:
                row["instrument_version"] = current_version
            rows.append(row)

        # Delete + bulk insert share one transaction: readers see the previous
        # committed master until the complete replacement commits.
        if "source" in columns:
            connection.execute(text("DELETE FROM instruments WHERE source = 'KITE'"))
        else:
            connection.execute(text("DELETE FROM instruments"))
        connection.execute(inspect(engine).dialect.statement_compiler(
            inspect(engine).dialect, None
        ).__class__ if False else text("SELECT 1")) if False else None
        from sqlalchemy import Table, MetaData
        table = Table("instruments", MetaData(), autoload_with=connection)
        connection.execute(table.insert(), rows)

    result = {
        "downloaded": len(records),
        "stored": len(valid),
        "instrument_version": current_version,
        "refreshed_at": datetime.now(IST).isoformat(),
    }
    logger.info("Kite instrument master refreshed: %s", result)
    return result


async def run_daily_instrument_refresh_scheduler() -> None:
    """Refresh once per day at 08:00 IST; retry periodically until successful."""
    last_success_date = None
    while True:
        now = datetime.now(IST)
        due = (now.hour, now.minute) >= (REFRESH_HOUR, REFRESH_MINUTE)
        if due and last_success_date != now.date():
            try:
                result = await asyncio.to_thread(refresh_instrument_master)
                last_success_date = now.date()
                logger.info("Daily Kite instrument refresh completed: %s", result)
            except Exception:
                logger.exception("Daily Kite instrument refresh failed; keeping previous master and retrying")
        await asyncio.sleep(300)
