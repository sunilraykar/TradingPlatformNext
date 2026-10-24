"""Thread-safe fan-out for incremental Trade Terminal WebSocket events."""
import asyncio
import json
import threading
from fastapi import WebSocket

_clients: set[tuple[WebSocket, asyncio.AbstractEventLoop]] = set()
_clients_lock = threading.RLock()


async def connect(websocket: WebSocket) -> None:
    await websocket.accept()
    with _clients_lock:
        _clients.add((websocket, asyncio.get_running_loop()))


def disconnect(websocket: WebSocket) -> None:
    with _clients_lock:
        for client in tuple(_clients):
            if client[0] is websocket:
                _clients.discard(client)


def publish(event: dict) -> None:
    """Schedule incremental events without blocking the Kite ticker thread."""
    payload = json.dumps(event, separators=(",", ":"), default=str)
    with _clients_lock:
        clients = tuple(_clients)
    for websocket, loop in clients:
        if loop.is_closed():
            disconnect(websocket)
            continue
        try:
            loop.call_soon_threadsafe(
                lambda ws=websocket, lp=loop, data=payload:
                    asyncio.create_task(_send_on_loop(lp, ws, data))
            )
        except RuntimeError:
            disconnect(websocket)


async def _send_on_loop(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: str) -> None:
    try:
        await websocket.send_text(payload)
    except Exception:
        disconnect(websocket)
