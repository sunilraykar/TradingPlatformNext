import json
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import get_engine
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services import live_market_data, trade_terminal, trade_terminal_stream
from app.services.live_quotes import live_quotes

router = APIRouter(prefix="/trades", tags=["trade terminal"])


class TradeCreate(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    execution_mode: Literal["PAPER", "LIVE"] = "PAPER"
    source: str = Field(default="MANUAL", min_length=1, max_length=80)
    strategy: str | None = Field(default=None, max_length=120)
    underlying: str | None = Field(default=None, max_length=80)
    instrument_symbol: str = Field(min_length=1, max_length=120)
    exchange: str | None = Field(default=None, max_length=30)
    direction: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=1000000)
    entry_price: float | None = Field(default=None, ge=0)
    stop_loss: float | None = Field(default=None, ge=0)
    target: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    signal_timestamp: str | None = Field(default=None, max_length=80)


class TradeUpdate(BaseModel):
    status: str | None = Field(default=None, min_length=2, max_length=40)
    quantity: int | None = Field(default=None, gt=0, le=1000000)
    open_quantity: int | None = Field(default=None, ge=0, le=1000000)
    entry_price: float | None = Field(default=None, ge=0)
    average_entry_price: float | None = Field(default=None, ge=0)
    exit_price: float | None = Field(default=None, ge=0)
    average_exit_price: float | None = Field(default=None, ge=0)
    stop_loss: float | None = Field(default=None, ge=0)
    target: float | None = Field(default=None, ge=0)
    realized_pnl: float | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("")
def list_terminal_trades(
    mode: str | None = Query(default=None, pattern="^(PAPER|LIVE)$"),
    status: str | None = None,
    source: str | None = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    trade_terminal.refresh_open_trade_quotes_from_cache()
    return {"trades": trade_terminal.list_trades(mode=mode, status_filter=status, source=source, underlying=underlying),
            "terminal": trade_terminal.status()}


@router.get("/instruments")
def search_trade_instruments(
    q: str = Query(default="", max_length=80),
    exchange: str | None = Query(default=None, max_length=10),
    instrument_type: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    """Search the active Kite instrument master for the paper-trade symbol picker."""
    from sqlalchemy import text

    from app.db.session import get_engine

    search = q.strip().upper()
    if len(search) < 2:
        return {"instruments": [], "message": "Enter at least two characters to search."}
    clauses = ["source = 'KITE'", "is_active = TRUE", "(UPPER(tradingsymbol) LIKE :query OR UPPER(name) LIKE :query)"]
    params: dict[str, Any] = {"query": f"%{search}%", "limit": limit}
    if exchange:
        clauses.append("exchange = :exchange")
        params["exchange"] = exchange.upper()
    if instrument_type:
        clauses.append("instrument_type = :instrument_type")
        params["instrument_type"] = instrument_type.upper()
    sql = text(f"""
        SELECT instrument_token, exchange, tradingsymbol, name, instrument_type, segment,
               expiry, strike, lot_size
        FROM instruments
        WHERE {' AND '.join(clauses)}
        ORDER BY CASE WHEN UPPER(tradingsymbol) = :exact THEN 0
                      WHEN UPPER(tradingsymbol) LIKE :prefix THEN 1 ELSE 2 END,
                 exchange, tradingsymbol, expiry, strike
        LIMIT :limit
    """)
    params["exact"] = search
    params["prefix"] = f"{search}%"
    with get_engine().connect() as connection:
        rows = connection.execute(sql, params).mappings().all()
    return {"instruments": [
        {**dict(row), "instrument_token": int(row["instrument_token"]),
         "expiry": row["expiry"].isoformat() if row["expiry"] else None}
        for row in rows
    ]}


@router.get("/status")
def terminal_status() -> dict[str, Any]:
    return trade_terminal.status()


def _enforce_autotrender_entry_window(source: str, signal_timestamp: str | None) -> None:
    """Fail closed on AutoTrender entries outside the cutoff or with stale signals."""
    normalized_source = source.upper().replace("-", "").replace("_", "")
    if "AUTOTRENDER" not in normalized_source:
        return

    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text("SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN (:start_key, :end_key, :age_key)"),
                {"start_key": "ui.autoTrenderEntryStartTime", "end_key": "ui.autoTrenderEntryEndTime", "age_key": "ui.autoTrenderMaxSignalAgeSeconds"},
            ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to read AutoTrender entry-window settings") from exc

    settings = {"autoTrenderEntryStartTime": "10:00", "autoTrenderEntryEndTime": "15:25", "autoTrenderMaxSignalAgeSeconds": 300}
    for row in rows:
        value = row["setting_value"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
        settings[str(row["setting_key"])[3:]] = value

    start_value = settings["autoTrenderEntryStartTime"]
    end_value = settings["autoTrenderEntryEndTime"]
    try:
        if not isinstance(start_value, str) or not isinstance(end_value, str):
            raise TypeError("Entry times must be strings")
        start = time.fromisoformat(start_value)
        end = time.fromisoformat(end_value)
        if (
            len(start_value) != 5 or len(end_value) != 5
            or start.strftime("%H:%M") != start_value
            or end.strftime("%H:%M") != end_value
            or end <= start
        ):
            raise ValueError("Invalid entry window")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="AutoTrender entry-window settings are invalid; correct them in Settings",
        ) from exc

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start_time = time.fromisoformat(start_value)
    end_time = time.fromisoformat(end_value)
    if now.time().replace(tzinfo=None) < start_time or now.time().replace(tzinfo=None) >= end_time:
        raise HTTPException(
            status_code=422,
            detail=f"New AutoTrender entries are allowed from {start_value} IST (inclusive) until {end_value} IST (exclusive)",
        )

    if not signal_timestamp:
        raise HTTPException(status_code=422, detail="AutoTrender entries require a source signal timestamp")
    try:
        parsed_signal_time = datetime.fromisoformat(signal_timestamp.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="AutoTrender signal timestamp is invalid") from exc
    if parsed_signal_time.tzinfo is None:
        parsed_signal_time = parsed_signal_time.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    age_seconds = (now - parsed_signal_time.astimezone(ZoneInfo("Asia/Kolkata"))).total_seconds()
    try:
        max_age_seconds = int(float(settings["autoTrenderMaxSignalAgeSeconds"]))
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=503, detail="AutoTrender maximum signal age setting is invalid") from exc
    if max_age_seconds < 1 or max_age_seconds > 3600:
        raise HTTPException(status_code=503, detail="AutoTrender maximum signal age must be between 1 and 3600 seconds")
    if age_seconds < -30:
        raise HTTPException(status_code=422, detail="AutoTrender signal timestamp is in the future")
    if age_seconds > max_age_seconds:
        raise HTTPException(status_code=422, detail="AutoTrender signal is stale; a fresh signal is required")


@router.post("")
def create_terminal_trade(payload: TradeCreate) -> dict[str, Any]:
    _enforce_autotrender_entry_window(payload.source, payload.signal_timestamp)
    try:
        return {"trade": trade_terminal.create_trade(payload.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/{trade_id}")
def patch_terminal_trade(trade_id: str, payload: TradeUpdate) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return {"trade": trade_terminal.update_trade(trade_id, changes, event_type="TRADE_LIFECYCLE_CHANGED")}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trade not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/retry-persistence")
def retry_terminal_persistence() -> dict[str, Any]:
    return trade_terminal.retry_dirty()


@router.post("/end-of-day-flush")
def flush_terminal_end_of_day() -> dict[str, Any]:
    return trade_terminal.end_of_day_flush()


@router.post("/reconcile-live")
def reconcile_live_terminal_positions() -> dict[str, Any]:
    """Read Kite net positions and upsert them into the canonical trade terminal."""
    try:
        positions = get_kite_provider().positions()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail="Unable to reconcile live positions from Kite") from exc
    net = positions.get("net", []) if isinstance(positions, dict) else []
    try:
        result = trade_terminal.reconcile_live_positions(net)
        return {"reconciliation": result, "terminal": trade_terminal.status()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.websocket("/stream")
async def trade_terminal_stream_socket(websocket: WebSocket) -> None:
    """Push trade lifecycle and quote updates to the terminal without polling."""
    await trade_terminal_stream.connect(websocket)
    try:
        # Reconcile cached ticks before taking the snapshot, so stale trade rows cannot
        # overwrite newer quote updates when a client connects or reconnects.
        trade_terminal.refresh_open_trade_quotes_from_cache()
        await websocket.send_json({
            "type": "snapshot",
            "trades": trade_terminal.list_trades(),
            "terminal": trade_terminal.status(),
        })
        while True:
            # Keep the connection alive; client messages are not commands.
            await websocket.receive_text()
    except WebSocketDisconnect:
        trade_terminal_stream.disconnect(websocket)
    except Exception:  # noqa: BLE001 - disconnect on any socket/send failure
        trade_terminal_stream.disconnect(websocket)


@router.get("/market-data-status")
def terminal_market_data_status() -> dict[str, Any]:
    """Diagnose live subscriptions for active Terminal trades; no database writes."""
    market = live_market_data.status()
    subscribed = set(market.get("subscribed_tokens", []))
    quotes = live_quotes.snapshot()
    rows = []
    for trade in trade_terminal.list_trades():
        if str(trade.get("status", "OPEN")).upper() in {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}:
            continue
        token_value = trade.get("instrument_token")
        token = int(token_value) if token_value else None
        quote = quotes.get(token) if token else None
        rows.append({
            "trade_id": trade.get("trade_id"),
            "symbol": trade.get("instrument_symbol"),
            "exchange": trade.get("exchange") or "NFO",
            "status": trade.get("status", "OPEN"),
            "instrument_token": token,
            "subscription_registered": token in subscribed if token else False,
            "subscription_mode": next(
                (item.get("mode") for item in market.get("subscriptions", [])
                 if token and item.get("instrument_token") == token), None
            ),
            "quote_available": bool(quote),
            "last_price": quote.get("last_price") if quote else None,
            "quote_received_at": quote.get("received_at") if quote else None,
            "diagnostic": (
                "INSTRUMENT_NOT_RESOLVED" if not token else
                "SUBSCRIBED_WAITING_FOR_QUOTE" if token in subscribed and not quote else
                "LIVE_QUOTE_AVAILABLE" if quote else
                "TOKEN_NOT_REGISTERED"
            ),
        })
    return {"websocket": market, "active_terminal_trades": rows, "active_count": len(rows)}
