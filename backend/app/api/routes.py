import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_engine
from app.providers.kite.client import KiteProvider, KiteProviderError
from app.providers.kite.registry import configure_active_kite_session, get_kite_provider
from app.services import live_market_data
from app.services.instrument_master import import_instrument_master
from app.services.kite_sessions import KiteSessionStoreError, save_session
from app.services.live_quotes import live_quotes

router = APIRouter()


class SessionExchangeRequest(BaseModel):
    request_token: str = Field(min_length=1, max_length=256)


class QuoteResponse(BaseModel):
    instrument_token: int
    last_price: float
    last_trade_time: str | None = None
    received_at: str


class SubscriptionRequest(BaseModel):
    instrument_tokens: list[int] = Field(min_length=1, max_length=500)
    mode: str = "quote"


class UnsubscribeRequest(BaseModel):
    instrument_tokens: list[int] = Field(min_length=1, max_length=500)


def _exchange_and_save(request_token: str) -> str:
    settings = get_settings()
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Kite API credentials are not configured")
    try:
        session = KiteProvider.exchange_request_token(
            api_key=settings.kite_api_key,
            api_secret=settings.kite_api_secret,
            request_token=request_token,
        )
        save_session(session.user_id, session.access_token)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KiteSessionStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    configure_active_kite_session(session.access_token)
    try:
        live_market_data.start()
    except (RuntimeError, ValueError, OSError):
        # Authentication remains valid even if the WebSocket cannot connect yet.
        logging.getLogger(__name__).exception("Kite session saved but WebSocket startup failed")
    return session.user_id


@router.get("/kite/login-url", tags=["kite"])
def kite_login_url() -> dict[str, str]:
    settings = get_settings()
    if not settings.kite_api_key:
        raise HTTPException(status_code=503, detail="KITE_API_KEY is not configured")
    return {"login_url": KiteProvider.login_url(settings.kite_api_key)}


@router.get("/kite/callback", tags=["kite"], response_class=HTMLResponse)
def kite_login_callback(
    request_token: str = Query(..., min_length=1, max_length=256),
    status: str = Query(default="success"),
) -> HTMLResponse:
    if status != "success":
        return HTMLResponse(
            "<h2>Kite login was not completed.</h2><p>You may close this tab and retry from TradingPlatformNext.</p>",
            status_code=401,
        )
    _exchange_and_save(request_token)
    return HTMLResponse(
        "<!doctype html><html><head><title>TradingPlatformNext</title></head>"
        "<body><h2>Kite login successful</h2>"
        "<p>Your session was saved securely. You may return to TradingPlatformNext.</p></body></html>"
    )


@router.post("/kite/session", tags=["kite"])
def exchange_session(payload: SessionExchangeRequest) -> dict[str, str]:
    user_id = _exchange_and_save(payload.request_token)
    return {"status": "authenticated", "user_id": user_id}


@router.get("/kite/session/status", tags=["kite"])
def kite_session_status() -> dict[str, str | bool]:
    provider = get_kite_provider()
    try:
        profile = provider.profile()
    except KiteProviderError:
        return {"authenticated": False, "status": "login_required"}
    return {
        "authenticated": True,
        "status": "authenticated",
        "user_id": str(profile.get("user_id", "")),
    }


@router.get("/kite/market-data/status", tags=["kite"])
def market_data_status() -> dict:
    return live_market_data.status()


@router.post("/kite/market-data/start", tags=["kite"])
def start_market_data() -> dict:
    try:
        return live_market_data.start()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not start Kite WebSocket") from exc


@router.post("/kite/market-data/subscribe", tags=["kite"])
def subscribe_market_data(payload: SubscriptionRequest) -> dict:
    try:
        return live_market_data.subscribe(payload.instrument_tokens, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not subscribe to Kite instruments") from exc


@router.post("/kite/market-data/unsubscribe", tags=["kite"])
def unsubscribe_market_data(payload: UnsubscribeRequest) -> dict:
    return live_market_data.unsubscribe(payload.instrument_tokens)


@router.get("/kite/quote/{instrument_token}", tags=["kite"])
def get_quote(instrument_token: int) -> dict:
    cached = live_quotes.get(instrument_token)
    if cached:
        return cached
    provider = get_kite_provider()
    try:
        return provider.quote(instrument_token)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/kite/quotes", tags=["kite"])
def get_quotes(
    instrument_tokens: list[int] = Query(..., min_length=1, max_length=200),  # noqa: B008
) -> dict:
    provider = get_kite_provider()
    try:
        result = provider.quotes(instrument_tokens)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"quotes": result}



@router.post("/kite/instruments/import", tags=["kite"])
def import_kite_instruments() -> dict:
    """Download and persist Kite's complete instrument master."""
    try:
        return import_instrument_master()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/instruments", tags=["instruments"])
def list_instruments(
    search: str | None = Query(default=None, min_length=1, max_length=100),
    exchange: str | None = Query(default=None, max_length=30),
    segment: str | None = Query(default=None, max_length=40),
    instrument_type: str | None = Query(default=None, max_length=30),
    expiry: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Search the active instrument master, including expiry/option filtering."""
    clauses = ["is_active = TRUE"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if search:
        clauses.append("(tradingsymbol LIKE :search OR name LIKE :search)")
        params["search"] = f"%{search.strip()}%"
    if exchange:
        clauses.append("exchange = :exchange")
        params["exchange"] = exchange.upper()
    if segment:
        clauses.append("segment = :segment")
        params["segment"] = segment.upper()
    if instrument_type:
        clauses.append("instrument_type = :instrument_type")
        params["instrument_type"] = instrument_type.upper()
    if expiry:
        try:
            from datetime import date

            params["expiry"] = date.fromisoformat(expiry)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="expiry must use YYYY-MM-DD format") from exc
        clauses.append("expiry = :expiry")

    where_sql = " AND ".join(clauses)
    try:
        with get_engine().connect() as connection:
            total = connection.execute(
                text(f"SELECT COUNT(*) FROM instruments WHERE {where_sql}"), params
            ).scalar_one()
            rows = connection.execute(
                text(
                    "SELECT instrument_token, exchange, tradingsymbol, name, instrument_type, "
                    "segment, expiry, strike, lot_size, tick_size, instrument_version, updated_at "
                    f"FROM instruments WHERE {where_sql} "
                    "ORDER BY exchange, tradingsymbol LIMIT :limit OFFSET :offset"
                ),
                params,
            ).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Instrument database unavailable") from None

    instruments = [
        {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()}
        for row in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "instruments": instruments}


@router.get("/health/db", tags=["health"])
def database_health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None

    return {"status": "ok", "database": "connected"}
