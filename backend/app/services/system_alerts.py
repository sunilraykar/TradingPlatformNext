"""Rate-limited Telegram forwarding for application ERROR/CRITICAL log records."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.services.notification_events import enqueue_notification, notifications_enabled

IST = ZoneInfo("Asia/Kolkata")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bot\d{6,}:[A-Za-z0-9_-]{20,})"),
    re.compile(r"(?i)(access[_ -]?token|api[_ -]?key|secret|password)\s*[:=]\s*[^\s,;]+"),
)


def _setting(key, default):
    with get_engine().connect() as connection:
        row = connection.execute(text("SELECT setting_value FROM app_settings WHERE setting_key=:key"), {"key": "ui." + key}).first()
    if not row:
        return default
    value = row[0]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _safe(value: str) -> str:
    result = str(value)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result[:1200]


class TelegramSystemAlertHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not notifications_enabled() or not bool(_setting("telegramSystemAlertsEnabled", True)):
                return
            minimum = str(_setting("telegramSystemAlertSeverity", "ERROR")).upper()
            if record.levelno < (logging.CRITICAL if minimum == "CRITICAL" else logging.ERROR):
                return
            if record.name.startswith(("app.services.telegram", "app.services.notification_events", "app.services.system_alerts")):
                return
            cooldown = max(1, min(1440, int(_setting("telegramSystemAlertCooldownMinutes", 15))))
            message = _safe(record.getMessage())
            fingerprint = hashlib.sha256(f"{record.name}|{record.levelname}|{message}".encode("utf-8", "replace")).hexdigest()[:32]
            state_key = "telegram:system-alert:" + fingerprint
            now = datetime.now(IST)
            with get_engine().begin() as connection:
                row = connection.execute(text("SELECT state_json FROM notification_rule_state WHERE state_key=:key FOR UPDATE"), {"key": state_key}).first()
                previous = row[0] if row else None
                if isinstance(previous, str):
                    previous = json.loads(previous)
                if previous and previous.get("last_sent_at"):
                    last = datetime.fromisoformat(previous["last_sent_at"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=IST)
                    if now - last.astimezone(IST) < timedelta(minutes=cooldown):
                        return
                body = (
                    f"{'🚨' if record.levelno >= logging.CRITICAL else '⚠️'} SYSTEM {record.levelname}\n"
                    f"Component: {record.name}\n"
                    f"Message: {message}\n"
                    f"Time: {now.strftime('%d %b %Y, %I:%M:%S %p IST')}"
                )
                enqueue_notification(
                    event_type="SYSTEM_ALERT", category="SYSTEM",
                    source="PYTHON_LOGGING", entity_type="SYSTEM",
                    entity_id=fingerprint, payload={"telegram_text": body},
                    deduplication_key=f"system-alert:{fingerprint}:{int(now.timestamp()) // (cooldown * 60)}",
                    occurred_at=now,
                    connection=connection,
                )
                connection.execute(text("""
                    INSERT INTO notification_rule_state (state_key,state_json)
                    VALUES (:key,CAST(:state AS JSON))
                    ON DUPLICATE KEY UPDATE state_json=VALUES(state_json)
                """), {"key": state_key, "state": json.dumps({"last_sent_at": now.isoformat()})})
        except (SQLAlchemyError, TypeError, ValueError, OverflowError, KeyError):
            # Never recurse through logging from a logging handler.
            self.handleError(record)


def install_system_alert_handler() -> None:
    root = logging.getLogger()
    if any(isinstance(handler, TelegramSystemAlertHandler) for handler in root.handlers):
        return
    handler = TelegramSystemAlertHandler()
    handler.setLevel(logging.ERROR)
    root.addHandler(handler)
