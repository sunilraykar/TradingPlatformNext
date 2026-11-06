"""Scheduled Telegram notifications for open-trade P&L and trading summaries."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.session import get_engine
from app.services import trade_terminal
from app.services.notification_events import (
    enqueue_notification,
    get_rule_state,
    notifications_enabled,
    set_rule_state,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_PNL_THRESHOLD = 1000.0


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _setting(key: str, default: Any) -> Any:
    with get_engine().connect() as connection:
        row = connection.execute(text(
            "SELECT setting_value FROM app_settings WHERE setting_key = :key"
        ), {"key": "ui." + key}).first()
    return _decode(row[0]) if row else default


def _enabled(key: str, default: bool = True) -> bool:
    return bool(_setting(key, default))


def _pnl_threshold() -> float:
    try:
        value = float(_setting("telegramPnlAlertThreshold", DEFAULT_PNL_THRESHOLD))
        return value if value > 0 else DEFAULT_PNL_THRESHOLD
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_PNL_THRESHOLD


def _trade_pnl(trade: dict[str, Any]) -> tuple[float | None, str]:
    mode = str(trade.get("execution_mode") or "").upper()
    if mode == "LIVE":
        value = trade.get("broker_pnl")
        try:
            return (float(value), "Broker-reported P&L") if value is not None else (None, "Broker-reported P&L")
        except (TypeError, ValueError, OverflowError):
            return None, "Broker-reported P&L"
    entry = trade.get("average_entry_price", trade.get("entry_price"))
    ltp = trade.get("last_price")
    quantity = trade.get("quantity")
    if entry is None or ltp is None or quantity is None:
        return None, "Unrealized P&L"
    difference = float(ltp) - float(entry)
    if str(trade.get("direction") or "BUY").upper() == "SELL":
        difference = -difference
    return difference * abs(float(quantity)), "Unrealized P&L"


def _queue(*, event_type: str, category: str, source: str, text_body: str,
           dedupe: str, entity_type: str = "SYSTEM", entity_id: str = "scheduled") -> None:
    enqueue_notification(
        event_type=event_type, category=category, source=source,
        entity_type=entity_type, entity_id=entity_id,
        payload={"telegram_text": text_body},
        deduplication_key=dedupe,
    )


def _process_pnl_alerts(now: datetime) -> int:
    if not notifications_enabled() or not _enabled("telegramPnlAlertsEnabled"):
        return 0
    threshold = _pnl_threshold()
    sent = 0
    for trade in trade_terminal.list_trades(status_filter="OPEN"):
        pnl, label = _trade_pnl(trade)
        if pnl is None:
            continue
        side = "PROFIT" if pnl >= threshold else "LOSS" if pnl <= -threshold else None
        state_key = f"telegram:pnl-alert:{trade['trade_id']}"
        state = get_rule_state(state_key) or {}
        previous_side = state.get("alerted_side")
        if side is None:
            if previous_side and _enabled("telegramPnlAlertRearmEnabled"):
                set_rule_state(state_key, {
                    "alerted_side": None, "rearmed_at": now.isoformat(),
                    "alert_count": int(state.get("alert_count") or 0),
                })
            continue
        if side == previous_side:
            continue
        symbol = trade.get("instrument_symbol") or trade.get("underlying") or "Unknown"
        direction = str(trade.get("direction") or "").upper()
        body = (
            f"{'🟢' if side == 'PROFIT' else '🔴'} TRADE P&L ALERT\n"
            f"Symbol: {symbol}\nDirection: {direction}\n"
            f"Quantity: {trade.get('quantity', 'N/A')}\n"
            f"{label}: ₹{pnl:+,.2f}\n"
            f"Alert threshold: ₹{threshold:,.2f}\n"
            f"Time: {now.strftime('%d %b %Y, %I:%M:%S %p IST')}"
        )
        alert_count = int(state.get("alert_count") or 0) + 1
        _queue(
            event_type="TRADE_PNL_THRESHOLD",
            category="TRADE",
            source="TRADE_NOTIFICATION_SCHEDULER",
            text_body=body,
            dedupe=f"trade-pnl:{trade['trade_id']}:{side}:{now.date().isoformat()}:{alert_count}",
            entity_type="TRADE",
            entity_id=str(trade["trade_id"]),
        )
        set_rule_state(state_key, {
            "alerted_side": side, "alerted_at": now.isoformat(),
            "threshold": threshold, "alert_count": alert_count,
        })
        sent += 1
    return sent


def _active_trade_summary(now: datetime) -> str | None:
    trades = trade_terminal.list_trades(status_filter="OPEN")
    if not trades:
        return None
    lines = [f"📊 ACTIVE TRADE SUMMARY · {now.strftime('%I:%M %p IST')}", f"Open trades: {len(trades)}"]
    total = 0.0
    known = 0
    for trade in trades[:20]:
        pnl, _label = _trade_pnl(trade)
        symbol = trade.get("instrument_symbol") or trade.get("underlying") or "Unknown"
        value = f"₹{pnl:+,.2f}" if pnl is not None else "N/A"
        if pnl is not None:
            total += pnl
            known += 1
        lines.append(f"• {symbol}: {value}")
    if len(trades) > 20:
        lines.append(f"…and {len(trades) - 20} more")
    if known:
        lines.append(f"Combined available P&L: ₹{total:+,.2f}")
    return "\n".join(lines)


def _daily_summary(now: datetime) -> str:
    trades = trade_terminal.list_trades()
    today = now.date().isoformat()
    opened = [t for t in trades if str(t.get("created_at") or "").startswith(today)]
    closed = [t for t in trades if str(t.get("closed_at") or "").startswith(today)]
    active = [t for t in trades if str(t.get("status") or "OPEN").upper() == "OPEN"]
    realized_total = 0.0
    realized_count = 0
    lines = [f"📘 DAILY TRADING SUMMARY · {now.strftime('%d %b %Y')}",
             f"Trades opened: {len(opened)}", f"Trades closed: {len(closed)}",
             f"Currently open: {len(active)}"]
    for trade in closed:
        pnl = trade.get("net_realized_pnl", trade.get("gross_realized_pnl"))
        if pnl is not None:
            realized_total += float(pnl)
            realized_count += 1
    if realized_count:
        lines.append(f"Available realized P&L (gross unless net supplied): ₹{realized_total:+,.2f}")
    else:
        lines.append("Available realized P&L: N/A")
    for trade in active[:10]:
        pnl, _ = _trade_pnl(trade)
        symbol = trade.get("instrument_symbol") or trade.get("underlying") or "Unknown"
        lines.append(f"Open · {symbol}: {'₹'+format(pnl, '+,.2f') if pnl is not None else 'P&L N/A'}")
    return "\n".join(lines)


async def run_scheduler() -> None:
    """Run threshold checks, 15-minute active summaries, and 15:45 IST daily summaries."""
    while True:
        try:
            now = datetime.now(IST)
            if notifications_enabled() and now.weekday() < 5:
                _process_pnl_alerts(now)
                start = datetime.strptime(str(_setting("telegramActiveSummaryStartTime", "09:15")), "%H:%M").time()
                end = datetime.strptime(str(_setting("telegramActiveSummaryEndTime", "15:30")), "%H:%M").time()
                interval = int(_setting("telegramActiveSummaryIntervalMinutes", 15))
                in_market = start <= now.time() < end
                elapsed_from_start = (now.hour * 60 + now.minute) - (start.hour * 60 + start.minute)
                if _enabled("telegramActiveSummaryEnabled") and in_market and elapsed_from_start % interval == 0:
                    bucket = now.strftime("%Y-%m-%d-%H-%M")
                    state_key = "telegram:active-summary:last-bucket"
                    state = get_rule_state(state_key) or {}
                    if state.get("bucket") != bucket:
                        body = _active_trade_summary(now)
                        if body:
                            _queue(
                                event_type="ACTIVE_TRADE_SUMMARY", category="TRADE",
                                source="TRADE_NOTIFICATION_SCHEDULER", text_body=body,
                                dedupe=f"active-trade-summary:{bucket}",
                            )
                        set_rule_state(state_key, {"bucket": bucket, "checked_at": now.isoformat()})
                daily_time = datetime.strptime(str(_setting("telegramDailySummaryTime", "15:45")), "%H:%M").time()
                if _enabled("telegramDailySummaryEnabled") and now.time() >= daily_time:
                    state_key = "telegram:daily-summary:last-date"
                    state = get_rule_state(state_key) or {}
                    today = now.date().isoformat()
                    if state.get("date") != today:
                        _queue(
                            event_type="DAILY_TRADING_SUMMARY", category="TRADE",
                            source="TRADE_NOTIFICATION_SCHEDULER",
                            text_body=_daily_summary(now),
                            dedupe=f"daily-trading-summary:{today}",
                        )
                        set_rule_state(state_key, {"date": today, "sent_at": now.isoformat()})
        except Exception:
            logger.exception("Telegram scheduled notification cycle failed")
        await asyncio.sleep(max(5, min(300, int(_setting("telegramPnlAlertCheckSeconds", 15)))))
