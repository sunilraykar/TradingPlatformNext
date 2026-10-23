"""Encrypted persistence for daily Kite Connect sessions."""
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine

IST = ZoneInfo("Asia/Kolkata")


class KiteSessionStoreError(RuntimeError):
    """Safe error for Kite session persistence."""


def _cipher() -> Fernet:
    key = get_settings().kite_token_encryption_key
    if not key:
        raise KiteSessionStoreError(
            "KITE_TOKEN_ENCRYPTION_KEY must be configured before saving Kite sessions"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise KiteSessionStoreError("Kite token encryption key is invalid") from exc


def save_session(user_id: str, access_token: str) -> None:
    encrypted = _cipher().encrypt(access_token.encode("utf-8"))
    session_date = datetime.now(IST).date()
    try:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO provider_sessions
                        (provider_code, provider_user_id, access_token_encrypted,
                         session_date, status)
                    VALUES ('KITE', :user_id, :token, :session_date, 'ACTIVE')
                    ON DUPLICATE KEY UPDATE
                        access_token_encrypted = VALUES(access_token_encrypted),
                        status = 'ACTIVE',
                        updated_at = CURRENT_TIMESTAMP(6)
                    """
                ),
                {"user_id": user_id, "token": encrypted, "session_date": session_date},
            )
    except Exception as exc:
        raise KiteSessionStoreError("Could not save Kite session to MySQL") from exc


def load_session() -> tuple[str, str] | None:
    session_date = datetime.now(IST).date()
    try:
        with get_engine().connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT provider_user_id, access_token_encrypted
                    FROM provider_sessions
                    WHERE provider_code = 'KITE' AND session_date = :session_date
                      AND status = 'ACTIVE'
                    ORDER BY updated_at DESC LIMIT 1
                    """
                ),
                {"session_date": session_date},
            ).first()
    except Exception as exc:
        raise KiteSessionStoreError("Could not read Kite session from MySQL") from exc
    if row is None:
        return None
    try:
        token = _cipher().decrypt(bytes(row.access_token_encrypted)).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError, TypeError) as exc:
        raise KiteSessionStoreError("Saved Kite session could not be decrypted") from exc
    return str(row.provider_user_id), token


def reset_sessions() -> None:
    """Invalidate locally stored active Kite sessions; does not revoke them at Zerodha."""
    try:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "UPDATE provider_sessions SET status='EXPIRED' "
                    "WHERE provider_code='KITE' AND status='ACTIVE'"
                )
            )
    except Exception as exc:
        raise KiteSessionStoreError("Could not reset Kite sessions in MySQL") from exc
