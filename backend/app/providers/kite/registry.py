from functools import lru_cache

from app.core.config import get_settings
from app.providers.kite.client import KiteProvider


@lru_cache
def get_kite_provider() -> KiteProvider:
    settings = get_settings()
    return KiteProvider(
        api_key=settings.kite_api_key,
        access_token=settings.kite_access_token,
    )
