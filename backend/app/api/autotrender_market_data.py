from fastapi import APIRouter, HTTPException, Request

from app.providers.autotrender.auth import AutoTrenderAuth
from app.services.autotrender_cache import AutoTrenderCache

router = APIRouter(prefix="/autotrender", tags=["AutoTrender Market Data"])


def _cache(request: Request) -> AutoTrenderCache:
    cache = getattr(request.app.state, "autotrender_cache", None)
    if cache is None:
        auth = getattr(request.app.state, "autotrender_auth", None)
        if auth is None:
            auth = AutoTrenderAuth()
            request.app.state.autotrender_auth = auth
        cache = AutoTrenderCache(auth)
        request.app.state.autotrender_cache = cache
    return cache


@router.get("/index-price")
def index_price(request: Request) -> dict:
    """Return AutoTrender Index Price from process memory, refreshing after its TTL."""
    try:
        return _cache(request).get_index_price()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/cache/status")
def cache_status(request: Request) -> dict:
    """Report the in-memory cache age and refresh status without querying MySQL."""
    return _cache(request).status()
