import logging
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramConfigRequest(BaseModel):
    enabled: bool = False
    chat_id: str = Field(default="", max_length=128)
    bot_token: str | None = Field(default=None, max_length=512)
    notifications: dict[str, Any] = Field(default_factory=dict)


def _encryption():
    key = get_settings().kite_token_encryption_key
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Configure KITE_TOKEN_ENCRYPTION_KEY in backend/.env before saving a Telegram Bot Token.",
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="KITE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key.",
        ) from exc


def _read_setting(connection, key: str):
    row = connection.execute(
        text("SELECT setting_value FROM app_settings WHERE setting_key = :key"),
        {"key": key},
    ).first()
    if not row:
        return None
    value = row[0]
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _write_setting(connection, key: str, value: Any, *, secret: bool = False):
    import json
    connection.execute(
        text(
            "INSERT INTO app_settings (setting_key, setting_value, description, is_secret) "
            "VALUES (:key, CAST(:value AS JSON), :description, :secret) "
            "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), "
            "description = VALUES(description), is_secret = VALUES(is_secret), "
            "version = version + 1"
        ),
        {
            "key": key,
            "value": json.dumps(value, separators=(",", ":")),
            "description": "Telegram integration credential" if secret else "Telegram integration setting",
            "secret": secret,
        },
    )


TELEGRAM_NOTIFICATION_DEFAULTS = {
    "telegramPaperTradeEntryEnabled": True,
    "telegramPaperTradeExitEnabled": True,
    "telegramLiveFillEnabled": True,
    "telegramPnlAlertsEnabled": True,
    "telegramPnlAlertThreshold": 1000,
    "telegramPnlAlertCheckSeconds": 15,
    "telegramPnlAlertRearmEnabled": True,
    "telegramActiveSummaryEnabled": True,
    "telegramActiveSummaryIntervalMinutes": 15,
    "telegramActiveSummaryStartTime": "09:15",
    "telegramActiveSummaryEndTime": "15:30",
    "telegramDailySummaryEnabled": True,
    "telegramDailySummaryTime": "15:45",
    "telegramLiveFillPollSeconds": 15,
    "telegramSystemAlertsEnabled": True,
    "telegramSystemAlertSeverity": "ERROR",
    "telegramSystemAlertCooldownMinutes": 15,
    "telegramDispatcherIdlePollSeconds": 5,
    "telegramDispatcherActivePollMilliseconds": 500,
    "telegramRequestTimeoutSeconds": 15,
    "telegramMaxDeliveryAttempts": 5,
    "telegramRetryDelay1Seconds": 5,
    "telegramRetryDelay2Seconds": 30,
    "telegramRetryDelay3Seconds": 120,
    "telegramRetryDelay4Seconds": 600,
}


def _load_config(connection):
    encrypted = _read_setting(connection, "integration.telegram.bot_token")
    chat_id = _read_setting(connection, "integration.telegram.chat_id") or ""
    enabled = _read_setting(connection, "integration.telegram.enabled")
    notification_settings = dict(TELEGRAM_NOTIFICATION_DEFAULTS)
    for key in TELEGRAM_NOTIFICATION_DEFAULTS:
        stored = _read_setting(connection, "ui." + key)
        if stored is not None:
            notification_settings[key] = stored
    return {
        "enabled": bool(enabled), "chat_id": str(chat_id),
        "bot_configured": bool(encrypted), "notifications": notification_settings,
    }


@router.get("/config")
def get_telegram_config():
    try:
        with get_engine().connect() as connection:
            return _load_config(connection)
    except SQLAlchemyError as exc:
        logger.exception("Unable to load Telegram configuration")
        raise HTTPException(status_code=503, detail="Unable to load Telegram settings from MySQL.") from exc


@router.put("/config")
def save_telegram_config(payload: TelegramConfigRequest):
    try:
        with get_engine().begin() as connection:
            current = _read_setting(connection, "integration.telegram.bot_token")
            if payload.bot_token and payload.bot_token.strip():
                encrypted = _encryption().encrypt(payload.bot_token.strip().encode("utf-8")).decode("ascii")
                _write_setting(connection, "integration.telegram.bot_token", encrypted, secret=True)
                current = encrypted
            if payload.enabled and not current:
                raise HTTPException(status_code=422, detail="Enter a Bot Token before enabling Telegram.")
            if payload.enabled and not payload.chat_id.strip():
                raise HTTPException(status_code=422, detail="Enter a Telegram Chat ID before enabling Telegram.")
            _write_setting(connection, "integration.telegram.chat_id", payload.chat_id.strip())
            _write_setting(connection, "integration.telegram.enabled", bool(payload.enabled))
            unknown = set(payload.notifications) - set(TELEGRAM_NOTIFICATION_DEFAULTS)
            if unknown:
                raise HTTPException(status_code=422, detail="Unknown Telegram notification settings: " + ", ".join(sorted(unknown)))
            merged = dict(TELEGRAM_NOTIFICATION_DEFAULTS)
            for key in TELEGRAM_NOTIFICATION_DEFAULTS:
                stored = _read_setting(connection, "ui." + key)
                if stored is not None:
                    merged[key] = stored
            merged.update(payload.notifications)
            for key in ("telegramPnlAlertThreshold",):
                try:
                    if float(merged[key]) <= 0 or float(merged[key]) > 100000000:
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(status_code=422, detail=f"{key} must be a positive number")
            for key, minimum, maximum in (
                ("telegramPnlAlertCheckSeconds", 5, 300),
                ("telegramActiveSummaryIntervalMinutes", 5, 180),
                ("telegramLiveFillPollSeconds", 5, 300),
                ("telegramSystemAlertCooldownMinutes", 1, 1440),
                ("telegramDispatcherIdlePollSeconds", 1, 60),
                ("telegramDispatcherActivePollMilliseconds", 100, 5000),
                ("telegramRequestTimeoutSeconds", 5, 60),
                ("telegramMaxDeliveryAttempts", 1, 10),
                ("telegramRetryDelay1Seconds", 1, 3600),
                ("telegramRetryDelay2Seconds", 1, 3600),
                ("telegramRetryDelay3Seconds", 1, 3600),
                ("telegramRetryDelay4Seconds", 1, 3600),
            ):
                try:
                    value = int(merged[key])
                    if value < minimum or value > maximum:
                        raise ValueError
                    merged[key] = value
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(status_code=422, detail=f"{key} must be between {minimum} and {maximum}")
            import re
            for key in ("telegramActiveSummaryStartTime", "telegramActiveSummaryEndTime", "telegramDailySummaryTime"):
                if not isinstance(merged[key], str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", merged[key]):
                    raise HTTPException(status_code=422, detail=f"{key} must use HH:MM 24-hour time")
            from datetime import time
            if time.fromisoformat(merged["telegramActiveSummaryEndTime"]) <= time.fromisoformat(merged["telegramActiveSummaryStartTime"]):
                raise HTTPException(status_code=422, detail="Active summary end time must be later than its start time")
            if merged["telegramSystemAlertSeverity"] not in {"ERROR", "CRITICAL"}:
                raise HTTPException(status_code=422, detail="System alert severity must be ERROR or CRITICAL")
            for key, value in merged.items():
                _write_setting(connection, "ui." + key, value)
        return {"saved": True, "config": get_telegram_config()}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Unable to save Telegram configuration")
        raise HTTPException(status_code=503, detail="Unable to save Telegram settings to MySQL.") from exc


@router.post("/test-message")
def send_telegram_test_message():
    try:
        with get_engine().connect() as connection:
            encrypted = _read_setting(connection, "integration.telegram.bot_token")
            chat_id = str(_read_setting(connection, "integration.telegram.chat_id") or "").strip()
            enabled = bool(_read_setting(connection, "integration.telegram.enabled"))
        if not enabled:
            raise HTTPException(status_code=409, detail="Enable Telegram integration before sending a test message.")
        if not encrypted or not chat_id:
            raise HTTPException(status_code=422, detail="Save a Bot Token and Chat ID before sending a test message.")
        try:
            token = _encryption().decrypt(str(encrypted).encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise HTTPException(status_code=503, detail="Stored Telegram Bot Token cannot be decrypted. Check KITE_TOKEN_ENCRYPTION_KEY.") from exc
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "TradingPlatformNext Telegram test message. Your bot integration is working.",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok or not result.get("ok"):
            description = result.get("description") or f"Telegram API returned HTTP {response.status_code}."
            raise HTTPException(status_code=502, detail=description)
        return {
            "sent": True,
            "chat_id": chat_id,
            "message_id": (result.get("result") or {}).get("message_id"),
        }
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logger.warning("Telegram test message failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Telegram Bot API. Check backend network connectivity.") from exc
    except SQLAlchemyError as exc:
        logger.exception("Unable to read Telegram configuration for test message")
        raise HTTPException(status_code=503, detail="Unable to read Telegram settings from MySQL.") from exc
