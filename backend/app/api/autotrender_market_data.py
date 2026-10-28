import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from typing import Annotated

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.db.session import get_engine
from app.providers.autotrender.auth import AutoTrenderAuth
from app.services.autotrender_cache import AutoTrenderCache

router = APIRouter(prefix="/autotrender", tags=["AutoTrender Market Data"])

# Use AutoTrender's exact category values; these map to its derivative endpoints.
INDIA_TZ = ZoneInfo("Asia/Kolkata")
MARKET_DATA_START = time(9, 30)


def _ensure_market_data_window() -> None:
    """Prevent upstream AutoTrender intraday/graph calls before 09:30 IST."""
    now = datetime.now(INDIA_TZ)
    if now.time() < MARKET_DATA_START:
        raise HTTPException(
            status_code=425,
            detail="AutoTrender intraday and open-interest data are available from 9:30 AM IST. Please try again after the market data window opens.",
        )


_INDEX_CATEGORIES = {
    "NIFTY": "Nifty",
    "BANKNIFTY": "BankNifty",
    "FINNIFTY": "FinNifty",
    "MIDCPNIFTY": "MidcpNifty",
    "SENSEX": "Sensex",
    "BANKEX": "Bankex",
}


def _auth(request: Request) -> AutoTrenderAuth:
    auth = getattr(request.app.state, "autotrender_auth", None)
    if auth is None:
        auth = AutoTrenderAuth()
        request.app.state.autotrender_auth = auth
    return auth


def _cache(request: Request) -> AutoTrenderCache:
    cache = getattr(request.app.state, "autotrender_cache", None)
    if cache is None:
        cache = AutoTrenderCache(_auth(request))
        request.app.state.autotrender_cache = cache
    return cache


def _get_json(auth: AutoTrenderAuth, endpoint: str, **params) -> dict:
    try:
        response = auth.get(endpoint, params=params)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" not in content_type:
            raise TypeError("AutoTrender returned a non-JSON response")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("AutoTrender returned an unexpected response")
        return payload
    except requests.Timeout as exc:
        raise HTTPException(
            status_code=504,
            detail="AutoTrender is taking too long to respond. Please try again shortly.",
        ) from exc
    except (RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
        raise HTTPException(
            status_code=502,
            detail="AutoTrender data is temporarily unavailable. Please retry in a little while; other TradingPlatform features remain available.",
        ) from exc


@router.get("/index-price")
def index_price(request: Request) -> dict:
    """Return AutoTrender Index Price from process memory, refreshing after its TTL."""
    try:
        return _cache(request).get_index_price()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/indices/{index}/intraday")
def index_intraday(
    index: str,
    request: Request,
    mins: int = Query(default=5, ge=1, le=60),
) -> dict:
    """Return AutoTrender intraday option-strength data for a supported index."""
    _ensure_market_data_window()
    category = _INDEX_CATEGORIES.get(index.upper())
    if category is None:
        raise HTTPException(status_code=404, detail="Supported indices are NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, and BANKEX")
    return _get_json(
        _auth(request),
        "/api/derivatives/intra-day",
        category=category,
        mins=mins,
    )


@router.get("/indices/{index}/oi-graph")
def index_oi_graph(index: str, request: Request) -> dict:
    """Return AutoTrender change-in-open-interest graph data for a supported index."""
    _ensure_market_data_window()
    category = _INDEX_CATEGORIES.get(index.upper())
    if category is None:
        raise HTTPException(status_code=404, detail="Supported indices are FINNIFTY and SENSEX")
    return _get_json(
        _auth(request),
        "/api/derivatives/table/graph",
        category=category,
    )


@router.get("/cache/status")
def cache_status(request: Request) -> dict:
    """Report the in-memory index-price cache age and refresh status without querying MySQL."""
    return _cache(request).status()


@router.get("/cache/content")
def cache_content(request: Request) -> dict:
    """Read-only, redacted view of currently cached index-price data."""
    return _cache(request).viewer()


@router.get("/worker/status")
def autotrender_worker_status() -> dict:
    """Report the latest AutoTrender paper-entry evaluator cycle."""
    from app.services.autotrender_paper_trade_worker import status

    return status()


@router.get("/historical")
def historical_cache_snapshots(
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List daily AutoTrender cache snapshots stored in MySQL."""
    clauses = ["snapshot_type = :snapshot_type", "snapshot_key = :snapshot_key"]
    params: dict = {"snapshot_type": "AUTOTRENDER_CACHE", "snapshot_key": "index-price", "limit": limit, "offset": offset}
    if start_date:
        clauses.append("trading_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("trading_date <= :end_date")
        params["end_date"] = end_date
    where_sql = " AND ".join(clauses)

    with get_engine().connect() as connection:
        total = connection.execute(
            text(f"SELECT COUNT(*) FROM market_snapshots WHERE {where_sql}"),
            params,
        ).scalar_one()
        rows = connection.execute(
            text(
                "SELECT snapshot_id, trading_date, captured_at, created_at, OCTET_LENGTH(payload) AS payload_bytes "
                f"FROM market_snapshots WHERE {where_sql} "
                "ORDER BY trading_date DESC, captured_at DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings().all()

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "snapshots": [
            {
                "snapshot_id": row["snapshot_id"],
                "trading_date": row["trading_date"].isoformat() if row["trading_date"] else None,
                "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "payload_bytes": row["payload_bytes"],
            }
            for row in rows
        ],
    }


@router.get("/historical/{snapshot_id}")
def historical_cache_snapshot(snapshot_id: int) -> dict:
    """Read a single stored AutoTrender cache snapshot from MySQL."""
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT snapshot_id, snapshot_type, snapshot_key, trading_date, captured_at, "
                "created_at, payload, HEX(payload_hash) AS payload_hash "
                "FROM market_snapshots WHERE snapshot_id = :snapshot_id "
                "AND snapshot_type = :snapshot_type AND snapshot_key = :snapshot_key"
            ),
            {
                "snapshot_id": snapshot_id,
                "snapshot_type": "AUTOTRENDER_CACHE",
                "snapshot_key": "index-price",
            },
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Historical AutoTrender snapshot not found")
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "snapshot_id": row["snapshot_id"],
        "snapshot_type": row["snapshot_type"],
        "snapshot_key": row["snapshot_key"],
        "trading_date": row["trading_date"].isoformat() if row["trading_date"] else None,
        "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "payload_hash": row["payload_hash"],
        "payload": payload,
    }
