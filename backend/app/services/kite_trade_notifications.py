"""Poll Kite's broker trade book and notify once per confirmed execution fill."""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
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
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except (TypeError, ValueError):
        return str(value)


def _ensure_fill_position_schema() -> None:
    with get_engine().begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS kite_notification_processed_fills (
                account_id VARCHAR(64) NOT NULL,
                broker_trade_id VARCHAR(128) NOT NULL,
                processed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                PRIMARY KEY (account_id, broker_trade_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS kite_notification_positions (
                account_id VARCHAR(64) NOT NULL,
                position_key VARCHAR(255) NOT NULL,
                exchange VARCHAR(20) NOT NULL,
                tradingsymbol VARCHAR(120) NOT NULL,
                product VARCHAR(30) NOT NULL,
                net_quantity BIGINT NOT NULL DEFAULT 0,
                average_price DECIMAL(20,6) NOT NULL DEFAULT 0,
                realized_pnl DECIMAL(20,2) NOT NULL DEFAULT 0,
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                PRIMARY KEY (account_id, position_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))


def calculate_fill_transition(old_qty: int, old_avg: float, delta: int, price: float) -> tuple[str, float, int, int, float]:
    """Return classification, realized P&L, closed quantity, new net quantity and average."""
    new_qty = old_qty + delta
    realized = 0.0
    closed_qty = 0
    if old_qty and (old_qty > 0) != (delta > 0):
        closed_qty = min(abs(old_qty), abs(delta))
        realized = (price - old_avg) * closed_qty * (1 if old_qty > 0 else -1)
    if new_qty == 0:
        new_avg = 0.0
        classification = "FINAL EXIT"
    elif old_qty == 0 or (old_qty > 0) == (delta > 0):
        new_avg = ((abs(old_qty) * old_avg) + (abs(delta) * price)) / abs(new_qty)
        classification = "ENTRY" if old_qty == 0 else "ADD"
    elif (old_qty > 0) == (new_qty > 0):
        new_avg = old_avg
        classification = "PARTIAL EXIT"
    else:
        new_avg = price
        classification = "REVERSAL"
    return classification, realized, closed_qty, new_qty, new_avg


def _apply_fill(connection, account_id: str, fill: dict[str, Any], timestamp: Any) -> tuple[str, float | None, int, float]:
    exchange = str(fill.get("exchange") or "").upper()
    symbol = str(fill.get("tradingsymbol") or "")
    product = str(fill.get("product") or "").upper()
    key = f"{exchange}:{symbol}:{product}"
    side = str(fill.get("transaction_type") or "").upper()
    qty = int(fill.get("quantity") or 0)
    price = float(fill.get("average_price", fill.get("price")) or 0)
    if qty <= 0 or not math.isfinite(price) or price <= 0:
        raise ValueError("Kite fill quantity and price must be positive finite values")
    delta = qty if side == "BUY" else -qty
    row = connection.execute(text("""
        SELECT net_quantity, average_price FROM kite_notification_positions
        WHERE account_id=:account_id AND position_key=:position_key FOR UPDATE
    """), {"account_id": account_id, "position_key": key}).first()
    old_qty = int(row[0]) if row else 0
    old_avg = float(row[1]) if row else 0.0
    new_qty = old_qty + delta
    realized = 0.0
    closed_qty = 0
    if old_qty and (old_qty > 0) != (delta > 0):
        closed_qty = min(abs(old_qty), abs(delta))
        realized = (price - old_avg) * closed_qty * (1 if old_qty > 0 else -1)
    if new_qty == 0:
        new_avg = 0.0
        classification = "FINAL EXIT"
    elif old_qty == 0 or (old_qty > 0) == (delta > 0):
        new_avg = ((abs(old_qty) * old_avg) + (abs(delta) * price)) / abs(new_qty)
        classification = "ENTRY" if old_qty == 0 else "ADD"
    elif (old_qty > 0) == (new_qty > 0):
        new_avg = old_avg
        classification = "PARTIAL EXIT"
    else:
        new_avg = price
        classification = "REVERSAL"
    connection.execute(text("""
        INSERT INTO kite_notification_positions
          (account_id,position_key,exchange,tradingsymbol,product,net_quantity,average_price,realized_pnl)
        VALUES (:account_id,:position_key,:exchange,:symbol,:product,:net_quantity,:average_price,:realized)
        ON DUPLICATE KEY UPDATE net_quantity=VALUES(net_quantity),
          average_price=VALUES(average_price), realized_pnl=realized_pnl+VALUES(realized_pnl)
    """), {"account_id":account_id,"position_key":key,"exchange":exchange,"symbol":symbol,
           "product":product,"net_quantity":new_qty,"average_price":new_avg,"realized":realized})
    return classification, realized if closed_qty else None, new_qty, new_avg


def _initialize_daily_position_baseline(account_id: str, positions: list[dict[str, Any]], fills: list[dict[str, Any]], now: datetime) -> bool:
    """Seed current broker positions once per account/day and suppress historical fill replay."""
    baseline_key = f"telegram:kite-fill-baseline:{account_id}:{now.date().isoformat()}"
    with get_engine().begin() as connection:
        state = connection.execute(text(
            "SELECT state_json FROM notification_rule_state WHERE state_key=:key FOR UPDATE"
        ), {"key": baseline_key}).first()
        if state:
            return False
        connection.execute(text("""
            UPDATE kite_notification_positions
            SET net_quantity=0, average_price=0, realized_pnl=0
            WHERE account_id=:account_id
        """), {"account_id": account_id})
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("tradingsymbol") or "").strip()
            exchange = str(position.get("exchange") or "").upper()
            product = str(position.get("product") or "").upper()
            quantity = int(position.get("quantity") or position.get("net_quantity") or 0)
            average = float(position.get("average_price") or 0)
            if not symbol or not exchange or not product or quantity == 0:
                continue
            key = f"{exchange}:{symbol}:{product}"
            connection.execute(text("""
                INSERT INTO kite_notification_positions
                  (account_id,position_key,exchange,tradingsymbol,product,net_quantity,average_price,realized_pnl)
                VALUES (:account_id,:position_key,:exchange,:symbol,:product,:quantity,:average,0)
                ON DUPLICATE KEY UPDATE net_quantity=VALUES(net_quantity),
                  average_price=VALUES(average_price), realized_pnl=0
            """), {"account_id":account_id,"position_key":key,"exchange":exchange,
                   "symbol":symbol,"product":product,"quantity":quantity,"average":average})
        for fill in fills:
            fill_id = str(fill.get("trade_id") or fill.get("id") or "").strip()
            if fill_id:
                connection.execute(text("""
                    INSERT IGNORE INTO kite_notification_processed_fills (account_id,broker_trade_id)
                    VALUES (:account_id,:trade_id)
                """), {"account_id":account_id,"trade_id":fill_id})
        connection.execute(text("""
            INSERT INTO notification_rule_state (state_key,state_json)
            VALUES (:key,CAST(:state AS JSON))
        """), {"key":baseline_key,"state":json.dumps({
            "initialized_at": now.isoformat(),
            "open_positions_seeded": sum(1 for p in positions if isinstance(p, dict) and int(p.get("quantity") or p.get("net_quantity") or 0) != 0),
            "historical_fills_skipped": len(fills),
        })})
    return True


def process_trade_book(provider=None) -> dict[str, int]:
    """Classify confirmed fills against durable account positions and queue idempotent messages."""
    if not notifications_enabled() or not bool(_setting("telegramLiveFillEnabled", True)):
        return {"checked": 0, "queued": 0}
    provider = provider or get_kite_provider()
    profile = provider.profile()
    account_id = str(profile.get("user_id") or "").strip()
    if not account_id:
        raise RuntimeError("Kite profile did not provide an account identity")
    fills = provider.trades()
    if not isinstance(fills, list):
        raise TypeError("Kite trade book response was not a list")
    _ensure_fill_position_schema()
    positions_response = provider.positions()
    net_positions = positions_response.get("net", []) if isinstance(positions_response, dict) else None
    if not isinstance(net_positions, list):
        raise TypeError("Kite net positions response was not a list")
    now = datetime.now(IST)
    _initialize_daily_position_baseline(account_id, net_positions, fills, now)
    queued = 0
    fills = sorted(
        [item for item in fills if isinstance(item, dict)],
        key=lambda item: str(item.get("fill_timestamp") or item.get("exchange_timestamp") or item.get("order_timestamp") or ""),
    )
    for fill in fills:
        fill_id = str(fill.get("trade_id") or fill.get("id") or "").strip()
        symbol = str(fill.get("tradingsymbol") or "").strip()
        side = str(fill.get("transaction_type") or "").strip().upper()
        quantity = fill.get("quantity")
        price = fill.get("average_price", fill.get("price"))
        if not fill_id or not symbol or side not in {"BUY", "SELL"} or quantity is None or price is None:
            logger.warning("Skipping incomplete Kite trade fill id=%s symbol=%s", fill_id, symbol)
            continue
        timestamp = fill.get("fill_timestamp") or fill.get("exchange_timestamp") or fill.get("order_timestamp")
        occurred_at = timestamp
        if timestamp:
            try:
                parsed = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp))
                occurred_at = parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed
            except (TypeError, ValueError):
                occurred_at = None
        try:
            with get_engine().begin() as connection:
                dedupe = f"kite-live-fill:{account_id}:{fill_id}"
                claimed = connection.execute(text("""
                    INSERT IGNORE INTO kite_notification_processed_fills (account_id, broker_trade_id)
                    VALUES (:account_id, :trade_id)
                """), {"account_id": account_id, "trade_id": fill_id})
                if claimed.rowcount != 1:
                    continue
                classification, realized, net_qty, avg_price = _apply_fill(connection, account_id, fill, occurred_at)
                is_exit = classification in {"PARTIAL EXIT", "FINAL EXIT", "REVERSAL"}
                body = (
                    f"{'🟢' if side == 'BUY' else '🔴'} LIVE TRADE {classification}\n"
                    f"Account: {account_id}\nSymbol: {symbol}\n"
                    f"Exchange: {fill.get('exchange') or 'N/A'}\nAction: {side}\n"
                    f"Quantity: {quantity}\nExecution Price: ₹{float(price):,.2f}\n"
                    f"Product: {fill.get('product') or 'N/A'}\n"
                    f"Order ID: {fill.get('order_id') or 'N/A'}\nTrade ID: {fill_id}\n"
                )
                if is_exit:
                    body += f"Closed Quantity: {min(abs(int(quantity)), abs(net_qty - (int(quantity) if side == 'BUY' else -int(quantity))))}\n"
                    body += f"Realized P&L: {'₹'+format(realized, '+,.2f') if realized is not None else 'N/A'}\n"
                    body += f"Remaining Position: {net_qty}\n"
                else:
                    body += f"Open Position: {net_qty}\nAverage Entry: ₹{avg_price:,.2f}\n"
                body += f"Time: {_format_time(timestamp)}"
                enqueue_notification(
                    event_type="LIVE_TRADE_EXIT" if is_exit else "LIVE_TRADE_ENTERED",
                    category="LIVE_TRADE", source="KITE_TRADE_BOOK",
                    entity_type="BROKER_TRADE", entity_id=fill_id, account_id=account_id,
                    payload={"telegram_text": body, "account_id":account_id,"trade_id":fill_id,
                             "order_id":str(fill.get("order_id") or ""),"symbol":symbol,
                             "classification":classification,"realized_pnl":realized,
                             "remaining_quantity":net_qty},
                    deduplication_key=dedupe, occurred_at=occurred_at, connection=connection,
                )
                queued += 1
        except Exception:
            logger.exception("Could not process Kite fill trade_id=%s", fill_id)
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
