import asyncio
import logging
import webbrowser
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.account import router as account_router
from app.api.autotrender_auth import router as autotrender_auth_router
from app.api.autotrender_market_data import router as autotrender_market_data_router
from app.api.routes import router
from app.api.subscription_test import router as subscription_test_router
from app.api.trades import router as trades_router
from app.api.kite_instruments import router as kite_instruments_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.providers.kite.client import KiteProvider, KiteProviderError
from app.providers.kite.registry import (
    clear_active_kite_session,
    configure_active_kite_session,
)
from app.services import live_market_data, trade_terminal
from app.services.live_quotes import live_quotes
from app.services.kite_session_reset import run_daily_reset_scheduler
from app.services.kite_instrument_master import run_daily_instrument_refresh_scheduler
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
            try:
                live_market_data.start()
            except Exception:
                logger.exception("Kite session restored but WebSocket startup failed")
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
            configure_active_kite_session(settings.kite_access_token)
            try:
                live_market_data.start()
            except Exception:
                logger.exception("Configured Kite session is valid but WebSocket startup failed")
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
    trade_terminal.hydrate_cache()
    live_quotes.add_listener(trade_terminal.apply_quote_to_open_trades)
    await _restore_kite_session()
    # Default AutoTrender index feeds are registered on every backend start.
    # Desired tokens remain in memory and will be applied when Kite is authenticated.
    live_market_data.subscribe_autotrender_indices()
    trade_terminal.subscribe_open_trades()
    reset_task = asyncio.create_task(run_daily_reset_scheduler())
    trade_task = asyncio.create_task(trade_terminal.run_maintenance_scheduler())
    instrument_task = asyncio.create_task(run_daily_instrument_refresh_scheduler())
    try:
        yield
    finally:
        live_quotes.remove_listener(trade_terminal.apply_quote_to_open_trades)
        reset_task.cancel()
        trade_task.cancel()
        instrument_task.cancel()
        with suppress(asyncio.CancelledError):
            await reset_task
        with suppress(asyncio.CancelledError):
            await trade_task
        with suppress(asyncio.CancelledError):
            await instrument_task


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(autotrender_auth_router, prefix="/api/v1")
app.include_router(autotrender_market_data_router, prefix="/api/v1")
app.include_router(subscription_test_router, prefix="/api/v1")
app.include_router(trades_router, prefix="/api/v1")
app.include_router(kite_instruments_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trading-platform-next"}
