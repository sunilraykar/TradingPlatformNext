from fastapi import APIRouter, HTTPException, Query, Request
import requests

from app.providers.autotrender.auth import AutoTrenderAuth
from app.services.autotrender_cache import AutoTrenderCache

router = APIRouter(prefix="/autotrender", tags=["AutoTrender Market Data"])

# Use AutoTrender's exact category values; these map to its derivative endpoints.
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
    except (RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
        raise HTTPException(
            status_code=502,
            detail="AutoTrender market data is unavailable or invalid",
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
