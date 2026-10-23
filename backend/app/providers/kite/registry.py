from functools import lru_cache

from app.core.config import get_settings
from app.providers.kite.client import KiteProvider

_runtime_access_token: str | None = None


@lru_cache
def get_kite_provider() -> KiteProvider:
    settings = get_settings()
    access_token = (
        _runtime_access_token
        if _runtime_access_token is not None
        else settings.kite_access_token
    )
    return KiteProvider(api_key=settings.kite_api_key, access_token=access_token)


def configure_active_kite_session(access_token: str) -> None:
    global _runtime_access_token
    _runtime_access_token = access_token
    get_kite_provider.cache_clear()


def clear_active_kite_session() -> None:
    global _runtime_access_token
    _runtime_access_token = ""
    get_kite_provider.cache_clear()


def get_active_kite_token() -> str:
    if _runtime_access_token is not None:
        return _runtime_access_token
    return get_settings().kite_access_token
