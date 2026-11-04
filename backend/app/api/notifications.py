from uuid import uuid4

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.telegram import get_telegram_config
from app.services.notification_events import enqueue_notification, notification_status

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/status")
def get_notification_status():
    try:
        return notification_status()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Unable to load notification queue status from MySQL.") from exc


@router.post("/test-dispatch")
def queue_telegram_dispatch_test():
    """Queue a test through the same durable dispatcher used by future events."""
    config = get_telegram_config()
    if not config.get("enabled") or not config.get("bot_configured") or not config.get("chat_id"):
        raise HTTPException(
            status_code=409,
            detail="Enable Telegram and save a Bot Token and Chat ID before testing queued delivery.",
        )
    try:
        result = enqueue_notification(
            event_type="NOTIFICATION_TEST",
            category="SYSTEM",
            source="API_TEST",
            entity_type="SYSTEM",
            entity_id="telegram-dispatch-test",
            payload={
                "telegram_text": "TradingPlatformNext Telegram notification queue test. Delivery pipeline is working.",
            },
            deduplication_key=f"telegram-dispatch-test:{uuid4()}",
        )
        return {"queued": True, **result}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Unable to queue the Telegram test notification.") from exc
