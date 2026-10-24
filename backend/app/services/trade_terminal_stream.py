"""Thread-safe fan-out for incremental Trade Terminal WebSocket events."""
import asyncio
import json
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)
_clients: set[tuple[WebSocket, asyncio.AbstractEventLoop]] = set()


async def connect(websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.add((websocket, asyncio.get_running_loop()))


def disconnect(websocket: WebSocket) -> None:
    _clients.difference_update(client for client in _clients if client[0] is websocket)


def publish(event: dict) -> None:
    """Schedule a small incremental event for each connected UI without blocking quote ingestion."""
    payload = json.dumps(event, separators=(",", ":"), default=str)
    for websocket, loop in tuple(_clients):
        if loop.is_closed():
            disconnect(websocket)
            continue
        async def send(ws=websocket, data=payload):
            try:
                await ws.send_text(data)
            except Exception:
                disconnect(ws)
        try:
            loop.call_soon_threadsafe(lambda ws=websocket, data=payload, lp=loop: asyncio.create_task(_send_on_loop(lp, ws, data)))
        except RuntimeError:
            disconnect(websocket)


async def _send_on_loop(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: str) -> None:
    try:
        await websocket.send_text(payload)
    except Exception:
        disconnect(websocket)
