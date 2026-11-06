"""Central outbound HTTP proxy configuration for broker/provider REST traffic."""
from __future__ import annotations

import os
from urllib.parse import quote

from app.core.config import get_settings


def get_proxy_url() -> str | None:
    """Build the authenticated StaticIP proxy URL from backend environment settings."""
    settings = get_settings()
    if not settings.outbound_proxy_enabled:
        return None

    host = settings.outbound_proxy_host.strip()
    username = settings.outbound_proxy_username
    password = settings.outbound_proxy_password
    port = settings.outbound_proxy_port
    scheme = settings.outbound_proxy_scheme.strip().lower()

    if not host:
        raise RuntimeError("OUTBOUND_PROXY_HOST is required when outbound proxy is enabled.")
    if not username or not password:
        raise RuntimeError(
            "OUTBOUND_PROXY_USERNAME and OUTBOUND_PROXY_PASSWORD are required "
            "when outbound proxy is enabled."
        )
    if scheme not in {"http", "https"}:
        raise RuntimeError("OUTBOUND_PROXY_SCHEME must be http or https.")
    if not 1 <= port <= 65535:
        raise RuntimeError("OUTBOUND_PROXY_PORT must be between 1 and 65535.")

    # IPv6 literals must be bracketed when embedded in a URL authority.
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"

    safe_user = quote(username, safe="")
    safe_password = quote(password, safe="")
    return f"{scheme}://{safe_user}:{safe_password}@{host}:{port}"


def get_proxy_map() -> dict[str, str]:
    """Return Requests/KiteConnect-compatible proxy mappings; empty means direct."""
    proxy_url = get_proxy_url()
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


def configure_process_proxy() -> bool:
    """Configure Requests-based provider sessions before they are created."""
    proxy_url = get_proxy_url()
    if not proxy_url:
        return False

    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        os.environ[name] = proxy_url

    # Keep local frontend/backend and loopback service calls off the external proxy.
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    bypass = [item.strip() for item in existing.split(",") if item.strip()]
    for host in ("localhost", "127.0.0.1", "::1"):
        if host not in bypass:
            bypass.append(host)
    no_proxy = ",".join(bypass)
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy
    return True


def proxy_status() -> dict[str, object]:
    """Expose safe proxy diagnostics without returning credentials or the full URL."""
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
        "providers": {
            "kite_rest": enabled and configured,
            "apiplatform_rest": enabled and configured,
            "autotrender_rest": enabled and configured,
            "kite_websocket": False,
            "firebase": False,
        },
    }
