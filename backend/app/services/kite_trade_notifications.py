"""Poll Kite's broker trade book and notify once per confirmed execution fill."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from sqlalchemy import text

from app.db.session import get_engine
from app.services.notification_events import enqueue_notification, notifications_enabled

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _setting(key: str, default):
    with get_engine().connect() as connection:
        row = connection.execute(text("SELECT setting_value FROM app_settings WHERE setting_key = :key"), {"key": "ui." + key}).first()
    if not row:
        return default
    value = row[0]
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _format_time(value: Any) -> str:
    if not value:
        return "N/A"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except (TypeError, ValueError):
        return str(value)


def process_trade_book(provider=None) -> dict[str, int]:
    """Queue durable notifications for broker-confirmed fills, never submitted orders."""
    if not notifications_enabled() or not bool(_setting("telegramLiveFillEnabled", True)):
        return {"checked": 0, "queued": 0}
    provider = provider or get_kite_provider()
    profile = provider.profile()
    account_id = str(profile.get("user_id") or "").strip()
    if not account_id:
        raise RuntimeError("Kite profile did not provide an account identity")
    fills = provider.trades()
    if not isinstance(fills, list):
        raise RuntimeError("Kite trade book response was not a list")
    queued = 0
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        fill_id = str(fill.get("trade_id") or fill.get("id") or "").strip()
        if not fill_id:
            # Do not synthesize an unstable identity from mutable fields.
            logger.warning("Skipping Kite trade fill without broker trade_id")
            continue
        symbol = str(fill.get("tradingsymbol") or "").strip()
        side = str(fill.get("transaction_type") or "").strip().upper()
        quantity = fill.get("quantity")
        price = fill.get("average_price", fill.get("price"))
        if not symbol or side not in {"BUY", "SELL"} or quantity is None or price is None:
            logger.warning("Skipping incomplete Kite trade fill trade_id=%s", fill_id)
            continue
        timestamp = fill.get("fill_timestamp") or fill.get("exchange_timestamp") or fill.get("order_timestamp")
        occurred_at = timestamp
        if timestamp:
            try:
                parsed_timestamp = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if parsed_timestamp.tzinfo is None:
                    parsed_timestamp = parsed_timestamp.replace(tzinfo=IST)
                occurred_at = parsed_timestamp
            except (TypeError, ValueError):
                occurred_at = None
        body = (
            f"{'🟢' if side == 'BUY' else '🔴'} LIVE TRADE EXECUTION\n"
            f"Account: {account_id}\n"
            f"Symbol: {symbol}\n"
            f"Exchange: {fill.get('exchange') or 'N/A'}\n"
            f"Action: {side}\n"
            f"Quantity: {quantity}\n"
            f"Execution Price: ₹{float(price):,.2f}\n"
            f"Product: {fill.get('product') or 'N/A'}\n"
            f"Order ID: {fill.get('order_id') or 'N/A'}\n"
            f"Trade ID: {fill_id}\n"
            f"Time: {_format_time(timestamp)}"
        )
        result = enqueue_notification(
            event_type="LIVE_TRADE_FILL",
            category="LIVE_TRADE",
            source="KITE_TRADE_BOOK",
            entity_type="BROKER_TRADE",
            entity_id=fill_id,
            account_id=account_id,
            payload={
                "account_id": account_id, "trade_id": fill_id,
                "order_id": str(fill.get("order_id") or ""),
                "symbol": symbol, "transaction_type": side,
                "quantity": quantity, "average_price": price,
                "telegram_text": body,
            },
            deduplication_key=f"kite-live-fill:{account_id}:{fill_id}",
            occurred_at=occurred_at,
        )
        if result.get("queued"):
            queued += 1
    return {"checked": len(fills), "queued": queued}


async def run_scheduler() -> None:
    """Poll broker fills at a conservative interval; delivery remains in the outbox worker."""
    while True:
        try:
            await asyncio.to_thread(process_trade_book)
        except KiteProviderError as exc:
            logger.warning("Kite trade-fill notification poll unavailable: %s", exc)
        except Exception:
            logger.exception("Kite trade-fill notification poll failed")
        try:
            interval = int(_setting("telegramLiveFillPollSeconds", 15))
        except (TypeError, ValueError):
            interval = 15
        await asyncio.sleep(max(5, min(300, interval)))
