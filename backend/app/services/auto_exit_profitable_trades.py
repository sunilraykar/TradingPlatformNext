"""Configurable end-of-session auto-exit for profitable PAPER option trades.

This worker deliberately does not place live Kite orders. Live execution remains
read-only until broker order placement/reconciliation is implemented.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.services import trade_terminal

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")
# A close is only attempted during the final minutes, while the quote is still fresh.
_WINDOW_START = time(15, 30)
_WINDOW_END = time(15, 32)
_MAX_QUOTE_AGE_SECONDS = 120


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_zerodha_option_charges(
    *,
    exchange: str,
    buy_premium: Decimal,
    sell_premium: Decimal,
    quantity: int,
) -> dict[str, Decimal]:
    """Estimate round-trip Indian listed-options charges using Zerodha retail rates.

    buy_premium and sell_premium are per-unit premiums for the entry/exit legs.
    The calculation assumes one executed entry order and one executed exit order.
    """
    exchange = exchange.upper()
    if exchange not in {"NFO", "BFO"}:
        raise ValueError("Auto Exit currently supports NSE/BSE listed options only")
    if quantity <= 0 or buy_premium < 0 or sell_premium < 0:
        raise ValueError("Premiums must be non-negative and quantity must be positive")

    buy_turnover = buy_premium * quantity
    sell_turnover = sell_premium * quantity
    brokerage = Decimal("40.00")  # ₹20 per executed order, entry + exit
    stt = sell_turnover * Decimal("0.0015")
    transaction_rate = Decimal("0.0003553") if exchange == "NFO" else Decimal("0.000325")
    transaction = (buy_turnover + sell_turnover) * transaction_rate
    sebi = (buy_turnover + sell_turnover) * Decimal("0.000001")
    stamp_duty = buy_turnover * Decimal("0.00003")
    gst = (brokerage + transaction + sebi) * Decimal("0.18")
    total = sum((brokerage, stt, transaction, sebi, stamp_duty, gst), Decimal(0))
    return {
        "brokerage": _money(brokerage),
        "stt": _money(stt),
        "exchange_transaction_charges": _money(transaction),
        "sebi_charges": _money(sebi),
        "stamp_duty": _money(stamp_duty),
        "gst": _money(gst),
        "total": _money(total),
    }


def _enabled() -> bool:
    """Read the persisted UI setting; fail closed if settings cannot be read."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT setting_value FROM app_settings WHERE setting_key = :key"),
                {"key": "ui.autoExitProfitableTrades"},
            ).all()
    except SQLAlchemyError:
        logger.exception("Auto Exit setting could not be read; skipping this cycle")
        return False
    if not rows:
        return False
    value = rows[0][0]
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return False
    return decoded is True or (isinstance(decoded, str) and decoded.strip().lower() == "true")


def _option_exchange(trade: dict[str, Any]) -> str | None:
    exchange = str(trade.get("exchange") or "NFO").upper()
    symbol = str(trade.get("instrument_symbol") or "").upper()
    instrument_type = str(trade.get("instrument_type") or "").upper()
    if exchange not in {"NFO", "BFO"}:
        return None
    if instrument_type not in {"CE", "PE", "CALL", "PUT", "OPTION"} and not symbol.endswith(("CE", "PE")):
        return None
    return exchange


def _quote_is_fresh(trade: dict[str, Any], now: datetime) -> bool:
    timestamp = trade.get("last_market_data_at") or trade.get("last_quote_at") or trade.get("quote_received_at")
    if not timestamp:
        return False
    try:
        quoted_at = datetime.fromisoformat(str(timestamp))
        if quoted_at.tzinfo is None:
            quoted_at = quoted_at.replace(tzinfo=_IST)
        return 0 <= (now - quoted_at.astimezone(_IST)).total_seconds() <= _MAX_QUOTE_AGE_SECONDS
    except (TypeError, ValueError):
        return False


def run_auto_exit_cycle(now: datetime | None = None) -> dict[str, Any]:
    """Evaluate eligible PAPER option trades once the configured switch is on."""
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
    for trade in trade_terminal.list_trades(status_filter="OPEN"):
        trade_id = str(trade.get("trade_id") or "")
        # Broker positions are intentionally excluded: this worker does not place Kite orders.
        if str(trade.get("execution_mode") or "").upper() != "PAPER":
            continue
        exchange = _option_exchange(trade)
        if exchange is None:
            skipped.append({"trade_id": trade_id, "reason": "unsupported_instrument"})
            continue
        if not _quote_is_fresh(trade, now):
            skipped.append({"trade_id": trade_id, "reason": "stale_or_missing_quote"})
            continue
        try:
            entry = Decimal(str(trade.get("average_entry_price", trade.get("entry_price"))))
            exit_price = Decimal(str(trade.get("last_price")))
            quantity = int(trade.get("open_quantity", trade.get("quantity", 0)) or 0)
            if quantity <= 0 or entry < 0 or exit_price < 0:
                raise ValueError("invalid trade price or quantity")
        except (TypeError, ValueError, ArithmeticError):
            skipped.append({"trade_id": trade_id, "reason": "invalid_trade_values"})
            continue

        direction = str(trade.get("direction") or "BUY").upper()
        if direction not in {"BUY", "SELL"}:
            skipped.append({"trade_id": trade_id, "reason": "unsupported_direction"})
            continue
        buy_premium, sell_premium = (entry, exit_price) if direction == "BUY" else (exit_price, entry)
        gross_pnl = (exit_price - entry) * quantity * (Decimal(1) if direction == "BUY" else Decimal(-1))
        try:
            charges = calculate_zerodha_option_charges(
                exchange=exchange, buy_premium=buy_premium, sell_premium=sell_premium, quantity=quantity,
            )
        except ValueError:
            skipped.append({"trade_id": trade_id, "reason": "charges_unavailable"})
            continue
        net_pnl = _money(gross_pnl - charges["total"])
        if net_pnl <= 0:
            skipped.append({"trade_id": trade_id, "reason": "net_pnl_not_positive"})
            continue

        # update_trade serializes lifecycle changes and persists a versioned close event.
        try:
            trade_terminal.update_trade(
                trade_id,
                {
                    "status": "CLOSED",
                    "open_quantity": 0,
                    "exit_price": float(exit_price),
                    "gross_realized_pnl": float(_money(gross_pnl)),
                    "realized_pnl": float(net_pnl),
                    "net_realized_pnl": float(net_pnl),
                    "auto_exit": True,
                    "auto_exit_at": now.isoformat(),
                    "auto_exit_charges": {key: float(value) for key, value in charges.items()},
                    "auto_exit_reason": "NET_PROFITABLE_AT_MARKET_CLOSE",
                },
                event_type="AUTO_EXIT_PROFITABLE_TRADE",
            )
            closed.append(trade_id)
        except (KeyError, RuntimeError, ValueError):
            logger.exception("Auto Exit failed to close trade %s", trade_id)
            skipped.append({"trade_id": trade_id, "reason": "lifecycle_close_failed"})
    result = {"enabled": True, "closed": closed, "skipped": skipped}
    logger.info("Auto Exit cycle result: %s", result)
    return result


async def run_auto_exit_scheduler() -> None:
    """Poll during the final session minutes; once closed, wait for the next session."""
    last_run_date: str | None = None
    while True:
        try:
            now = datetime.now(_IST)
            if now.date().isoformat() != last_run_date and _WINDOW_START <= now.time() <= _WINDOW_END:
                result = run_auto_exit_cycle(now)
                # If disabled early in the window, keep polling so enabling the setting
                # before the window closes can still take effect that day.
                if result.get("enabled") or now.time() >= _WINDOW_END:
                    last_run_date = now.date().isoformat()
                logger.info("Auto Exit daily run: %s", result)
        except Exception:
            logger.exception("Auto Exit scheduler cycle failed")
        await asyncio.sleep(15)
