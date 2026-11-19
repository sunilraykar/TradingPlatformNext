import asyncio
import logging
import webbrowser
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.account import router as account_router
from app.api.apiplatform_auth import router as apiplatform_auth_router
from app.api.apiplatform_option_chain import router as apiplatform_option_chain_router
from app.api.apiplatform_swing import router as apiplatform_swing_router
from app.api.auth import router as auth_router
from app.api.autotrender_auth import router as autotrender_auth_router
from app.api.autotrender_market_data import router as autotrender_market_data_router
from app.api.kite_instruments import router as kite_instruments_router
from app.api.notifications import router as notifications_router
from app.api.routes import router
from app.api.subscription_test import router as subscription_test_router
from app.api.telegram import router as telegram_router
from app.api.trades import router as trades_router
from app.auth.security import get_session_user, verify_csrf_token
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.providers.apiplatform.registry import configure_apiplatform, shutdown_apiplatform
from app.providers.autotrender.auth import AutoTrenderAuth
from app.providers.kite.client import KiteAuthenticationError, KiteProvider, KiteProviderError
from app.providers.kite.registry import (
    clear_active_kite_session,
    configure_active_kite_session,
)
from app.services import (
    auto_exit_profitable_trades,
    autotrender_cache_snapshot,
    autotrender_kite_execution,
    autotrender_paper_trade_worker,
    kite_trade_notifications,
    live_market_data,
    notification_scheduler,
    paper_option_market_close,
    system_alerts,
    telegram_dispatcher,
    trade_terminal,
)
from app.services.apiplatform_symbol_master import run_startup_symbol_master_refresh
from app.services.autotrender_cache import AutoTrenderCache
from app.services.kite_instrument_master import run_daily_instrument_refresh_scheduler
from app.services.kite_portfolio_snapshots import (
    ensure_portfolio_snapshot_schema,
    run_daily_portfolio_snapshot_scheduler,
)
from app.services.kite_session_reset import run_daily_reset_scheduler
from app.services.kite_sessions import (
    KiteSessionStoreError,
    load_session,
    reset_sessions,
)
from app.services.live_quotes import live_quotes
from app.services.notification_events import ensure_notification_schema

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
        except KiteAuthenticationError:
            logger.info("Saved Kite session was rejected by Kite; login is required")
            try:
                reset_sessions()
            except KiteSessionStoreError:
                logger.exception("Could not mark the invalid saved Kite session as expired")
            clear_active_kite_session()
        except KiteProviderError:
            # A gateway/network failure does not prove that the saved token is invalid.
            # Preserve it so a transient Kite outage cannot silently log the user out.
            logger.warning(
                "Could not validate saved Kite session because Kite API is unavailable; preserving session"
            )
            try:
                live_market_data.start()
            except Exception:
                logger.exception("Saved Kite session retained but WebSocket startup failed")
            return
    elif settings.kite_access_token:
        try:
            KiteProvider(settings.kite_api_key, settings.kite_access_token).profile()
            logger.info("Using configured Kite access token")
            configure_active_kite_session(settings.kite_access_token)
            try:
                live_market_data.start()
            except Exception:
                logger.exception("Configured Kite access token is valid but WebSocket startup failed")
            return
        except KiteAuthenticationError:
            logger.info("Configured Kite access token was rejected; login is required")
        except KiteProviderError:
            logger.warning(
                "Could not validate configured Kite access token because Kite API is unavailable; preserving session"
            )
            configure_active_kite_session(settings.kite_access_token)
            try:
                live_market_data.start()
            except Exception:
                logger.exception("Configured Kite session retained but WebSocket startup failed")
            return

    if settings.kite_auto_login_enabled and settings.app_env.lower() == "development":
        login_url = KiteProvider.login_url(settings.kite_api_key)
        opened = webbrowser.open(login_url)
        logger.info("Kite login required; browser launch requested (opened=%s)", opened)


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    ensure_notification_schema()
    autotrender_kite_execution.ensure_schema()
    system_alerts.install_system_alert_handler()
    ensure_portfolio_snapshot_schema()
    # Restore a valid persisted APIPLATFORM JWT without starting another login.
    apiplatform_client = configure_apiplatform()
    application.state.apiplatform_auth = apiplatform_client.auth
    application.state.apiplatform_client = apiplatform_client
    if not hasattr(application.state, "autotrender_auth"):
        application.state.autotrender_auth = AutoTrenderAuth()
    if not hasattr(application.state, "autotrender_cache"):
        application.state.autotrender_cache = AutoTrenderCache(application.state.autotrender_auth)
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
    auto_exit_task = asyncio.create_task(auto_exit_profitable_trades.run_auto_exit_scheduler())
    paper_market_close_task = asyncio.create_task(paper_option_market_close.run_market_close_scheduler())
    autotrender_worker_task = asyncio.create_task(autotrender_paper_trade_worker.run_scheduler())
    autotrender_kite_execution_task = asyncio.create_task(autotrender_kite_execution.run_scheduler())
    autotrender_snapshot_task = asyncio.create_task(
        autotrender_cache_snapshot.run_daily_snapshot_scheduler(application.state.autotrender_auth)
    )
    telegram_dispatch_task = asyncio.create_task(telegram_dispatcher.run_scheduler())
    notification_schedule_task = asyncio.create_task(notification_scheduler.run_scheduler())
    kite_trade_notification_task = asyncio.create_task(kite_trade_notifications.run_scheduler())
    portfolio_snapshot_task = asyncio.create_task(run_daily_portfolio_snapshot_scheduler())
    apiplatform_symbol_master_task = asyncio.create_task(
        run_startup_symbol_master_refresh(apiplatform_client)
    )
    try:
        yield
    finally:
        live_quotes.remove_listener(trade_terminal.apply_quote_to_open_trades)
        tasks = (
            reset_task, trade_task, instrument_task, auto_exit_task, paper_market_close_task,
            autotrender_worker_task, autotrender_kite_execution_task, autotrender_snapshot_task, telegram_dispatch_task,
            notification_schedule_task, kite_trade_notification_task, portfolio_snapshot_task, apiplatform_symbol_master_task,
        )
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        shutdown_apiplatform()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({origin.strip().rstrip("/") for origin in settings.cors_allowed_origins.split(",") if origin.strip()} | ({settings.app_public_url.rstrip("/")} if settings.app_public_url else set())),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "Authorization", "X-CSRF-Token"],
)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(autotrender_auth_router, prefix="/api/v1")
app.include_router(apiplatform_auth_router, prefix="/api/v1")
app.include_router(apiplatform_option_chain_router, prefix="/api/v1")
app.include_router(apiplatform_swing_router, prefix="/api/v1")
app.include_router(autotrender_market_data_router, prefix="/api/v1")
app.include_router(subscription_test_router, prefix="/api/v1")
app.include_router(trades_router, prefix="/api/v1")
app.include_router(telegram_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(kite_instruments_router, prefix="/api/v1")


PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/csrf",
    "/api/v1/auth/bootstrap/status",
    "/api/v1/auth/bootstrap",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/verification/resend",
    "/api/v1/auth/invitations/accept",
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
    "/api/v1/auth/login",
}


@app.middleware("http")
async def application_authentication(request: Request, call_next):
    settings = get_settings()
    path = request.url.path
    if settings.app_env.lower() == "test" or request.method == "OPTIONS":
        return await call_next(request)
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    allowed_origins = {
        item.strip().rstrip("/")
        for item in settings.cors_allowed_origins.split(",")
        if item.strip()
    }
    if settings.app_public_url:
        allowed_origins.add(settings.app_public_url.rstrip("/"))
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "Request origin is not allowed"})

    if request.method not in {"GET", "HEAD"}:
        csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
        csrf_header = request.headers.get("x-csrf-token")
        if (
            not csrf_cookie
            or not csrf_header
            or csrf_cookie != csrf_header
            or not verify_csrf_token(csrf_header)
        ):
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})

    if path in PUBLIC_AUTH_PATHS or path == "/api/v1/kite/callback":
        return await call_next(request)

    raw_session = request.cookies.get(settings.session_cookie_name)
    user = get_session_user(raw_session)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    if user["role"] == "VIEWER" and request.method not in {"GET", "HEAD"}:
        viewer_session_action = (
            path == "/api/v1/auth/logout"
            or (request.method == "DELETE" and path.startswith("/api/v1/auth/sessions/"))
        )
        if not viewer_session_action:
            return JSONResponse(status_code=403, content={"detail": "Viewer accounts have read-only access"})
    request.state.application_user = user
    return await call_next(request)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trading-platform-next"}
