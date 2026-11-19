import secrets
import smtplib
import uuid
from datetime import timedelta
from email.message import EmailMessage
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.auth.security import create_csrf_token, hash_token, utcnow
from app.core.config import get_settings
from app.db.session import get_engine

router = APIRouter(prefix="/auth", tags=["application authentication"])
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


class EmailRequest(BaseModel):
    email: EmailStr


class Credentials(EmailRequest):
    password: str = Field(min_length=12, max_length=256)


class BootstrapRequest(Credentials):
    pass


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class PasswordTokenRequest(TokenRequest):
    password: str = Field(min_length=12, max_length=256)


class InviteRequest(EmailRequest):
    pass


class UserStatusRequest(BaseModel):
    status: Literal["ACTIVE", "DISABLED"]


def _public_user(row: dict) -> dict:
    return {"user_id": str(row["user_id"]), "email": row["email"], "role": row["role"], "status": row["status"]}


def _make_action_token(connection, user_id: str, purpose: str, ttl: timedelta) -> str:
    raw = secrets.token_urlsafe(40)
    connection.execute(
        text("""
            INSERT INTO app_auth_tokens (token_hash, user_id, purpose, expires_at)
            VALUES (:token_hash, :user_id, :purpose, :expires_at)
        """),
        {"token_hash": hash_token(raw), "user_id": user_id, "purpose": purpose, "expires_at": utcnow() + ttl},
    )
    return raw


def _send_action_email(email: str, subject: str, action_url: str, purpose: str) -> dict:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        if settings.app_env.lower() not in {"development", "test"}:
            raise HTTPException(status_code=503, detail="Email delivery is not configured")
        return {"delivery": "development", "action_url": action_url}
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(
        f"Use the following one-time link to {purpose}. This link expires automatically.\n\n{action_url}\n"
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to send email; check SMTP configuration") from exc
    return {"delivery": "email"}


def _action_url(parameter: str, token: str) -> str:
    return f"{get_settings().app_public_url.rstrip('/')}/?{parameter}={token}"


def _set_session(response: Response, raw_token: str, csrf: str) -> None:
    settings = get_settings()
    secure = settings.app_env.lower() not in {"development", "test"}
    response.set_cookie(
        settings.session_cookie_name, raw_token, httponly=True, secure=secure,
        samesite="lax", path="/", max_age=settings.session_absolute_timeout_days * 86400,
    )
    response.set_cookie(
        settings.csrf_cookie_name, csrf, httponly=False, secure=secure,
        samesite="lax", path="/", max_age=settings.session_absolute_timeout_days * 86400,
    )


def _create_session(connection, user_id: str, request: Request) -> tuple[str, str]:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    csrf = create_csrf_token()
    now = utcnow()
    connection.execute(
        text("""
            INSERT INTO app_user_sessions
                (token_hash, user_id, created_at, last_seen_at, expires_at, user_agent, ip_address)
            VALUES (:token_hash, :user_id, :now, :now, :expires_at, :user_agent, :ip_address)
        """),
        {
            "token_hash": hash_token(raw_token), "user_id": user_id, "now": now,
            "expires_at": now + timedelta(days=settings.session_absolute_timeout_days),
            "user_agent": request.headers.get("user-agent", "")[:512],
            "ip_address": request.client.host if request.client else None,
        },
    )
    return raw_token, csrf


@router.get("/csrf")
def csrf_token(response: Response) -> dict:
    settings = get_settings()
    token = create_csrf_token()
    secure = settings.app_env.lower() not in {"development", "test"}
    response.set_cookie(settings.csrf_cookie_name, token, httponly=False, secure=secure, samesite="lax", path="/")
    return {"csrf_token": token}


@router.get("/bootstrap/status")
def bootstrap_status() -> dict:
    with get_engine().connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM app_users")).scalar_one()
    return {"required": int(count) == 0, "configured": bool(get_settings().initial_super_admin_email)}


@router.post("/bootstrap")
def bootstrap_admin(body: BootstrapRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    expected_email = settings.initial_super_admin_email.strip().lower()
    if not expected_email or body.email.lower() != expected_email:
        raise HTTPException(status_code=403, detail="This email is not authorized for initial setup")
    engine = get_engine()
    user_id = str(uuid.uuid4())
    with engine.begin() as connection:
        lock = connection.execute(text("SELECT GET_LOCK('tradingplatform_initial_admin', 10)")).scalar()
        if lock != 1:
            raise HTTPException(status_code=503, detail="Unable to acquire initial setup lock")
        try:
            count = connection.execute(text("SELECT COUNT(*) FROM app_users")).scalar_one()
            if int(count):
                raise HTTPException(status_code=409, detail="Initial administrator is already configured")
            connection.execute(
                text("""
                    INSERT INTO app_users (user_id, email, password_hash, role, status)
                    VALUES (:user_id, :email, :password_hash, 'SUPER_ADMIN', 'PENDING_VERIFICATION')
                """),
                {"user_id": user_id, "email": expected_email, "password_hash": password_hasher.hash(body.password)},
            )
            token = _make_action_token(connection, user_id, "VERIFY_EMAIL", timedelta(hours=24))
            raw_url = _action_url("verify", token)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('tradingplatform_initial_admin')"))
    delivery = _send_action_email(expected_email, "Verify your TradingPlatform account", raw_url, "verify your email")
    return {"created": True, "verification": delivery}


@router.post("/verification/resend")
def resend_verification(body: EmailRequest) -> dict:
    email = body.email.lower()
    token = None
    with get_engine().begin() as connection:
        row = connection.execute(
            text("SELECT user_id FROM app_users WHERE email = :email AND status = 'PENDING_VERIFICATION' LIMIT 1"),
            {"email": email},
        ).first()
        if row:
            token = _make_action_token(connection, str(row[0]), "VERIFY_EMAIL", timedelta(hours=24))
    delivery = {}
    if token:
        delivery = _send_action_email(
            email, "Verify your TradingPlatform account", _action_url("verify", token), "verify your email"
        )
    return {"requested": True, **delivery}


@router.post("/verify-email")
def verify_email(body: TokenRequest) -> dict:
    now = utcnow()
    with get_engine().begin() as connection:
        row = connection.execute(
            text("""
                SELECT t.user_id, t.purpose, t.expires_at, t.consumed_at, u.email
                FROM app_auth_tokens t JOIN app_users u ON u.user_id = t.user_id
                WHERE t.token_hash = :token_hash LIMIT 1
            """),
            {"token_hash": hash_token(body.token)},
        ).mappings().first()
        if not row or row["purpose"] != "VERIFY_EMAIL" or row["consumed_at"] or row["expires_at"] <= now:
            raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
        connection.execute(
            text("UPDATE app_auth_tokens SET consumed_at = :now WHERE token_hash = :token_hash"),
            {"now": now, "token_hash": hash_token(body.token)},
        )
        connection.execute(
            text("UPDATE app_users SET status = 'ACTIVE', email_verified_at = :now WHERE user_id = :user_id"),
            {"now": now, "user_id": row["user_id"]},
        )
    return {"verified": True, "email": row["email"]}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response) -> dict:
    email = body.email.lower()
    identity = hash_token(f"{email}|{request.client.host if request.client else 'unknown'}")
    now = utcnow()
    raw_token = csrf = None
    user_row = None
    failure_status = None
    with get_engine().begin() as connection:
        attempt = connection.execute(
            text("SELECT failed_attempts, window_started_at, locked_until FROM app_auth_login_attempts WHERE identity_hash = :identity"),
            {"identity": identity},
        ).mappings().first()
        if attempt and attempt["locked_until"] and attempt["locked_until"] > now:
            failure_status = 429
        else:
            row = connection.execute(
                text("SELECT user_id, email, password_hash, role, status FROM app_users WHERE email = :email LIMIT 1"),
                {"email": email},
            ).mappings().first()
            valid = False
            if row and row["status"] == "ACTIVE" and row["password_hash"]:
                try:
                    valid = password_hasher.verify(row["password_hash"], body.password)
                except (VerifyMismatchError, InvalidHashError):
                    valid = False
            if not valid:
                failures = 1
                if attempt and now - attempt["window_started_at"] <= timedelta(minutes=15):
                    failures = int(attempt["failed_attempts"]) + 1
                locked_until = now + timedelta(minutes=15) if failures >= 8 else None
                connection.execute(
                    text("""
                        INSERT INTO app_auth_login_attempts (identity_hash, failed_attempts, window_started_at, locked_until)
                        VALUES (:identity, :failures, :now, :locked)
                        ON DUPLICATE KEY UPDATE failed_attempts = VALUES(failed_attempts),
                            window_started_at = VALUES(window_started_at), locked_until = VALUES(locked_until)
                    """),
                    {"identity": identity, "failures": failures, "now": now, "locked": locked_until},
                )
                failure_status = 401
            else:
                connection.execute(text("DELETE FROM app_auth_login_attempts WHERE identity_hash = :identity"), {"identity": identity})
                raw_token, csrf = _create_session(connection, str(row["user_id"]), request)
                user_row = dict(row)
    if failure_status == 429:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    if failure_status:
        raise HTTPException(status_code=401, detail="Email or password is incorrect, or the account is not active")
    _set_session(response, raw_token, csrf)
    return {"user": _public_user(user_row), "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        with get_engine().begin() as connection:
            connection.execute(
                text("UPDATE app_user_sessions SET revoked_at = :now WHERE token_hash = :token_hash AND revoked_at IS NULL"),
                {"now": utcnow(), "token_hash": hash_token(raw_token)},
            )
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return {"logged_out": True}


@router.get("/me")
def me(request: Request) -> dict:
    user = getattr(request.state, "application_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": user}


@router.post("/password/forgot")
def forgot_password(body: EmailRequest) -> dict:
    email = body.email.lower()
    with get_engine().begin() as connection:
        row = connection.execute(
            text("SELECT user_id FROM app_users WHERE email = :email AND status = 'ACTIVE' LIMIT 1"),
            {"email": email},
        ).first()
        if row:
            token = _make_action_token(connection, str(row[0]), "RESET_PASSWORD", timedelta(minutes=30))
    if row:
        delivery = _send_action_email(email, "Reset your TradingPlatform password", _action_url("reset", token), "reset your password")
        if delivery.get("delivery") == "development":
            return {"requested": True, **delivery}
    return {"requested": True}


@router.post("/password/reset")
def reset_password(body: PasswordTokenRequest) -> dict:
    now = utcnow()
    with get_engine().begin() as connection:
        row = connection.execute(
            text("""
                SELECT user_id FROM app_auth_tokens
                WHERE token_hash = :token_hash AND purpose = 'RESET_PASSWORD'
                  AND consumed_at IS NULL AND expires_at > :now LIMIT 1
            """),
            {"token_hash": hash_token(body.token), "now": now},
        ).first()
        if not row:
            raise HTTPException(status_code=400, detail="Password reset link is invalid or expired")
        connection.execute(
            text("UPDATE app_auth_tokens SET consumed_at = :now WHERE token_hash = :token_hash"),
            {"now": now, "token_hash": hash_token(body.token)},
        )
        connection.execute(
            text("UPDATE app_users SET password_hash = :password_hash WHERE user_id = :user_id"),
            {"password_hash": password_hasher.hash(body.password), "user_id": row[0]},
        )
        connection.execute(
            text("UPDATE app_user_sessions SET revoked_at = :now WHERE user_id = :user_id AND revoked_at IS NULL"),
            {"now": now, "user_id": row[0]},
        )
    return {"reset": True}


@router.post("/invitations/accept")
def accept_invitation(body: PasswordTokenRequest) -> dict:
    now = utcnow()
    with get_engine().begin() as connection:
        row = connection.execute(
            text("""
                SELECT user_id FROM app_auth_tokens
                WHERE token_hash = :token_hash AND purpose = 'INVITE'
                  AND consumed_at IS NULL AND expires_at > :now LIMIT 1
            """),
            {"token_hash": hash_token(body.token), "now": now},
        ).first()
        if not row:
            raise HTTPException(status_code=400, detail="Invitation link is invalid or expired")
        connection.execute(
            text("UPDATE app_auth_tokens SET consumed_at = :now WHERE token_hash = :token_hash"),
            {"now": now, "token_hash": hash_token(body.token)},
        )
        connection.execute(
            text("UPDATE app_users SET password_hash = :password_hash, status = 'ACTIVE', email_verified_at = :now WHERE user_id = :user_id"),
            {"password_hash": password_hasher.hash(body.password), "now": now, "user_id": row[0]},
        )
    return {"accepted": True}


@router.get("/sessions")
def list_sessions(request: Request) -> dict:
    actor = getattr(request.state, "application_user", None)
    if not actor:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_token = request.cookies.get(get_settings().session_cookie_name)
    current_hash = hash_token(current_token) if current_token else ""
    with get_engine().connect() as connection:
        rows = connection.execute(
            text("""
                SELECT token_hash, created_at, last_seen_at, expires_at, user_agent, ip_address,
                       revoked_at
                FROM app_user_sessions
                WHERE user_id = :user_id
                ORDER BY created_at DESC
            """),
            {"user_id": actor["user_id"]},
        ).mappings().all()
    return {"sessions": [
        {
            "session_id": row["token_hash"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "expires_at": row["expires_at"],
            "user_agent": row["user_agent"],
            "ip_address": row["ip_address"],
            "revoked": row["revoked_at"] is not None,
            "current": row["token_hash"] == current_hash,
        }
        for row in rows
    ]}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, request: Request) -> dict:
    actor = getattr(request.state, "application_user", None)
    if not actor:
        raise HTTPException(status_code=401, detail="Authentication required")
    with get_engine().begin() as connection:
        result = connection.execute(
            text("""
                UPDATE app_user_sessions SET revoked_at = :now
                WHERE token_hash = :session_id AND user_id = :user_id AND revoked_at IS NULL
            """),
            {"now": utcnow(), "session_id": session_id, "user_id": actor["user_id"]},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Active session not found")
    return {"revoked": True}


@router.get("/users")
def list_users(request: Request) -> dict:
    actor = getattr(request.state, "application_user", None)
    if not actor or actor["role"] != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    with get_engine().connect() as connection:
        rows = connection.execute(
            text("SELECT user_id, email, role, status, email_verified_at, created_at FROM app_users ORDER BY created_at")
        ).mappings().all()
    return {"users": [dict(row) for row in rows]}


@router.post("/users/invitations")
def invite_viewer(body: InviteRequest, request: Request) -> dict:
    actor = getattr(request.state, "application_user", None)
    if not actor or actor["role"] != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    email = body.email.lower()
    user_id = str(uuid.uuid4())
    with get_engine().begin() as connection:
        exists = connection.execute(text("SELECT user_id FROM app_users WHERE email = :email"), {"email": email}).first()
        if exists:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        connection.execute(
            text("INSERT INTO app_users (user_id, email, role, status) VALUES (:id, :email, 'VIEWER', 'PENDING_VERIFICATION')"),
            {"id": user_id, "email": email},
        )
        token = _make_action_token(connection, user_id, "INVITE", timedelta(days=2))
    delivery = _send_action_email(email, "You are invited to TradingPlatform", _action_url("invite", token), "accept your invitation")
    return {"invited": True, "email": email, **delivery}


@router.patch("/users/{user_id}/status")
def update_user_status(user_id: str, body: UserStatusRequest, request: Request) -> dict:
    actor = getattr(request.state, "application_user", None)
    if not actor or actor["role"] != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    if actor["user_id"] == user_id and body.status == "DISABLED":
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    with get_engine().begin() as connection:
        row = connection.execute(
            text("SELECT role, status FROM app_users WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if row["role"] == "SUPER_ADMIN" and body.status == "DISABLED":
            raise HTTPException(status_code=400, detail="The initial Super Admin cannot be disabled here")
        connection.execute(
            text("UPDATE app_users SET status = :status WHERE user_id = :user_id"),
            {"status": body.status, "user_id": user_id},
        )
        if body.status == "DISABLED":
            connection.execute(
                text("UPDATE app_user_sessions SET revoked_at = :now WHERE user_id = :user_id AND revoked_at IS NULL"),
                {"now": utcnow(), "user_id": user_id},
            )
    return {"updated": True, "user_id": user_id, "status": body.status}
