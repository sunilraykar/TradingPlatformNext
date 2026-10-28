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


def _ensure_intraday_table() -> None:
    """Create the durable calculated AutoTrender intraday table if needed."""
    ddl = """
    CREATE TABLE IF NOT EXISTS autotrender_intraday_rows (
        row_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        trading_date DATE NOT NULL,
        index_name VARCHAR(24) NOT NULL,
        interval_minutes INT NOT NULL,
        row_time VARCHAR(32) NOT NULL,
        call_value DECIMAL(18, 4) NULL,
        put_value DECIMAL(18, 4) NULL,
        diff_value DECIMAL(18, 4) NULL,
        pcr_value DECIMAL(18, 6) NULL,
        price_value DECIMAL(18, 4) NULL,
        vwap_value DECIMAL(18, 4) NULL,
        vwap_signal VARCHAR(16) NULL,
        option_signal VARCHAR(16) NULL,
        call_strength DECIMAL(12, 6) NULL,
        put_strength DECIMAL(12, 6) NULL,
        strength_diff DECIMAL(12, 6) NULL,
        vwap_price_buffer_pct DECIMAL(12, 6) NULL,
        enter_signal VARCHAR(16) NULL,
        raw_row JSON NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_autotrender_intraday_point (trading_date, index_name, interval_minutes, row_time),
        KEY ix_autotrender_intraday_date_index (trading_date, index_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with get_engine().begin() as connection:
        connection.execute(text(ddl))


def _number_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.post("/indices/{index}/intraday/persist")
async def persist_index_intraday(index: str, request: Request) -> dict:
    """Persist every calculated intraday row for the selected index and interval."""
    normalized_index = index.upper()
    if normalized_index not in _INDEX_CATEGORIES:
        raise HTTPException(status_code=404, detail="Unsupported AutoTrender index")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="A JSON body is required") from exc
    if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
        raise HTTPException(status_code=400, detail="Request must contain a rows array")
    mins = body.get("mins", 5)
    if not isinstance(mins, int) or mins < 1 or mins > 60:
        raise HTTPException(status_code=400, detail="mins must be an integer from 1 to 60")
    rows = body["rows"]
    if len(rows) > 2000:
        raise HTTPException(status_code=413, detail="Too many intraday rows in one request")
    trading_date = datetime.now(INDIA_TZ).date()
    if not rows:
        return {"index": normalized_index, "trading_date": trading_date.isoformat(), "saved": 0}
    _ensure_intraday_table()
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_time = str(row.get("time") or "").strip()
        if not row_time:
            continue
        records.append({
            "trading_date": trading_date,
            "index_name": normalized_index,
            "interval_minutes": mins,
            "row_time": row_time[:32],
            "call_value": _number_or_none(row.get("call")),
            "put_value": _number_or_none(row.get("put")),
            "diff_value": _number_or_none(row.get("diff")),
            "pcr_value": _number_or_none(row.get("pcr")),
            "price_value": _number_or_none(row.get("price")),
            "vwap_value": _number_or_none(row.get("vwap")),
            "vwap_signal": str(row.get("vwapSignal") or "")[:16] or None,
            "option_signal": str(row.get("optionSignal") or "")[:16] or None,
            "call_strength": _number_or_none(row.get("callStrength")),
            "put_strength": _number_or_none(row.get("putStrength")),
            "strength_diff": _number_or_none(row.get("strengthDiff")),
            "vwap_price_buffer_pct": _number_or_none(row.get("buffer")),
            "enter_signal": str(row.get("enter") or "")[:16] or None,
            "raw_row": json.dumps(row, ensure_ascii=False, default=str),
        })
    if not records:
        return {"index": normalized_index, "trading_date": trading_date.isoformat(), "saved": 0}
    statement = text("""
        INSERT INTO autotrender_intraday_rows (
            trading_date, index_name, interval_minutes, row_time,
            call_value, put_value, diff_value, pcr_value, price_value, vwap_value,
            vwap_signal, option_signal, call_strength, put_strength, strength_diff,
            vwap_price_buffer_pct, enter_signal, raw_row
        ) VALUES (
            :trading_date, :index_name, :interval_minutes, :row_time,
            :call_value, :put_value, :diff_value, :pcr_value, :price_value, :vwap_value,
            :vwap_signal, :option_signal, :call_strength, :put_strength, :strength_diff,
            :vwap_price_buffer_pct, :enter_signal, :raw_row
        ) AS incoming
        ON DUPLICATE KEY UPDATE
            call_value = incoming.call_value,
            put_value = incoming.put_value,
            diff_value = incoming.diff_value,
            pcr_value = incoming.pcr_value,
            price_value = incoming.price_value,
            vwap_value = incoming.vwap_value,
            vwap_signal = incoming.vwap_signal,
            option_signal = incoming.option_signal,
            call_strength = incoming.call_strength,
            put_strength = incoming.put_strength,
            strength_diff = incoming.strength_diff,
            vwap_price_buffer_pct = incoming.vwap_price_buffer_pct,
            enter_signal = incoming.enter_signal,
            raw_row = incoming.raw_row
    """)
    with get_engine().begin() as connection:
        connection.execute(statement, records)
    return {"index": normalized_index, "trading_date": trading_date.isoformat(), "saved": len(records), "interval_minutes": mins}


@router.get("/indices/{index}/intraday/history")
def historical_intraday_rows(
    index: str,
    trading_date: Annotated[date, Query()],
    mins: Annotated[int, Query(ge=1, le=60)] = 5,
) -> dict:
    """Return persisted calculated intraday rows for an index and trading date."""
    normalized_index = index.upper()
    if normalized_index not in _INDEX_CATEGORIES:
        raise HTTPException(status_code=404, detail="Unsupported AutoTrender index")
    _ensure_intraday_table()
    with get_engine().connect() as connection:
        rows = connection.execute(text("""
            SELECT row_time AS time, call_value AS call, put_value AS put,
                   diff_value AS diff, pcr_value AS pcr, price_value AS price,
                   vwap_value AS vwap, vwap_signal AS vwapSignal,
                   option_signal AS optionSignal, call_strength AS callStrength,
                   put_strength AS putStrength, strength_diff AS strengthDiff,
                   vwap_price_buffer_pct AS buffer, enter_signal AS enter,
                   raw_row AS rawRow
            FROM autotrender_intraday_rows
            WHERE trading_date = :trading_date AND index_name = :index_name
              AND interval_minutes = :mins
            ORDER BY row_time DESC
        """), {"trading_date": trading_date, "index_name": normalized_index, "mins": mins}).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        raw = item.pop("rawRow", None)
        if isinstance(raw, str):
            try:
                item["rawRow"] = json.loads(raw)
            except ValueError:
                item["rawRow"] = raw
        result.append({key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in item.items()})
    return {"index": normalized_index, "trading_date": trading_date.isoformat(), "interval_minutes": mins, "total": len(result), "rows": result}


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
