from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.providers.apiplatform.client import APIPLATFORMClientError
from app.providers.apiplatform.registry import get_apiplatform_client

router = APIRouter(prefix="/apiplatform/option-chain", tags=["APIPLATFORM Option Chain"])

_cache_lock = Lock()
_latest_snapshot: dict[str, Any] | None = None


def store_latest_snapshot(
    symbol: str,
    expiry: str,
    lot_size: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Keep the complete latest APIPLATFORM response in process memory."""
    global _latest_snapshot
    snapshot = {
        "cached": True,
        "symbol": symbol,
        "expiry": expiry,
        "lot_size": lot_size,
        "cached_at": datetime.now(UTC).isoformat(),
        "fetch_time": payload.get("FetchTime")
        or payload.get("symbolMarketDirection", {}).get("actualFetchTime"),
        "option_chain_count": len(payload.get("OptionChain", []))
        if isinstance(payload.get("OptionChain"), list)
        else 0,
        "payload": payload,
    }
    with _cache_lock:
        _latest_snapshot = snapshot
    return snapshot


def read_latest_snapshot() -> dict[str, Any]:
    """Return a copy of the latest cache envelope, including the raw payload."""
    with _cache_lock:
        if _latest_snapshot is None:
            return {"cached": False, "payload": None}
        return dict(_latest_snapshot)


def clear_latest_snapshot() -> None:
    """Clear the in-memory snapshot; exposed for isolated tests."""
    global _latest_snapshot
    with _cache_lock:
        _latest_snapshot = None


@router.get("/cache")
def get_option_chain_cache() -> dict[str, Any]:
    """Read the latest cached APIPLATFORM response without calling the provider."""
    return read_latest_snapshot()


@router.get("/fetch")
def fetch_latest_option_chain(
    request: Request,
    symbol: str = Query(default="NIFTY", min_length=1, max_length=40),
    expiry: str = Query(..., min_length=8, max_length=10),
    lot_size: int = Query(..., alias="lot_size", ge=1, le=10000),
) -> dict[str, Any]:
    """Fetch a live APIPLATFORM response, replace the cache, and return the snapshot."""
    client = getattr(request.app.state, "apiplatform_client", None) or get_apiplatform_client()
    try:
        payload = client.fetch_option_chain(
            symbol=symbol.strip().upper(),
            expiry=expiry,
            lot_size=lot_size,
        )
    except (APIPLATFORMClientError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return store_latest_snapshot(symbol.strip().upper(), expiry, lot_size, payload)
