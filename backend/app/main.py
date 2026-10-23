import asyncio
import logging
import webbrowser
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.providers.kite.client import KiteProvider, KiteProviderError
from app.providers.kite.registry import (
    clear_active_kite_session,
    configure_active_kite_session,
)
from app.services.kite_session_reset import run_daily_reset_scheduler
from app.services.kite_sessions import (
    KiteSessionStoreError,
    load_session,
    reset_sessions,
)

logger = logging.getLogger(__name__)


async def _restore_kite_session() -> None:
    settings = get_settings()
    if not settings.kite_api_key:
        logger.warning("KITE_API_KEY is not configured; automatic Kite login is unavailable")
        return
    try:
        saved = load_session()
    except KiteSessionStoreError:
        logger.exception("Unable to restore the saved Kite session")
        saved = None

    if saved:
        user_id, access_token = saved
        configure_active_kite_session(access_token)
        try:
            profile = KiteProvider(settings.kite_api_key, access_token).profile()
            logger.info("Restored saved Kite session for user %s", profile.get("user_id", user_id))
            return
        except KiteProviderError:
            logger.info("Saved Kite session is no longer valid; login is required")
            try:
                reset_sessions()
            except KiteSessionStoreError:
                logger.exception("Could not mark the invalid saved Kite session as expired")
            clear_active_kite_session()
    elif settings.kite_access_token:
        try:
            KiteProvider(settings.kite_api_key, settings.kite_access_token).profile()
            logger.info("Using configured Kite access token")
            return
        except KiteProviderError:
            logger.info("Configured Kite access token is no longer valid")

    if settings.kite_auto_login_enabled and settings.app_env.lower() == "development":
        login_url = KiteProvider.login_url(settings.kite_api_key)
        opened = webbrowser.open(login_url)
        logger.info("Kite login required; browser launch requested (opened=%s)", opened)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    await _restore_kite_session()
    reset_task = asyncio.create_task(run_daily_reset_scheduler())
    try:
        yield
    finally:
        reset_task.cancel()
        with suppress(asyncio.CancelledError):
            await reset_task


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trading-platform-next"}
