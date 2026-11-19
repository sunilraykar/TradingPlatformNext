import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_csrf_token() -> str:
    secret = get_settings().app_secret_key
    if len(secret) < 32:
        raise RuntimeError("APP_SECRET_KEY must contain at least 32 characters")
    nonce = secrets.token_urlsafe(32)
    signature = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def verify_csrf_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    secret = get_settings().app_secret_key
    if len(secret) < 32:
        return False
    nonce, supplied = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return bool(nonce) and hmac.compare_digest(supplied, expected)


def get_session_user(raw_token: str | None) -> dict[str, Any] | None:
    if not raw_token:
        return None
    settings = get_settings()
    now = utcnow()
    with get_engine().begin() as connection:
        row = connection.execute(
            text("""
                SELECT u.user_id, u.email, u.role, u.status, s.created_at,
                       s.last_seen_at, s.expires_at, s.revoked_at
                FROM app_user_sessions s
                JOIN app_users u ON u.user_id = s.user_id
                WHERE s.token_hash = :token_hash
                LIMIT 1
            """),
            {"token_hash": hash_token(raw_token)},
        ).mappings().first()
        if not row or row["status"] != "ACTIVE" or row["revoked_at"] is not None:
            return None
        if row["expires_at"] <= now:
            return None
        if row["last_seen_at"] < now - timedelta(minutes=settings.session_idle_timeout_minutes):
            connection.execute(
                text("UPDATE app_user_sessions SET revoked_at = :now WHERE token_hash = :token_hash"),
                {"now": now, "token_hash": hash_token(raw_token)},
            )
            return None
        if row["last_seen_at"] <= now - timedelta(minutes=5):
            connection.execute(
                text("UPDATE app_user_sessions SET last_seen_at = :now WHERE token_hash = :token_hash"),
                {"now": now, "token_hash": hash_token(raw_token)},
            )
        return {
            "user_id": str(row["user_id"]),
            "email": row["email"],
            "role": row["role"],
            "status": row["status"],
        }


def get_websocket_user(cookies: dict[str, str]) -> dict[str, Any] | None:
    cookie_name = get_settings().session_cookie_name
    return get_session_user(cookies.get(cookie_name))
