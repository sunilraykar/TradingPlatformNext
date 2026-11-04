"""Durable notification event storage and transactional queue primitives."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported notification payload value: {type(value).__name__}")


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=_json_default)


def ensure_notification_schema() -> None:
    """Create notification tables idempotently; safe to run on every startup."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS notification_events (
            event_id CHAR(36) NOT NULL PRIMARY KEY,
            event_type VARCHAR(80) NOT NULL,
            category VARCHAR(40) NOT NULL,
            source VARCHAR(80) NOT NULL,
            entity_type VARCHAR(40) NULL,
            entity_id VARCHAR(128) NULL,
            account_id VARCHAR(128) NULL,
            occurred_at DATETIME(6) NOT NULL,
            schema_version SMALLINT UNSIGNED NOT NULL DEFAULT 1,
            deduplication_key VARCHAR(191) NOT NULL,
            payload JSON NOT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_notification_event_dedupe (deduplication_key),
            KEY ix_notification_events_type_time (event_type, occurred_at),
            KEY ix_notification_events_entity (entity_type, entity_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS notification_queue (
            queue_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            event_id CHAR(36) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
            next_attempt_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            locked_at DATETIME(6) NULL,
            last_error TEXT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_notification_queue_event (event_id),
            KEY ix_notification_queue_ready (status, next_attempt_at, queue_id),
            CONSTRAINT fk_notification_queue_event
                FOREIGN KEY (event_id) REFERENCES notification_events(event_id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS notification_delivery_log (
            delivery_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            queue_id BIGINT UNSIGNED NOT NULL,
            attempt_number TINYINT UNSIGNED NOT NULL,
            attempted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            outcome VARCHAR(20) NOT NULL,
            http_status SMALLINT UNSIGNED NULL,
            telegram_message_id BIGINT NULL,
            error_text TEXT NULL,
            response_payload JSON NULL,
            KEY ix_notification_delivery_queue (queue_id, attempted_at),
            CONSTRAINT fk_notification_delivery_queue
                FOREIGN KEY (queue_id) REFERENCES notification_queue(queue_id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS notification_rule_state (
            state_key VARCHAR(191) NOT NULL PRIMARY KEY,
            state_json JSON NOT NULL,
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def enqueue_notification(
    *,
    event_type: str,
    category: str,
    source: str,
    payload: dict[str, Any],
    deduplication_key: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    account_id: str | None = None,
    occurred_at: datetime | str | None = None,
    schema_version: int = 1,
) -> dict[str, Any]:
    """Persist an immutable event and its queue row in one MySQL transaction."""
    if not deduplication_key or len(deduplication_key) > 191:
        raise ValueError("deduplication_key must contain 1 to 191 characters")
    if occurred_at is None:
        event_time = datetime.now(UTC).replace(tzinfo=None)
    elif isinstance(occurred_at, str):
        parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        event_time = parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    else:
        event_time = occurred_at.astimezone(UTC).replace(tzinfo=None) if occurred_at.tzinfo else occurred_at

    candidate_id = str(uuid.uuid4())
    with get_engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO notification_events
              (event_id, event_type, category, source, entity_type, entity_id, account_id,
               occurred_at, schema_version, deduplication_key, payload)
            VALUES
              (:event_id, :event_type, :category, :source, :entity_type, :entity_id, :account_id,
               :occurred_at, :schema_version, :deduplication_key, CAST(:payload AS JSON))
            ON DUPLICATE KEY UPDATE event_id = event_id
        """), {
            "event_id": candidate_id,
            "event_type": event_type,
            "category": category,
            "source": source,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "account_id": account_id,
            "occurred_at": event_time,
            "schema_version": schema_version,
            "deduplication_key": deduplication_key,
            "payload": _dump(payload),
        })
        row = connection.execute(text("""
            SELECT event_id FROM notification_events WHERE deduplication_key = :dedupe
        """), {"dedupe": deduplication_key}).first()
        event_id = str(row[0])
        result = connection.execute(text("""
            INSERT INTO notification_queue (event_id, status, next_attempt_at)
            VALUES (:event_id, 'PENDING', UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE queue_id = LAST_INSERT_ID(queue_id)
        """), {"event_id": event_id})
        queued = result.rowcount == 1
    return {"event_id": event_id, "queued": queued}


def get_rule_state(state_key: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(text("""
            SELECT state_json FROM notification_rule_state WHERE state_key = :state_key
        """), {"state_key": state_key}).first()
    if not row:
        return None
    value = row[0]
    return json.loads(value) if isinstance(value, str) else value


def set_rule_state(state_key: str, state: dict[str, Any]) -> None:
    with get_engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO notification_rule_state (state_key, state_json)
            VALUES (:state_key, CAST(:state_json AS JSON))
            ON DUPLICATE KEY UPDATE state_json = VALUES(state_json)
        """), {"state_key": state_key, "state_json": _dump(state)})


def notification_status() -> dict[str, Any]:
    with get_engine().connect() as connection:
        rows = connection.execute(text("""
            SELECT status, COUNT(*) AS total
            FROM notification_queue GROUP BY status
        """)).mappings().all()
        total_events = connection.execute(text(
            "SELECT COUNT(*) FROM notification_events"
        )).scalar_one()
        last_delivery = connection.execute(text(
            "SELECT MAX(attempted_at) FROM notification_delivery_log"
        )).scalar_one()
    return {
        "events_total": int(total_events or 0),
        "queue": {str(row["status"]): int(row["total"]) for row in rows},
        "last_delivery_at": last_delivery.isoformat() if last_delivery else None,
    }
