"""Canonical trade lifecycle, cache, and durable persistence.

Quote/P&L-only updates stay in memory. Lifecycle changes are written immediately.
The database remains authoritative after process restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from functools import wraps
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.services import live_market_data
from app.services.live_quotes import live_quotes
from app.services.trade_terminal_stream import publish

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_lifecycle_lock = threading.RLock()
_cache: dict[str, dict[str, Any]] = {}
_dirty: set[str] = set()
_persistence_error: str | None = None
_TERMINAL = {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}


def _serialized_lifecycle(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with _lifecycle_lock:
            return function(*args, **kwargs)
    return wrapper


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=_json_default)


def _is_active(trade: dict[str, Any]) -> bool:
    return str(trade.get("status", "OPEN")).upper() not in _TERMINAL


def _naive_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo is not None else parsed


_LEGACY_TRADE_FIELDS = {"realized_pnl", "unrealized_pnl", "open_quantity", "closed_quantity", "original_quantity"}

_CACHE_ONLY_FIELDS = {
    "last_price", "ltp", "price", "unrealized_pnl", "realized_pnl",
    "open_quantity", "closed_quantity", "original_quantity", "last_market_data_at",
    "quote_state", "quote_received_at", "quote_timestamp", "last_quote_at", "received_at",
    "market_quote", "cached_quote", "quote", "quote_data", "instrument_token",
    "subscription_state", "subscription_mode", "subscribed",
}


def _persistent_trade_payload(trade: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in trade.items() if key not in _CACHE_ONLY_FIELDS and key not in _LEGACY_TRADE_FIELDS}


def _db_row(trade: dict[str, Any]) -> dict[str, Any]:
    persistent_payload = _persistent_trade_payload(trade)
    return {
        "trade_id": trade["trade_id"],
        "idempotency_key": trade.get("idempotency_key"),
        "execution_mode": trade["execution_mode"],
        "source": trade.get("source") or "MANUAL",
        "strategy": trade.get("strategy"),
        "underlying": trade.get("underlying"),
        "instrument_symbol": trade.get("instrument_symbol"),
        "status": trade.get("status", "OPEN"),
        "version": int(trade.get("version", 1)),
        "payload": _dump({**persistent_payload, "persistence_state": "PERSISTED"}),
        "created_at": _naive_datetime(trade["created_at"]),
        "updated_at": _naive_datetime(trade["updated_at"]),
    }


def hydrate_cache() -> dict[str, Any]:
    """Restore persisted trades into cache. Safe to call repeatedly."""
    global _persistence_error
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT payload FROM trade_records ORDER BY updated_at DESC"
            )).mappings().all()
        restored = {}
        for row in rows:
            payload = row["payload"]
            trade = json.loads(payload) if isinstance(payload, str) else payload
            if isinstance(trade, dict) and trade.get("trade_id"):
                restored[str(trade["trade_id"])] = {key: value for key, value in trade.items() if key not in _LEGACY_TRADE_FIELDS}
        with _lock:
            _cache.clear()
            _cache.update(restored)
            _dirty.clear()
            _persistence_error = None
        return {"restored": len(restored), "open": sum(_is_active(t) for t in restored.values())}
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        _persistence_error = str(exc)
        logger.exception("Trade cache hydration failed")
        return {"restored": 0, "open": 0, "persistence_degraded": True, "error": str(exc)}


def status() -> dict[str, Any]:
    with _lock:
        return {
            "cached_trades": len(_cache),
            "open_trades": sum(_is_active(t) for t in _cache.values()),
            "dirty_trades": len(_dirty),
            "persistence_degraded": bool(_persistence_error or _dirty),
            "persistence_error": _persistence_error,
        }


def list_trades(*, mode: str | None = None, status_filter: str | None = None,
                source: str | None = None, underlying: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = [{key: value for key, value in trade.items() if key not in _LEGACY_TRADE_FIELDS} for trade in _cache.values()]
    if mode:
        rows = [t for t in rows if t.get("execution_mode") == mode.upper()]
    if status_filter:
        rows = [t for t in rows if t.get("status", "OPEN").upper() == status_filter.upper()]
    if source:
        rows = [t for t in rows if str(t.get("source", "")).lower() == source.lower()]
    if underlying:
        rows = [t for t in rows if str(t.get("underlying", "")).lower() == underlying.lower()]
    return sorted(rows, key=lambda t: str(t.get("updated_at", "")), reverse=True)


def get_trade(trade_id: str) -> dict[str, Any] | None:
    with _lock:
        trade = _cache.get(trade_id)
        return {key: value for key, value in trade.items() if key not in _LEGACY_TRADE_FIELDS} if trade else None


def _persist(trade: dict[str, Any], event_type: str, event_payload: dict[str, Any]) -> None:
    global _persistence_error
    row = _db_row(trade)
    event_id = str(uuid.uuid4())
    event_version = int(trade["version"])
    try:
        with get_engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO trade_records
                  (trade_id, idempotency_key, execution_mode, source, strategy, underlying, instrument_symbol,
                   status, version, is_dirty, payload, created_at, updated_at, persisted_at)
                VALUES
                  (:trade_id, :idempotency_key, :execution_mode, :source, :strategy, :underlying, :instrument_symbol,
                   :status, :version, FALSE, CAST(:payload AS JSON), :created_at, :updated_at, CURRENT_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE
                  idempotency_key=COALESCE(idempotency_key, VALUES(idempotency_key)),
                  execution_mode=VALUES(execution_mode), source=VALUES(source),
                  strategy=VALUES(strategy), underlying=VALUES(underlying),
                  instrument_symbol=VALUES(instrument_symbol), status=VALUES(status),
                  version=VALUES(version), is_dirty=FALSE, payload=VALUES(payload),
                  updated_at=VALUES(updated_at), persisted_at=CURRENT_TIMESTAMP(6)
            """), row)
            conn.execute(text("""
                INSERT INTO trade_lifecycle_events
                  (event_id, trade_id, event_type, event_version, event_at, payload)
                VALUES (:event_id, :trade_id, :event_type, :event_version, :event_at, CAST(:payload AS JSON))
                ON DUPLICATE KEY UPDATE event_id=event_id
            """), {
                "event_id": event_id, "trade_id": trade["trade_id"],
                "event_type": event_type, "event_version": event_version,
                "event_at": _naive_datetime(trade["updated_at"]),
                "payload": _dump(event_payload),
            })
        with _lock:
            _dirty.discard(trade["trade_id"])
            _persistence_error = None
    except SQLAlchemyError as exc:
        with _lock:
            _dirty.add(trade["trade_id"])
            _persistence_error = str(exc)
        logger.exception("Trade %s lifecycle persistence failed", trade["trade_id"])
        raise RuntimeError("Trade updated in cache but database persistence failed; retry is required") from exc


@_serialized_lifecycle
def create_trade(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("execution_mode", "PAPER")).upper()
    if mode not in {"PAPER", "LIVE"}:
        raise ValueError("execution_mode must be PAPER or LIVE")
    if mode == "LIVE":
        raise ValueError("Live trade creation is read-only until broker reconciliation is implemented; no orders are placed.")
    symbol = str(payload.get("instrument_symbol") or "").strip()
    if not symbol:
        raise ValueError("instrument_symbol is required")
    quantity = int(payload.get("quantity") or 0)
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero")
    idempotency_key = str(payload.get("idempotency_key") or uuid.uuid4())
    with _lock:
        for existing in _cache.values():
            if existing.get("idempotency_key") == idempotency_key:
                return dict(existing)
    now = _now()
    trade = {
        **payload,
        "trade_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "trading-platform-next:trade:" + idempotency_key)),
        "idempotency_key": idempotency_key,
        "execution_mode": mode,
        "source": str(payload.get("source") or "MANUAL"),
        "strategy": payload.get("strategy"),
        "instrument_symbol": symbol,
        "quantity": quantity,
        "status": "OPEN",
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "persistence_state": "PENDING",
    }
    with _lock:
        _cache[trade["trade_id"]] = trade
        _dirty.add(trade["trade_id"])
    try:
        _persist(trade, "TRADE_CREATED", {"execution_mode": mode, "source": trade["source"], "quantity": quantity})
    except RuntimeError:
        trade["persistence_state"] = "DEGRADED"
        raise
    trade["persistence_state"] = "PERSISTED"
    with _lock:
        _cache[trade["trade_id"]] = trade
    # Resolve and subscribe immediately after acceptance. The quote path remains cache-only.
    try:
        exchange = str(trade.get("exchange") or "NFO").strip().upper()
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT instrument_token FROM instruments WHERE source = 'KITE' AND exchange = :exchange "
                "AND tradingsymbol = :symbol AND is_active = TRUE ORDER BY instrument_version DESC LIMIT 1"
            ), {"exchange": exchange, "symbol": symbol}).first()
        if row and int(row[0]) > 0:
            token = int(row[0])
            # Preserve the last known price while registering the resolved token.
            # A process-local cached tick can hydrate this trade immediately.
            with _lock:
                current = _cache.get(trade["trade_id"])
                if current:
                    current["instrument_token"] = token
                    current["quote_state"] = "WAITING_FOR_FIRST_QUOTE"
            trade["instrument_token"] = token
            trade["quote_state"] = "WAITING_FOR_FIRST_QUOTE"
            cached_quote = live_quotes.get(token)
            if cached_quote and cached_quote.get("last_price") is not None:
                apply_quote_to_open_trades(cached_quote)
            live_market_data.subscribe([token], mode="quote")
            # Re-check after subscription to close the race with a newly arriving tick.
            cached_quote = live_quotes.get(token)
            if cached_quote and cached_quote.get("last_price") is not None:
                apply_quote_to_open_trades(cached_quote)
        else:
            trade["quote_state"] = "INSTRUMENT_NOT_RESOLVED"
            with _lock:
                if trade["trade_id"] in _cache:
                    _cache[trade["trade_id"]]["quote_state"] = "INSTRUMENT_NOT_RESOLVED"
    except Exception:
        logger.exception("Trade %s saved but immediate market-data subscription failed", trade["trade_id"])
        trade["quote_state"] = "SUBSCRIPTION_PENDING"
        with _lock:
            if trade["trade_id"] in _cache:
                _cache[trade["trade_id"]]["quote_state"] = "SUBSCRIPTION_PENDING"
    publish({"type": "trade_upsert", "trade": dict(_cache.get(trade["trade_id"], trade))})
    return dict(_cache.get(trade["trade_id"], trade))


def subscribe_open_trades() -> dict[str, int]:
    """Resolve and subscribe every active trade after startup/session restoration."""
    subscribed = 0
    unresolved = 0
    with _lock:
        trades = [dict(t) for t in _cache.values() if _is_active(t)]
    for trade in trades:
        try:
            token = int(trade.get("instrument_token") or 0)
            if token <= 0:
                with get_engine().connect() as conn:
                    row = conn.execute(text(
                        "SELECT instrument_token FROM instruments WHERE source = 'KITE' AND exchange = :exchange "
                        "AND tradingsymbol = :symbol AND is_active = TRUE ORDER BY instrument_version DESC LIMIT 1"
                    ), {"exchange": str(trade.get("exchange") or "NFO").upper(),
                        "symbol": trade.get("instrument_symbol")}).first()
                token = int(row[0]) if row else 0
            if token <= 0:
                unresolved += 1
                continue
            # Register the token before subscribing, but do not erase a valid quote
            # already attached to this restored trade. Cached ticks are authoritative
            # for the current process and should be shown immediately on reconnect.
            with _lock:
                current = _cache.get(trade["trade_id"])
                if current:
                    current["instrument_token"] = token
                    current["quote_state"] = "WAITING_FOR_FIRST_QUOTE"
            cached_quote = live_quotes.get(token)
            if cached_quote and cached_quote.get("last_price") is not None:
                apply_quote_to_open_trades(cached_quote)
            live_market_data.subscribe([token], mode="quote")
            # A tick can arrive between the cache check and subscription setup.
            # Re-read after subscribing to close that race without database writes.
            cached_quote = live_quotes.get(token)
            if cached_quote and cached_quote.get("last_price") is not None:
                apply_quote_to_open_trades(cached_quote)
            subscribed += 1
        except Exception:
            logger.exception("Could not restore subscription for trade %s", trade.get("trade_id"))
            unresolved += 1
    return {"subscribed": subscribed, "unresolved": unresolved}


def refresh_open_trade_quotes_from_cache() -> int:
    """Reconcile active trade rows with every quote already present in the Kite cache.

    This is intentionally safe to call before REST snapshots and WebSocket snapshots:
    a cached tick can arrive before a trade's subscription is restored, or before the
    client snapshot is sent.
    """
    refreshed = 0
    for quote in live_quotes.snapshot().values():
        refreshed += apply_quote_to_open_trades(quote)
    return refreshed


def apply_quote_to_open_trades(quote: dict[str, Any]) -> int:
    """Update matching open trades in memory and push changed rows; never touches MySQL."""
    token = quote.get("instrument_token")
    price = quote.get("last_price")
    if token is None or price is None:
        return 0
    try:
        price = float(price)
        token = int(token)
    except (TypeError, ValueError):
        return 0
    if price < 0:
        return 0
    timestamp = quote.get("received_at") or _now()
    changed = []
    with _lock:
        for trade_id, current in _cache.items():
            if int(current.get("instrument_token") or 0) != token or not _is_active(current):
                continue
            updated = {key: value for key, value in current.items() if key not in _LEGACY_TRADE_FIELDS}
            updated["last_price"] = price
            updated["last_market_data_at"] = timestamp
            updated["quote_state"] = "LIVE"
            _cache[trade_id] = updated
            changed.append(updated)
    for trade in changed:
        publish({"type": "trade_quote", "trade_id": trade["trade_id"], "trade": trade})
    return len(changed)


@_serialized_lifecycle
def update_trade(trade_id: str, changes: dict[str, Any], *, event_type: str = "TRADE_UPDATED") -> dict[str, Any]:
    if not changes:
        raise ValueError("At least one trade field is required")
    if "trade_id" in changes or "created_at" in changes or "version" in changes:
        raise ValueError("Trade identity, creation timestamp, and version cannot be changed")
    if "execution_mode" in changes and str(changes["execution_mode"]).upper() == "LIVE":
        raise ValueError("Execution mode cannot be changed to LIVE through a manual update")
    with _lock:
        current = _cache.get(trade_id)
        if not current:
            raise KeyError(trade_id)
        if current.get("execution_mode") == "LIVE" and not event_type.startswith("BROKER_"):
            raise ValueError("Live trade lifecycle is controlled by broker reconciliation; manual updates are blocked.")
        # Enforce market-close quote validity on the backend as well as the UI.
        # Never accept a stale client-supplied price as a simulated market fill.
        requested_status = str(changes.get("status", current.get("status", ""))).upper()
        requested_exit_method = str(changes.get("exit_method", current.get("exit_method", ""))).upper()
        if (
            requested_status == "CLOSED"
            and str(current.get("execution_mode", "PAPER")).upper() == "PAPER"
            and requested_exit_method == "PAPER_MARKET_SIMULATION"
        ):
            quote_timestamp = (
                current.get("last_market_data_at")
                or current.get("quote_received_at")
                or current.get("quote_timestamp")
                or current.get("last_quote_at")
            )
            try:
                normalized_timestamp = str(quote_timestamp).replace("Z", "+00:00")
                parsed_timestamp = datetime.fromisoformat(normalized_timestamp)
                if parsed_timestamp.tzinfo is None:
                    parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
                quote_age = (datetime.now(UTC) - parsed_timestamp.astimezone(UTC)).total_seconds()
                market_price = float(current.get("last_price"))
            except (TypeError, ValueError):
                quote_age = float("inf")
                market_price = 0.0
            if quote_age < 0 or quote_age > 30 or market_price <= 0:
                raise ValueError(
                    "Cannot close at market: the backend has no valid quote from the last 30 seconds. "
                    "Wait for a fresh Kite quote or use a limit-price simulation."
                )
            # The backend's latest cached market quote is authoritative for a
            # simulated market exit; ignore any stale client-supplied price.
            changes = {
                **changes,
                "exit_price": market_price,
                "average_exit_price": market_price,
            }

        updated = dict(current)
        # Quantity is the single source of truth for display and all P&L calculations.
        # Do not persist duplicate quantity or realized/unrealized P&L fields.
        changes = {
            key: value for key, value in changes.items()
            if key not in _LEGACY_TRADE_FIELDS
        }
        updated = {
            key: value for key, value in current.items()
            if key not in _LEGACY_TRADE_FIELDS
        }
        updated.update(changes)
        closing_status = str(updated.get("status", "")).upper()
        if closing_status == "CLOSED" and str(updated.get("execution_mode", "PAPER")).upper() == "PAPER":
            exit_price = updated.get("average_exit_price") or updated.get("exit_price")
            entry_price = updated.get("average_entry_price") or updated.get("entry_price")
            quantity = updated.get("quantity")
            try:
                if float(exit_price) <= 0 or float(entry_price) <= 0 or float(quantity) <= 0:
                    raise ValueError("Closing a trade requires positive entry, exit, and quantity")
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Cannot close trade without valid entry price, exit price, side, and quantity"
                ) from exc
        updated["version"] = int(current.get("version", 1)) + 1
        updated["updated_at"] = _now()
        updated["persistence_state"] = "PENDING"
        _cache[trade_id] = updated
        _dirty.add(trade_id)
    try:
        _persist(updated, event_type, {"changed_fields": sorted(changes), "changes": changes})
    except RuntimeError:
        updated["persistence_state"] = "DEGRADED"
        with _lock:
            _cache[trade_id] = updated
        raise
    updated["persistence_state"] = "PERSISTED"
    with _lock:
        _cache[trade_id] = updated
        token = int(updated.get("instrument_token") or 0)
        still_used = token > 0 and any(
            other_id != trade_id and int(other.get("instrument_token") or 0) == token and _is_active(other)
            for other_id, other in _cache.items()
        )
    if token > 0 and not _is_active(updated) and not still_used:
        try:
            live_market_data.unsubscribe([token])
        except Exception:
            logger.exception("Could not release quote subscription for closed trade %s", trade_id)
    publish({"type": "trade_upsert", "trade": dict(updated), "terminal": status()})
    return dict(updated)


def update_market_state(trade_id: str, *, last_price: float | None = None,
                        last_market_data_at: str | None = None) -> dict[str, Any]:
    """Ephemeral quote/P&L update; intentionally does not touch the database."""
    with _lock:
        current = _cache.get(trade_id)
        if not current:
            raise KeyError(trade_id)
        updated = {key: value for key, value in current.items() if key not in _LEGACY_TRADE_FIELDS}
        if last_price is not None:
            updated["last_price"] = last_price
        updated["last_market_data_at"] = last_market_data_at or _now()
        updated["updated_at"] = _now()
        _cache[trade_id] = updated
        return dict(updated)


@_serialized_lifecycle
def retry_dirty() -> dict[str, Any]:
    with _lock:
        trades = [dict(_cache[trade_id]) for trade_id in list(_dirty) if trade_id in _cache]
    persisted, failed = 0, []
    for trade in trades:
        try:
            _persist(trade, "PERSISTENCE_RETRY", {"version": trade["version"]})
            persisted += 1
        except RuntimeError as exc:
            failed.append({"trade_id": trade["trade_id"], "error": str(exc)})
    return {"persisted": persisted, "failed": failed}


@_serialized_lifecycle
def end_of_day_flush() -> dict[str, Any]:
    with _lock:
        trades = [dict(t) for t in _cache.values()]
    persisted, failed = 0, []
    for trade in trades:
        try:
            _persist(trade, "END_OF_DAY_SNAPSHOT_" + datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(), {"status": trade.get("status"), "version": trade.get("version")})
            persisted += 1
        except RuntimeError as exc:
            failed.append({"trade_id": trade["trade_id"], "error": str(exc)})
    return {"persisted": persisted, "failed": failed, "date": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()}


async def run_maintenance_scheduler() -> None:
    """Retry dirty writes periodically and flush a daily terminal snapshot after 15:30 IST."""
    timezone_ist = ZoneInfo("Asia/Kolkata")
    last_eod_date: str | None = None
    while True:
        try:
            retry_dirty()
            now = datetime.now(timezone_ist)
            if now.time() >= time(15, 30) and last_eod_date != now.date().isoformat():
                result = end_of_day_flush()
                logger.info("Trade terminal EOD flush: %s", result)
                last_eod_date = now.date().isoformat()
        except Exception:
            logger.exception("Trade terminal maintenance cycle failed")
        await asyncio.sleep(60)


@_serialized_lifecycle
def reconcile_live_positions(position_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Import broker net positions as canonical LIVE records; never place or modify orders."""
    normalized: dict[str, dict[str, Any]] = {}
    for row in position_rows:
        quantity = int(row.get("quantity") or row.get("net_quantity") or 0)
        exchange = str(row.get("exchange") or "").strip().upper()
        symbol = str(row.get("tradingsymbol") or row.get("trading_symbol") or "").strip()
        product = str(row.get("product") or "").strip().upper()
        if not symbol or not exchange or quantity == 0:
            continue
        key = f"{exchange}:{symbol}:{product}"
        trade_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "trading-platform-next:kite-position:" + key))
        normalized[key] = {
            "trade_id": trade_id, "broker_position_key": key,
            "execution_mode": "LIVE", "source": "KITE_POSITION",
            "strategy": "Broker Position Reconciliation",
            "underlying": row.get("name") or row.get("underlying") or symbol,
            "instrument_symbol": symbol, "exchange": exchange, "product": product,
            "direction": "BUY" if quantity > 0 else "SELL",
            "quantity": abs(quantity),
            "average_entry_price": row.get("average_price"),
            "last_price": row.get("last_price"), "broker_pnl": row.get("pnl"),
            "status": "OPEN",
        }

    with _lock:
        existing = {
            str(t.get("broker_position_key")): dict(t)
            for t in _cache.values()
            if t.get("execution_mode") == "LIVE" and t.get("source") == "KITE_POSITION"
            and t.get("broker_position_key")
        }
    created = updated = closed = 0
    for key, incoming in normalized.items():
        old = existing.get(key)
        if old is None:
            now = _now()
            trade = {
                **{key: value for key, value in incoming.items() if key not in _LEGACY_TRADE_FIELDS},
                "created_at": now, "updated_at": now,
                "version": 1, "persistence_state": "PENDING",
            }
            with _lock:
                _cache[trade["trade_id"]] = trade
                _dirty.add(trade["trade_id"])
            _persist(trade, "BROKER_POSITION_DISCOVERED", {"broker_position_key": key, "quantity": trade["quantity"]})
            trade["persistence_state"] = "PERSISTED"
            with _lock:
                _cache[trade["trade_id"]] = trade
            created += 1
            continue

        material = any(old.get(field) != incoming.get(field) for field in ("quantity", "status", "direction", "average_entry_price"))
        # Quote/P&L fields are refreshed in cache but are not durable per-poll writes.
        with _lock:
            current = dict(_cache[old["trade_id"]])
            current["last_price"] = incoming.get("last_price")
            current["broker_pnl"] = incoming.get("broker_pnl")
            current["last_market_data_at"] = _now()
            if material:
                current.update({k: incoming[k] for k in ("quantity", "status", "direction", "average_entry_price")})
                current["version"] = int(current.get("version", 1)) + 1
                current["updated_at"] = _now()
                current["persistence_state"] = "PENDING"
                _dirty.add(current["trade_id"])
            _cache[current["trade_id"]] = current
        if material:
            _persist(current, "BROKER_POSITION_CHANGED", {"broker_position_key": key, "quantity": current["quantity"], "status": current["status"]})
            current["persistence_state"] = "PERSISTED"
            with _lock:
                _cache[current["trade_id"]] = current
            updated += 1

    live_keys = set(normalized)
    for key, old in existing.items():
        if key in live_keys or not _is_active(old):
            continue
        current = update_trade(old["trade_id"], {"status": "CLOSED"}, event_type="BROKER_POSITION_FLATTENED")
        current["persistence_state"] = "PERSISTED"
        closed += 1
    # Newly reconciled broker positions are added after startup, so the
    # startup subscription pass cannot see them. Resolve their Kite instrument
    # tokens and register WebSocket subscriptions immediately after every sync.
    subscription = subscribe_open_trades()
    return {
        "created": created,
        "updated": updated,
        "closed": closed,
        "active_positions": len(normalized),
        "subscriptions": subscription,
    }
