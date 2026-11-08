"""Durable Kite order lifecycle for AutoTrender REAL execution.

An order intent is committed before contacting Kite. If the HTTP response is lost,
the order tag is searched in the broker order book; an ambiguous submission is
never blindly retried. Trades are created only after confirmed entry fills.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.providers.kite.registry import get_kite_provider
from app.services import trade_terminal

logger = logging.getLogger(__name__)

_ORDER_COLUMNS = """
    idempotency_key, order_tag, role, underlying, trade_id, broker_order_id,
    status, exchange, tradingsymbol, transaction_type, quantity, filled_quantity,
    average_price, payload, last_error
"""


def ensure_schema() -> None:
    with get_engine().begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS autotrender_kite_orders (
                idempotency_key VARCHAR(180) NOT NULL PRIMARY KEY,
                order_tag VARCHAR(20) NOT NULL UNIQUE,
                role VARCHAR(12) NOT NULL,
                underlying VARCHAR(32) NULL,
                trade_id VARCHAR(64) NULL,
                broker_order_id VARCHAR(64) NULL,
                status VARCHAR(40) NOT NULL,
                exchange VARCHAR(12) NOT NULL,
                tradingsymbol VARCHAR(80) NOT NULL,
                transaction_type VARCHAR(8) NOT NULL,
                quantity INT NOT NULL,
                filled_quantity INT NOT NULL DEFAULT 0,
                average_price DECIMAL(18,6) NULL,
                payload JSON NOT NULL,
                last_error VARCHAR(1000) NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                INDEX ix_autotrender_kite_status (status, role),
                INDEX ix_autotrender_kite_trade (trade_id, role)
            ) ENGINE=InnoDB
        """))


def _tag(key: str) -> str:
    return "AT" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:18]


def _get_intent(key: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT " + _ORDER_COLUMNS + " FROM autotrender_kite_orders WHERE idempotency_key=:key"
        ), {"key": key}).mappings().first()
    if not row:
        return None
    item = dict(row)
    payload = item.get("payload")
    item["payload"] = json.loads(payload) if isinstance(payload, str) else payload
    return item


def _save_status(key: str, status: str, *, order_id: str | None = None,
                 filled: int | None = None, average: float | None = None,
                 error: str | None = None) -> None:
    assignments = ["status=:status", "last_error=:error", "updated_at=CURRENT_TIMESTAMP(6)"]
    params: dict[str, Any] = {"key": key, "status": status, "error": error[:1000] if error else None}
    if order_id is not None:
        assignments.append("broker_order_id=:order_id")
        params["order_id"] = order_id
    if filled is not None:
        assignments.append("filled_quantity=:filled")
        params["filled"] = int(filled)
    if average is not None and average > 0:
        assignments.append("average_price=:average")
        params["average"] = float(average)
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE autotrender_kite_orders SET " + ", ".join(assignments) +
            " WHERE idempotency_key=:key"
        ), params)


def _broker_order_for_tag(orders: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    matches = [item for item in orders if str(item.get("tag") or "") == tag]
    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("order_timestamp") or item.get("exchange_timestamp") or ""))
    return matches[-1]


def _submit_intent(key: str) -> dict[str, Any]:
    intent = _get_intent(key)
    if not intent:
        raise RuntimeError("Kite order intent was not persisted")
    if intent["status"] not in {"PREPARED", "SUBMITTING"}:
        return intent
    provider = get_kite_provider()
    if intent["status"] == "SUBMITTING":
        # A prior process may have died after Kite accepted the order but before
        # its order_id was saved. Resolve by deterministic tag; never resubmit.
        found = _broker_order_for_tag(provider.orders(), intent["order_tag"])
        if found:
            _save_status(key, str(found.get("status") or "OPEN").upper(),
                         order_id=str(found.get("order_id") or ""),
                         filled=int(found.get("filled_quantity") or 0),
                         average=float(found.get("average_price") or 0))
            return _get_intent(key) or intent
        _save_status(key, "SUBMISSION_UNKNOWN",
                     error="Submission outcome is ambiguous; order tag not yet visible. Automatic retry is disabled.")
        return _get_intent(key) or intent

    # Compare-and-set the PREPARED intent so concurrent quote/auto-exit
    # callbacks cannot submit the same broker order twice.
    with get_engine().begin() as conn:
        claimed = conn.execute(text("""
            UPDATE autotrender_kite_orders
            SET status='SUBMITTING', last_error=NULL, updated_at=CURRENT_TIMESTAMP(6)
            WHERE idempotency_key=:key AND status='PREPARED'
        """), {"key": key})
    if claimed.rowcount != 1:
        return _submit_intent(key)
    try:
        order_id = provider.place_order(
            exchange=intent["exchange"],
            tradingsymbol=intent["tradingsymbol"],
            transaction_type=intent["transaction_type"],
            quantity=int(intent["quantity"]),
            product="MIS",
            order_type="MARKET",
            validity="IOC",
            tag=intent["order_tag"],
        )
        _save_status(key, "OPEN", order_id=str(order_id))
    except Exception as exc:
        # A timeout can happen after the broker accepted the order. Search the
        # tag before deciding whether the outcome is unknown.
        try:
            found = _broker_order_for_tag(provider.orders(), intent["order_tag"])
        except Exception:
            found = None
        if found:
            _save_status(key, str(found.get("status") or "OPEN").upper(),
                         order_id=str(found.get("order_id") or ""),
                         filled=int(found.get("filled_quantity") or 0),
                         average=float(found.get("average_price") or 0))
        else:
            _save_status(key, "SUBMISSION_UNKNOWN", error=str(exc))
            raise RuntimeError("Kite order submission outcome is unknown; it was not retried") from exc
    return _get_intent(key) or intent


def _create_intent(*, key: str, role: str, underlying: str, trade_id: str | None,
                   exchange: str, symbol: str, transaction: str, quantity: int,
                   payload: dict[str, Any]) -> dict[str, Any]:
    tag = _tag(key)
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT IGNORE INTO autotrender_kite_orders
              (idempotency_key, order_tag, role, underlying, trade_id, status, exchange,
               tradingsymbol, transaction_type, quantity, payload)
            VALUES (:key, :tag, :role, :underlying, :trade_id, 'PREPARED', :exchange,
                    :symbol, :transaction, :quantity, CAST(:payload AS JSON))
        """), {
            "key": key, "tag": tag, "role": role, "underlying": underlying,
            "trade_id": trade_id, "exchange": exchange, "symbol": symbol,
            "transaction": transaction, "quantity": quantity,
            "payload": json.dumps(payload, separators=(",", ":"), default=str),
        })
    return _submit_intent(key)


def submit_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist and submit one idempotent AutoTrender market entry."""
    ensure_schema()
    key = str(payload["idempotency_key"])
    existing = _get_intent(key)
    if existing:
        return existing
    # Validate the authenticated broker session before creating an intent. This
    # avoids leaving a false SUBMISSION_UNKNOWN record when Kite is simply logged out.
    get_kite_provider().profile()
    with get_engine().connect() as conn:
        pending = conn.execute(text("""
            SELECT idempotency_key FROM autotrender_kite_orders
            WHERE underlying=:underlying AND role='ENTRY'
              AND status IN ('PREPARED','SUBMITTING','SUBMISSION_UNKNOWN','OPEN','PENDING','PARTIAL')
            LIMIT 1
        """), {"underlying": str(payload["underlying"])}).first()
    if pending:
        raise RuntimeError("A previous AutoTrender Kite entry order is still unresolved for this index")
    return _create_intent(
        key=key, role="ENTRY", underlying=str(payload["underlying"]),
        trade_id=None, exchange=str(payload["exchange"]).upper(),
        symbol=str(payload["instrument_symbol"]), transaction="BUY",
        quantity=int(payload["quantity"]), payload=payload,
    )


def submit_exit(trade: dict[str, Any], reason: str, risk_updates: dict[str, Any]) -> dict[str, Any]:
    """Submit one broker exit after target/stop logic signals a close."""
    ensure_schema()
    trade_id = str(trade["trade_id"])
    with get_engine().connect() as conn:
        latest = conn.execute(text("""
            SELECT idempotency_key, status FROM autotrender_kite_orders
            WHERE trade_id=:trade_id AND role='EXIT'
            ORDER BY created_at DESC LIMIT 1
        """), {"trade_id": trade_id}).first()
        attempt_count = conn.execute(text("""
            SELECT COUNT(*) FROM autotrender_kite_orders
            WHERE trade_id=:trade_id AND role='EXIT'
        """), {"trade_id": trade_id}).scalar() or 0
    if latest and str(latest[1]).upper() != "EXIT_PARTIAL_UNRESOLVED":
        return _get_intent(str(latest[0])) or {"status": str(latest[1])}
    get_kite_provider().profile()
    key = "AUTOTRENDER:EXIT:" + trade_id + ":" + str(int(attempt_count) + 1)
    if str(trade.get("direction") or "BUY").upper() != "BUY":
        raise ValueError("AutoTrender live exit currently requires a long option position")
    # Persist strategy milestones before sending the exit; never mark the position
    # closed until Kite confirms the exit fill.
    safe_updates = {k: v for k, v in risk_updates.items() if k not in {"status", "exit_price", "average_exit_price"}}
    if safe_updates:
        trade_terminal.update_trade(trade_id, safe_updates, event_type="BROKER_RISK_STATE")
    return _create_intent(
        key=key, role="EXIT", underlying=str(trade.get("underlying") or ""),
        trade_id=trade_id, exchange=str(trade["exchange"]).upper(),
        symbol=str(trade["instrument_symbol"]), transaction="SELL",
        quantity=int(trade.get("quantity") or 0),
        payload={"reason": reason, "risk_updates": safe_updates},
    )


def _apply_entry_fill(intent: dict[str, Any]) -> None:
    payload = intent["payload"]
    filled = int(intent.get("filled_quantity") or 0)
    average = float(intent.get("average_price") or 0)
    if filled <= 0 or average <= 0:
        return
    trade = trade_terminal.create_trade({
        "idempotency_key": str(intent["idempotency_key"]),
        "execution_mode": "LIVE",
        "source": "AUTOTRENDER",
        "strategy": payload.get("strategy"),
        "underlying": payload.get("underlying"),
        "instrument_symbol": payload.get("instrument_symbol"),
        "exchange": payload.get("exchange"),
        "instrument_token": payload.get("instrument_token"),
        "direction": "BUY",
        "quantity": filled,
        "entry_price": average,
        "average_entry_price": average,
        "target": payload.get("target"),
        "broker_order_id": intent.get("broker_order_id"),
        "broker_status": str(intent.get("status") or "COMPLETE"),
        "entry_filled_quantity": filled,
        "notes": payload.get("notes"),
    })
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE autotrender_kite_orders SET trade_id=:trade_id, status='TRADE_CREATED',
                updated_at=CURRENT_TIMESTAMP(6) WHERE idempotency_key=:key
        """), {"trade_id": trade["trade_id"], "key": intent["idempotency_key"]})


def _apply_exit_fill(intent: dict[str, Any]) -> bool:
    trade_id = str(intent.get("trade_id") or "")
    trade = trade_terminal.get_trade(trade_id)
    filled = int(intent.get("filled_quantity") or 0)
    average = float(intent.get("average_price") or 0)
    if not trade or filled <= 0 or average <= 0:
        return False
    original = int(trade.get("quantity") or 0)
    if filled < original:
        _save_status(str(intent["idempotency_key"]), "EXIT_PARTIAL_UNRESOLVED",
                     error="Exit order filled only partially; remaining broker position requires reconciliation.")
        trade_terminal.update_trade(trade_id, {
            "quantity": max(1, original - filled),
            "broker_exit_order_id": intent.get("broker_order_id"),
            "broker_exit_filled_quantity": filled,
            "broker_exit_average_price": average,
            "broker_exit_state": "PARTIAL_REQUIRES_RECONCILIATION",
        }, event_type="BROKER_EXIT_PARTIAL")
        return False
    details = intent.get("payload") or {}
    trade_terminal.update_trade(trade_id, {
        "status": "CLOSED",
        "exit_price": average,
        "average_exit_price": average,
        "broker_exit_order_id": intent.get("broker_order_id"),
        "broker_exit_filled_quantity": filled,
        "broker_exit_state": "COMPLETE",
        "exit_reason": details.get("reason") or "AutoTrender target/stop",
        "exit_method": "KITE_MARKET_ORDER",
    }, event_type="BROKER_EXIT_FILLED")
    return True


def run_cycle() -> dict[str, Any]:
    """Reconcile broker orders and create/close canonical trades from confirmed fills."""
    ensure_schema()
    provider = get_kite_provider()
    orders = provider.orders()
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT " + _ORDER_COLUMNS + " FROM autotrender_kite_orders "
            "WHERE status IN ('SUBMITTING','SUBMISSION_UNKNOWN','OPEN','PENDING','PARTIAL','COMPLETE','CANCELLED','REJECTED')"
        )).mappings().all()
    processed = 0
    for row in rows:
        item = dict(row)
        payload = item.get("payload")
        item["payload"] = json.loads(payload) if isinstance(payload, str) else payload
        broker_order = None
        if item.get("broker_order_id"):
            broker_order = next((o for o in orders if str(o.get("order_id")) == str(item["broker_order_id"])), None)
        if broker_order is None:
            broker_order = _broker_order_for_tag(orders, item["order_tag"])
        if broker_order is None:
            if item["status"] == "SUBMITTING":
                _save_status(item["idempotency_key"], "SUBMISSION_UNKNOWN",
                             error="Order not visible in Kite order book; automatic resubmission is disabled.")
            continue
        status = str(broker_order.get("status") or "OPEN").upper()
        filled = int(broker_order.get("filled_quantity") or 0)
        average = float(broker_order.get("average_price") or 0)
        _save_status(item["idempotency_key"], status,
                     order_id=str(broker_order.get("order_id") or ""),
                     filled=filled, average=average,
                     error=str(broker_order.get("status_message") or "") or None)
        item.update({"status": status, "broker_order_id": str(broker_order.get("order_id") or ""),
                     "filled_quantity": filled, "average_price": average})
        if item["role"] == "ENTRY" and status in {"COMPLETE", "CANCELLED", "REJECTED"} and filled > 0:
            _apply_entry_fill(item)
            processed += 1
        elif item["role"] == "ENTRY" and status in {"CANCELLED", "REJECTED"} and filled == 0:
            _save_status(item["idempotency_key"], "ENTRY_REJECTED",
                         error=str(broker_order.get("status_message") or status))
        elif item["role"] == "EXIT" and status == "COMPLETE":
            if _apply_exit_fill(item):
                _save_status(item["idempotency_key"], "EXIT_FILLED")
            processed += 1
        elif item["role"] == "EXIT" and status in {"CANCELLED", "REJECTED"} and filled > 0:
            _apply_exit_fill(item)
            processed += 1
        elif item["role"] == "EXIT" and status in {"CANCELLED", "REJECTED"} and filled == 0:
            _save_status(item["idempotency_key"], "EXIT_REJECTED",
                         error=str(broker_order.get("status_message") or status))
            if item.get("trade_id"):
                trade_terminal.update_trade(str(item["trade_id"]), {
                    "broker_exit_state": "REJECTED",
                    "broker_exit_error": str(broker_order.get("status_message") or status),
                }, event_type="BROKER_EXIT_REJECTED")
    return {"processed": processed, "orders_seen": len(orders)}


async def run_scheduler() -> None:
    logger.info("AutoTrender Kite execution reconciler started")
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AutoTrender Kite execution reconciliation failed")
        await asyncio.sleep(2)
