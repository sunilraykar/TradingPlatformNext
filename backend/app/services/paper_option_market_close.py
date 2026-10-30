"""End-of-day auto-close for all open PAPER option trades.

This worker is intentionally PAPER-only. It records a simulated exit from a fresh
market quote and never submits broker orders.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.services import trade_terminal
from app.services.auto_exit_profitable_trades import (
    _money,
    _option_exchange,
    _quote_is_fresh,
    calculate_zerodha_option_charges,
)

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")
_WINDOW_START = time(15, 25)
_WINDOW_END = time(15, 29, 30)


def _enabled() -> bool:
    """Read the dedicated setting; fail closed when unavailable."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT setting_value FROM app_settings WHERE setting_key = :key"),
                {"key": "ui.autoClosePaperOptionsAtMarketClose"},
            ).all()
    except SQLAlchemyError:
        logger.exception("Paper market-close setting could not be read")
        return False
    if not rows:
        return False
    try:
        value = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
    except (TypeError, ValueError):
        return False
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def run_market_close_cycle(now: datetime | None = None) -> dict[str, Any]:
    """Close eligible PAPER option trades using the latest validated quote."""
    now = now or datetime.now(_IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_IST)
    now = now.astimezone(_IST)
    if now.weekday() >= 5 or not (_WINDOW_START <= now.time() <= _WINDOW_END):
        return {"enabled": False, "reason": "outside_market_close_window", "closed": [], "skipped": []}
    if not _enabled():
        return {"enabled": False, "reason": "setting_disabled_or_unavailable", "closed": [], "skipped": []}

    closed: list[str] = []
    skipped: list[dict[str, str]] = []
    for listed_trade in trade_terminal.list_trades(status_filter="OPEN"):
        trade_id = str(listed_trade.get("trade_id") or "")
        if not trade_id:
            continue
        # The list is only a candidate scan. All decisions and calculations use
        # one fresh read immediately before closing, never the earlier list copy.
        try:
            current = trade_terminal.get_trade(trade_id)
            if not current or str(current.get("status", "OPEN")).upper() != "OPEN":
                skipped.append({"trade_id": trade_id, "reason": "trade_no_longer_open"})
                continue
            if str(current.get("execution_mode") or "").upper() != "PAPER":
                continue
            exchange = _option_exchange(current)
            if exchange is None:
                skipped.append({"trade_id": trade_id, "reason": "unsupported_instrument"})
                continue
            if not _quote_is_fresh(current, now):
                skipped.append({"trade_id": trade_id, "reason": "missing_or_stale_quote"})
                continue

            entry_raw = current.get("average_entry_price")
            if entry_raw is None:
                entry_raw = current.get("entry_price")
            entry = Decimal(str(entry_raw))
            exit_price = Decimal(str(current.get("last_price")))
            quantity = int(current.get("open_quantity", current.get("quantity", 0)) or 0)
            if quantity <= 0 or entry <= 0 or exit_price <= 0:
                raise ValueError("invalid trade price or quantity")
            direction = str(current.get("direction") or "BUY").upper()
            if direction not in {"BUY", "SELL"}:
                raise ValueError("invalid trade direction")

            buy_premium, sell_premium = (
                (entry, exit_price) if direction == "BUY" else (exit_price, entry)
            )
            gross_pnl = (
                (exit_price - entry)
                * quantity
                * (Decimal(1) if direction == "BUY" else Decimal(-1))
            )
            charges = calculate_zerodha_option_charges(
                exchange=exchange,
                buy_premium=buy_premium,
                sell_premium=sell_premium,
                quantity=quantity,
            )
            net_pnl = _money(gross_pnl - charges["total"])
        except (TypeError, ValueError, ArithmeticError) as exc:
            skipped.append({"trade_id": trade_id, "reason": "invalid_trade_values"})
            logger.warning("Skipping paper market-close trade %s: %s", trade_id, exc)
            continue

        try:
            # Refuse to close if the trade changed while calculations were prepared.
            latest = trade_terminal.get_trade(trade_id)
            if (
                not latest
                or str(latest.get("status", "OPEN")).upper() != "OPEN"
                or str(latest.get("execution_mode") or "").upper() != "PAPER"
            ):
                skipped.append({"trade_id": trade_id, "reason": "trade_no_longer_open"})
                continue
            if (
                latest.get("last_market_data_at") != current.get("last_market_data_at")
                or latest.get("last_price") != current.get("last_price")
            ):
                skipped.append({"trade_id": trade_id, "reason": "quote_changed_during_close"})
                continue
            if not _quote_is_fresh(latest, now):
                skipped.append({"trade_id": trade_id, "reason": "quote_became_stale"})
                continue

            trade_terminal.update_trade(
                trade_id,
                {
                    "status": "CLOSED",
                    "open_quantity": 0,
                    "exit_price": float(exit_price),
                    "average_exit_price": float(exit_price),
                    "gross_realized_pnl": float(_money(gross_pnl)),
                    "realized_pnl": float(net_pnl),
                    "net_realized_pnl": float(net_pnl),
                    "auto_exit": True,
                    "auto_exit_at": now.isoformat(),
                    "auto_exit_reason": "MARKET_CLOSE",
                    "exit_reason": "Paper option auto-closed at market close",
                    "exit_method": "PAPER_MARKET_CLOSE_SIMULATION",
                    "auto_exit_charges": {key: float(value) for key, value in charges.items()},
                },
                event_type="PAPER_OPTION_MARKET_CLOSE",
            )
            closed.append(trade_id)
        except (KeyError, RuntimeError, ValueError):
            logger.exception("Paper market-close failed for trade %s", trade_id)
            skipped.append({"trade_id": trade_id, "reason": "lifecycle_close_failed"})

    result = {"enabled": True, "closed": closed, "skipped": skipped}
    logger.info("Paper option market-close cycle: %s", result)
    return result


async def run_market_close_scheduler() -> None:
    """Check every 15 seconds in the close window; unresolved trades are retried."""
    while True:
        try:
            now = datetime.now(_IST)
            if now.weekday() < 5 and _WINDOW_START <= now.time() <= _WINDOW_END:
                run_market_close_cycle(now)
        except Exception:
            logger.exception("Paper option market-close scheduler cycle failed")
        await asyncio.sleep(15)
