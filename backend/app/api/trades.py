from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services import trade_terminal
from app.services import trade_terminal_stream
from app.services import live_market_data
from app.services.live_quotes import live_quotes

router = APIRouter(prefix="/trades", tags=["trade terminal"])


class TradeCreate(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    execution_mode: Literal["PAPER", "LIVE"] = "PAPER"
    source: str = Field(default="MANUAL", min_length=1, max_length=80)
    strategy: str | None = Field(default=None, max_length=120)
    underlying: str | None = Field(default=None, max_length=80)
    instrument_symbol: str = Field(min_length=1, max_length=120)
    exchange: str | None = Field(default="NFO", max_length=30)
    direction: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=1000000)
    entry_price: float | None = Field(default=None, ge=0)
    stop_loss: float | None = Field(default=None, ge=0)
    target: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


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
    return {"trades": trade_terminal.list_trades(mode=mode, status_filter=status, source=source, underlying=underlying),
            "terminal": trade_terminal.status()}


@router.get("/status")
def terminal_status() -> dict[str, Any]:
    return trade_terminal.status()


@router.post("")
def create_terminal_trade(payload: TradeCreate) -> dict[str, Any]:
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
    except Exception:
        trade_terminal_stream.disconnect(websocket)
