from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.providers.kite.client import KiteProvider, KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services.live_quotes import live_quotes

router = APIRouter()


class SessionExchangeRequest(BaseModel):
    request_token: str = Field(min_length=1, max_length=256)


class QuoteResponse(BaseModel):
    instrument_token: int
    last_price: float
    last_trade_time: str | None = None
    received_at: str


@router.get("/kite/login-url", tags=["kite"])
def kite_login_url() -> dict[str, str]:
    settings = get_settings()
    if not settings.kite_api_key:
        raise HTTPException(status_code=503, detail="KITE_API_KEY is not configured")
    return {"login_url": KiteProvider.login_url(settings.kite_api_key)}


@router.post("/kite/session", tags=["kite"])
def exchange_session(payload: SessionExchangeRequest) -> dict[str, str]:
    settings = get_settings()
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Kite API credentials are not configured")
    try:
        session = KiteProvider.exchange_request_token(
            api_key=settings.kite_api_key,
            api_secret=settings.kite_api_secret,
            request_token=payload.request_token,
        )
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Token is returned only to the caller; this initial scaffold does not log it.
    return {"status": "authenticated", "access_token": session.access_token, "user_id": session.user_id}


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
def get_quotes(instrument_tokens: list[int] = Query(..., min_length=1, max_length=200)) -> dict:
    provider = get_kite_provider()
    try:
        result = provider.quotes(instrument_tokens)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"quotes": result}
