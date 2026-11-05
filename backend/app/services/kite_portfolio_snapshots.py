"""Daily, low-frequency Kite holdings snapshots for historical portfolio analytics."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.registry import get_kite_provider

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_HOUR = 15
SNAPSHOT_MINUTE = 40


def ensure_portfolio_snapshot_schema() -> None:
    statement = """
    CREATE TABLE IF NOT EXISTS kite_portfolio_snapshots (
      snapshot_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      account_id VARCHAR(64) NOT NULL,
      snapshot_date DATE NOT NULL,
      captured_at DATETIME(6) NOT NULL,
      holdings_count INT UNSIGNED NOT NULL DEFAULT 0,
      invested_value DECIMAL(20,2) NOT NULL DEFAULT 0,
      current_value DECIMAL(20,2) NOT NULL DEFAULT 0,
      total_pnl DECIMAL(20,2) NOT NULL DEFAULT 0,
      day_pnl DECIMAL(20,2) NOT NULL DEFAULT 0,
      holdings_json JSON NOT NULL,
      created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
      UNIQUE KEY uq_kite_portfolio_account_date (account_id, snapshot_date),
      KEY ix_kite_portfolio_date (snapshot_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with get_engine().begin() as connection:
        connection.execute(text(statement))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def capture_daily_portfolio_snapshot() -> dict[str, Any]:
    """Fetch Kite holdings once and persist an idempotent end-of-day account snapshot."""
    provider = get_kite_provider()
    profile = provider.profile()
    account_id = str(profile.get("user_id") or "").strip()
    if not account_id:
        raise RuntimeError("Kite profile did not include a user_id; snapshot was not saved")
    holdings = provider.holdings()
    if not isinstance(holdings, list):
        raise RuntimeError("Kite holdings response was not a list")

    rows = []
    invested = current = total_pnl = day_pnl = 0.0
    for item in holdings:
        if not isinstance(item, dict):
            continue
        quantity = _number(item.get("quantity"))
        average = _number(item.get("average_price"))
        ltp = _number(item.get("last_price"))
        pnl = _number(item.get("pnl"))
        close = item.get("close_price")
        explicit_day = item.get("day_pnl")
        per_day = _number(explicit_day) if explicit_day is not None else (
            (ltp - _number(close)) * quantity if close is not None else 0.0
        )
        invested += abs(quantity) * average
        current += abs(quantity) * ltp
        total_pnl += pnl
        day_pnl += per_day
        rows.append({
            "symbol": str(item.get("tradingsymbol") or ""),
            "exchange": str(item.get("exchange") or ""),
            "quantity": quantity,
            "average_price": average,
            "last_price": ltp,
            "invested_value": round(abs(quantity) * average, 2),
            "current_value": round(abs(quantity) * ltp, 2),
            "pnl": pnl,
            "day_pnl": per_day,
            "close_price": _number(close) if close is not None else None,
            "instrument_token": item.get("instrument_token"),
        })

    now = datetime.now(IST)
    with get_engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO kite_portfolio_snapshots
              (account_id, snapshot_date, captured_at, holdings_count, invested_value,
               current_value, total_pnl, day_pnl, holdings_json)
            VALUES
              (:account_id, :snapshot_date, :captured_at, :holdings_count, :invested_value,
               :current_value, :total_pnl, :day_pnl, CAST(:holdings_json AS JSON))
            ON DUPLICATE KEY UPDATE
              captured_at = VALUES(captured_at),
              holdings_count = VALUES(holdings_count),
              invested_value = VALUES(invested_value),
              current_value = VALUES(current_value),
              total_pnl = VALUES(total_pnl),
              day_pnl = VALUES(day_pnl),
              holdings_json = VALUES(holdings_json)
        """), {
            "account_id": account_id,
            "snapshot_date": now.date(),
            "captured_at": now.replace(tzinfo=None),
            "holdings_count": len(rows),
            "invested_value": round(invested, 2),
            "current_value": round(current, 2),
            "total_pnl": round(total_pnl, 2),
            "day_pnl": round(day_pnl, 2),
            "holdings_json": json.dumps(rows, separators=(",", ":")),
        })
    result = {
        "state": "saved", "account_id": account_id, "snapshot_date": now.date().isoformat(),
        "captured_at": now.isoformat(), "holdings_count": len(rows),
        "invested_value": round(invested, 2), "current_value": round(current, 2),
        "total_pnl": round(total_pnl, 2), "day_pnl": round(day_pnl, 2),
    }
    logger.info("Kite portfolio snapshot stored: %s", result)
    return result


def get_portfolio_history(account_id: str, period: str = "1Y") -> dict[str, Any]:
    periods = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": None}
    normalized = period.upper()
    if normalized not in periods:
        raise ValueError("period must be one of 1W, 1M, 3M, 6M, 1Y, ALL")
    cutoff = datetime.now(IST).date() - timedelta(days=periods[normalized]) if periods[normalized] else None
    query = """
      SELECT snapshot_date, captured_at, holdings_count, invested_value, current_value,
             total_pnl, day_pnl, holdings_json
      FROM kite_portfolio_snapshots WHERE account_id = :account_id
    """
    params: dict[str, Any] = {"account_id": account_id}
    if cutoff:
        query += " AND snapshot_date >= :cutoff"
        params["cutoff"] = cutoff
    query += " ORDER BY snapshot_date ASC"
    with get_engine().connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    history = []
    for row in rows:
        holdings = row["holdings_json"]
        if isinstance(holdings, str):
            holdings = json.loads(holdings)
        history.append({
            "date": row["snapshot_date"].isoformat(),
            "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
            "holdings_count": int(row["holdings_count"]),
            "invested_value": float(row["invested_value"]),
            "current_value": float(row["current_value"]),
            "total_pnl": float(row["total_pnl"]),
            "day_pnl": float(row["day_pnl"]),
            "holdings": holdings if isinstance(holdings, list) else [],
        })
    return {
        "period": normalized,
        "snapshot_count": len(history),
        "history_available_from": history[0]["date"] if history else None,
        "history": history,
        "message": None if history else "No daily snapshots yet. The first snapshot is captured after market close (15:40 IST).",
    }


async def run_daily_portfolio_snapshot_scheduler() -> None:
    """Capture once per weekday after market close; retry gently if Kite is offline."""
    while True:
        now = datetime.now(IST)
        due_at = now.replace(hour=SNAPSHOT_HOUR, minute=SNAPSHOT_MINUTE, second=0, microsecond=0)
        if now.weekday() < 5 and now >= due_at:
            try:
                profile = await asyncio.to_thread(get_kite_provider().profile)
                account_id = str(profile.get("user_id") or "").strip()
                if not account_id:
                    raise RuntimeError("Kite profile did not include an account ID")
                with get_engine().connect() as connection:
                    exists = connection.execute(text("""
                        SELECT 1 FROM kite_portfolio_snapshots
                        WHERE account_id = :account_id AND snapshot_date = :snapshot_date LIMIT 1
                    """), {"account_id": account_id, "snapshot_date": now.date()}).first()
                if not exists:
                    await asyncio.to_thread(capture_daily_portfolio_snapshot)
            except Exception:
                logger.exception("Daily Kite portfolio snapshot failed; retrying in 15 minutes")
                await asyncio.sleep(900)
                continue
            await asyncio.sleep(3600)
        else:
            next_run = due_at if now < due_at else due_at + timedelta(days=1)
            while next_run.weekday() >= 5:
                next_run += timedelta(days=1)
            await asyncio.sleep(min(300, max(30, (next_run - now).total_seconds())))
