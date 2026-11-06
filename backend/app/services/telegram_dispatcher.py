"""Independent Telegram delivery worker for durable notification queue rows."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_engine

logger = logging.getLogger(__name__)
_RETRY_DELAYS = (5, 30, 120, 600)
_REQUEST_TIMEOUT_SECONDS = 15


def _setting(key: str, default: Any) -> Any:
    with get_engine().connect() as connection:
        row = connection.execute(text(
            "SELECT setting_value FROM app_settings WHERE setting_key = :key"
        ), {"key": "ui." + key}).first()
    if not row:
        return default
    value = row[0]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def retry_delay_seconds(attempt_number: int, retry_after: int | None = None) -> int:
    if retry_after is not None:
        return max(1, int(retry_after))
    defaults = (5, 30, 120, 600)
    delays = tuple(max(1, min(3600, int(_setting(f"telegramRetryDelay{i}Seconds", default))))
                   for i, default in enumerate(defaults, start=1))
    index = max(0, min(attempt_number - 1, len(delays) - 1))
    return delays[index]


def format_notification_message(event_type: str, category: str, payload: dict[str, Any]) -> str:
    custom_text = payload.get("telegram_text")
    if custom_text:
        message = str(custom_text)
    else:
        lines = [f"TradingPlatformNext · {category}", event_type.replace("_", " ").title()]
        for key, value in payload.items():
            if key == "telegram_text" or value is None or isinstance(value, (dict, list)):
                continue
            label = key.replace("_", " ").strip().title()
            lines.append(f"{label}: {value}")
        message = "\n".join(lines)
    return message[:4096]


def _decode_setting(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _telegram_credentials() -> tuple[str, str] | None:
    with get_engine().connect() as connection:
        rows = connection.execute(text("""
            SELECT setting_key, setting_value
            FROM app_settings
            WHERE setting_key IN (
                'integration.telegram.bot_token',
                'integration.telegram.chat_id',
                'integration.telegram.enabled'
            )
        """)).mappings().all()
    settings = {row["setting_key"]: _decode_setting(row["setting_value"]) for row in rows}
    if not bool(settings.get("integration.telegram.enabled")):
        return None
    encrypted = settings.get("integration.telegram.bot_token")
    chat_id = str(settings.get("integration.telegram.chat_id") or "").strip()
    key = get_settings().kite_token_encryption_key
    if not encrypted or not chat_id or not key:
        logger.error("Telegram dispatcher is enabled but its credentials are incomplete")
        return None
    try:
        token = Fernet(key.encode("utf-8")).decrypt(str(encrypted).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.exception("Telegram dispatcher could not decrypt its configured Bot Token")
        return None
    return token, chat_id


def _claim_next_job() -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        # Recover a worker claim left behind by a process restart.
        connection.execute(text("""
            UPDATE notification_queue
            SET status = 'PENDING', locked_at = NULL,
                next_attempt_at = UTC_TIMESTAMP(6)
            WHERE status = 'PROCESSING'
              AND locked_at < UTC_TIMESTAMP(6) - INTERVAL 5 MINUTE
        """))
        row = connection.execute(text("""
            SELECT q.queue_id, q.event_id, q.attempts,
                   e.event_type, e.category, e.payload
            FROM notification_queue q
            JOIN notification_events e ON e.event_id = q.event_id
            WHERE q.status = 'PENDING'
              AND q.next_attempt_at <= UTC_TIMESTAMP(6)
            ORDER BY q.queue_id
            LIMIT 1
            FOR UPDATE
        """)).mappings().first()
        if not row:
            return None
        connection.execute(text("""
            UPDATE notification_queue
            SET status = 'PROCESSING', locked_at = UTC_TIMESTAMP(6)
            WHERE queue_id = :queue_id
        """), {"queue_id": row["queue_id"]})
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "queue_id": int(row["queue_id"]),
            "event_id": str(row["event_id"]),
            "attempts": int(row["attempts"]),
            "event_type": str(row["event_type"]),
            "category": str(row["category"]),
            "payload": payload if isinstance(payload, dict) else {},
        }


def _send_message(token: str, chat_id: str, message: str, timeout_seconds: int | None = None) -> tuple[bool, int | None, int | None, str | None, dict[str, Any]]:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=timeout_seconds or _REQUEST_TIMEOUT_SECONDS,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.ok and body.get("ok"):
            result = body.get("result") or {}
            return True, response.status_code, result.get("message_id"), None, body
        description = str(body.get("description") or f"Telegram API HTTP {response.status_code}")
        retry_after = (body.get("parameters") or {}).get("retry_after")
        if response.status_code == 429:
            return False, response.status_code, int(retry_after or 1), description, body
        if response.status_code >= 500:
            return False, response.status_code, None, description, body
        return False, response.status_code, -1, description, body
    except requests.RequestException as exc:
        return False, None, None, f"Telegram request failed ({type(exc).__name__})", {}


def dispatch_pending_once() -> bool:
    credentials = _telegram_credentials()
    if not credentials:
        return False
    job = _claim_next_job()
    if not job:
        return False

    token, chat_id = credentials
    message = format_notification_message(job["event_type"], job["category"], job["payload"])
    timeout_seconds = max(5, min(60, int(_setting("telegramRequestTimeoutSeconds", _REQUEST_TIMEOUT_SECONDS))))
    success, http_status, result_value, error, response_body = _send_message(token, chat_id, message, timeout_seconds)
    attempt_number = job["attempts"] + 1
    retry_after = result_value if http_status == 429 and result_value and result_value > 0 else None
    permanent_failure = result_value == -1
    retryable = not success and not permanent_failure
    max_attempts = max(1, min(10, int(_setting("telegramMaxDeliveryAttempts", 5))))
    exhausted = retryable and attempt_number >= max_attempts
    if success:
        outcome, new_status, next_attempt = "SENT", "SENT", None
        message_id = result_value
    elif permanent_failure or exhausted:
        outcome, new_status, next_attempt = "FAILED", "FAILED", None
        message_id = None
    else:
        outcome, new_status = "RETRY", "PENDING"
        delay = retry_delay_seconds(attempt_number, retry_after)
        next_attempt = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=delay)
        message_id = None

    with get_engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO notification_delivery_log
              (queue_id, attempt_number, outcome, http_status, telegram_message_id,
               error_text, response_payload)
            VALUES
              (:queue_id, :attempt_number, :outcome, :http_status, :message_id,
               :error_text, CAST(:response_payload AS JSON))
        """), {
            "queue_id": job["queue_id"],
            "attempt_number": attempt_number,
            "outcome": outcome,
            "http_status": http_status,
            "message_id": message_id,
            "error_text": error,
            "response_payload": json.dumps(response_body, separators=(",", ":")) if response_body else None,
        })
        connection.execute(text("""
            UPDATE notification_queue
            SET status = :status, attempts = :attempts, next_attempt_at = :next_attempt_at,
                locked_at = NULL, last_error = :last_error
            WHERE queue_id = :queue_id
        """), {
            "status": new_status,
            "attempts": attempt_number,
            "next_attempt_at": next_attempt or datetime.now(UTC).replace(tzinfo=None),
            "last_error": error,
            "queue_id": job["queue_id"],
        })
    if success:
        logger.info("Telegram notification delivered: event_id=%s", job["event_id"])
    elif new_status == "FAILED":
        logger.error("Telegram notification permanently failed: event_id=%s error=%s", job["event_id"], error)
    else:
        logger.warning("Telegram notification delivery will retry: event_id=%s error=%s", job["event_id"], error)
    return True


async def run_scheduler() -> None:
    """Poll and deliver queued notifications without running on the trading request path."""
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_pending_once)
        except SQLAlchemyError:
            logger.exception("Telegram notification dispatcher database operation failed")
            worked = False
        except Exception:
            logger.exception("Telegram notification dispatcher cycle failed")
            worked = False
        active_ms = max(100, min(5000, int(_setting("telegramDispatcherActivePollMilliseconds", 500))))
        idle_seconds = max(1, min(60, int(_setting("telegramDispatcherIdlePollSeconds", 5))))
        await asyncio.sleep(active_ms / 1000 if worked else idle_seconds)
