"""Kite-only outbound HTTP proxy configuration."""
from __future__ import annotations

from urllib.parse import quote

from app.core.config import get_settings


def get_proxy_url() -> str | None:
    """Build the authenticated StaticIP proxy URL for Kite REST requests only."""
    settings = get_settings()
    if not settings.outbound_proxy_enabled:
        return None

    host = settings.outbound_proxy_host.strip()
    username = settings.outbound_proxy_username
    password = settings.outbound_proxy_password
    port = settings.outbound_proxy_port
    scheme = settings.outbound_proxy_scheme.strip().lower()

    if not host:
        raise RuntimeError("OUTBOUND_PROXY_HOST is required when the Kite proxy is enabled.")
    if not username or not password:
        raise RuntimeError(
            "OUTBOUND_PROXY_USERNAME and OUTBOUND_PROXY_PASSWORD are required "
            "when the Kite proxy is enabled."
        )
    if scheme not in {"http", "https"}:
        raise RuntimeError("OUTBOUND_PROXY_SCHEME must be http or https.")
    if not 1 <= port <= 65535:
        raise RuntimeError("OUTBOUND_PROXY_PORT must be between 1 and 65535.")

    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"

    safe_user = quote(username, safe="")
    safe_password = quote(password, safe="")
    return f"{scheme}://{safe_user}:{safe_password}@{host}:{port}"


def get_proxy_map() -> dict[str, str]:
    """Return proxy mappings for KiteConnect REST calls; empty means direct."""
    proxy_url = get_proxy_url()
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


def proxy_status() -> dict[str, object]:
    """Expose safe Kite proxy diagnostics without returning credentials."""
    settings = get_settings()
    enabled = bool(settings.outbound_proxy_enabled)
    configured = bool(
        settings.outbound_proxy_host.strip()
        and settings.outbound_proxy_username
        and settings.outbound_proxy_password
    )
    return {
        "enabled": enabled,
        "configured": configured,
        "scheme": settings.outbound_proxy_scheme if enabled else None,
        "host": settings.outbound_proxy_host.strip() if enabled else None,
        "port": settings.outbound_proxy_port if enabled else None,
        "authentication_configured": configured,
        "kite_rest": enabled and configured,
        "kite_websocket": False,
        "other_providers": False,
        "firebase": False,
    }
