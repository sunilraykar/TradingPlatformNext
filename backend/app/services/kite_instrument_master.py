"""Daily Kite instrument-master refresh into MySQL with runtime diagnostics."""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, inspect, text

from app.db.session import get_engine
from app.providers.kite.registry import get_kite_provider

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
REFRESH_HOUR = 8
REFRESH_MINUTE = 0
_REFRESH_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_status: dict[str, object] = {
    "state": "Scheduled",
    "last_attempt_at": None,
    "last_success_at": None,
    "next_scheduled_at": None,
    "downloaded": None,
    "stored": None,
    "instrument_version": None,
    "last_error": None,
}


def _set_status(**changes: object) -> None:
    with _STATUS_LOCK:
        _status.update(changes)


def instrument_master_status() -> dict[str, object]:
    """Return scheduler and latest refresh status, without reading quote/cache data."""
    now = datetime.now(IST)
    next_run = now.replace(hour=REFRESH_HOUR, minute=REFRESH_MINUTE, second=0, microsecond=0)
    if now >= next_run:
        next_run += timedelta(days=1)
    with _STATUS_LOCK:
        snapshot = dict(_status)
    snapshot["schedule_timezone"] = "Asia/Kolkata"
    snapshot["schedule_time"] = "08:00"
    snapshot["next_scheduled_at"] = snapshot.get("next_scheduled_at") or next_run.isoformat()
    return snapshot


def refresh_instrument_master() -> dict[str, object]:
    """Download, validate and atomically replace only the KITE rows in MySQL."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise RuntimeError("An instrument-master refresh is already running")
    _set_status(state="Running", last_attempt_at=datetime.now(IST).isoformat(), last_error=None)
    try:
        records = get_kite_provider().instruments()
        if not isinstance(records, list) or not records:
            raise RuntimeError("Kite returned an empty instrument master; refusing replacement")

        required = {"instrument_token", "tradingsymbol", "exchange"}
        if not required.issubset(records[0].keys()):
            raise RuntimeError("Kite instrument master is missing required fields")

        valid: list[dict[str, object]] = []
        for item in records:
            try:
                token = int(item.get("instrument_token") or 0)
                symbol = str(item.get("tradingsymbol") or "").strip()
                exchange = str(item.get("exchange") or "").strip().upper()
            except (TypeError, ValueError):
                continue
            if token <= 0 or not symbol or not exchange:
                continue

            # Kite sends empty expiry strings for cash instruments; MySQL DATE
            # columns require NULL instead of ''. Normalize every record before
            # opening the replacement transaction.
            normalized = dict(item)
            raw_expiry = normalized.get("expiry")
            if raw_expiry is None or raw_expiry == "":
                normalized["expiry"] = None
            elif isinstance(raw_expiry, datetime):
                normalized["expiry"] = raw_expiry.date()
            elif isinstance(raw_expiry, date):
                normalized["expiry"] = raw_expiry
            elif isinstance(raw_expiry, str):
                try:
                    normalized["expiry"] = date.fromisoformat(raw_expiry.strip()) if raw_expiry.strip() else None
                except ValueError:
                    continue

            valid.append({
                **normalized,
                "instrument_token": token,
                "tradingsymbol": symbol,
                "exchange": exchange,
            })

        if len(valid) < 1000:
            raise RuntimeError(f"Instrument master validation failed: only {len(valid)} usable rows")

        engine = get_engine()
        inspector = inspect(engine)
        if not inspector.has_table("instruments"):
            raise RuntimeError("MySQL instruments table does not exist")
        columns = {column["name"] for column in inspector.get_columns("instruments")}
        missing = required - columns
        if missing:
            raise RuntimeError("MySQL instruments table missing columns: " + ", ".join(sorted(missing)))
        if "source" not in columns:
            raise RuntimeError("MySQL instruments table has no source column; refusing unsafe full-table replacement")

        with engine.begin() as connection:
            current_version = 1
            if "instrument_version" in columns:
                current_version = int(connection.execute(text(
                    "SELECT COALESCE(MAX(instrument_version), 0) + 1 FROM instruments"
                )).scalar_one())
            rows = []
            for item in valid:
                row = {key: value for key, value in item.items() if key in columns}
                row["source"] = "KITE"
                if "is_active" in columns:
                    row["is_active"] = True
                if "instrument_version" in columns:
                    row["instrument_version"] = current_version
                rows.append(row)
            # All rows use the same column set, avoiding executemany shape mismatches.
            insert_columns = sorted(set.intersection(*(set(row) for row in rows)))
            rows = [{key: row.get(key) for key in insert_columns} for row in rows]
            table = Table("instruments", MetaData(), autoload_with=connection)
            connection.execute(text("DELETE FROM instruments WHERE source = 'KITE'"))
            connection.execute(table.insert(), rows)

        result = {
            "state": "Success",
            "downloaded": len(records),
            "stored": len(valid),
            "instrument_version": current_version,
            "last_success_at": datetime.now(IST).isoformat(),
            "last_error": None,
        }
        _set_status(**result)
        logger.info("Kite instrument master refreshed: %s", result)
        return result
    except Exception as exc:
        _set_status(state="Failed", last_error=str(exc))
        logger.exception("Kite instrument master refresh failed; prior committed master was retained")
        raise
    finally:
        _REFRESH_LOCK.release()


async def run_daily_instrument_refresh_scheduler() -> None:
    """Refresh once daily at 08:00 IST; retry every five minutes after failures."""
    while True:
        now = datetime.now(IST)
        today_at_refresh = now.replace(hour=REFRESH_HOUR, minute=REFRESH_MINUTE, second=0, microsecond=0)
        due = now >= today_at_refresh
        last_success = instrument_master_status().get("last_success_at")
        try:
            succeeded_today = bool(last_success) and datetime.fromisoformat(str(last_success)).astimezone(IST).date() == now.date()
        except (TypeError, ValueError):
            succeeded_today = False

        if succeeded_today:
            next_run = today_at_refresh + timedelta(days=1)
        elif due:
            next_run = now + timedelta(minutes=5)
        else:
            next_run = today_at_refresh
        _set_status(next_scheduled_at=next_run.isoformat())

        if due and not succeeded_today:
            try:
                await asyncio.to_thread(refresh_instrument_master)
            except Exception:
                # Log failures and retry after five minutes; the prior master remains intact.
                logger.exception("Scheduled Kite instrument-master refresh failed; retrying in five minutes")
        await asyncio.sleep(300)
