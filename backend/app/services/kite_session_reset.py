"""Configurable daily local Kite-session reset scheduler."""
import asyncio
import logging
import webbrowser
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings
from app.providers.kite.client import KiteProvider
from app.providers.kite.registry import clear_active_kite_session
from app.services.kite_sessions import KiteSessionStoreError, reset_sessions
from app.services.live_quotes import live_quotes

logger = logging.getLogger(__name__)


def _reset_due(now: datetime, reset_time: str) -> bool:
    try:
        hour_text, minute_text = reset_time.split(":", maxsplit=1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return False
    except (ValueError, AttributeError):
        return False
    return now.hour == hour and now.minute == minute


async def run_daily_reset_scheduler() -> None:
    """Check once per minute and reset at most once per local calendar day."""
    last_reset_date = None
    while True:
        settings = get_settings()
        if settings.kite_session_reset_enabled:
            try:
                timezone = ZoneInfo(settings.kite_session_reset_timezone)
                now = datetime.now(timezone)
                if _reset_due(now, settings.kite_session_reset_time) and last_reset_date != now.date():
                    try:
                        reset_sessions()
                    finally:
                        clear_active_kite_session()
                        live_quotes.clear()
                    last_reset_date = now.date()
                    logger.info("Scheduled local Kite session reset completed")
                    if (
                        settings.kite_auto_login_enabled
                        and settings.app_env.lower() == "development"
                        and settings.kite_api_key
                    ):
                            opened = webbrowser.open(KiteProvider.login_url(settings.kite_api_key))
                            logger.info(
                                "Daily Kite login initiated (browser opened=%s)",
                                opened,
                            )
            except (ZoneInfoNotFoundError, KiteSessionStoreError):
                logger.exception("Scheduled Kite session reset failed")
        await asyncio.sleep(30)
