"""Process-local cache for AutoTrender market data; deliberately no database writes."""

from __future__ import annotations

import os
import time
from threading import RLock
from typing import Any

from app.providers.autotrender.auth import AutoTrenderAuth

_DEFAULT_TTL_SECONDS = 2.0


class AutoTrenderCache:
    """Cache the full Index Price response (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, etc.)."""

    def __init__(self, auth: AutoTrenderAuth, ttl_seconds: float | None = None) -> None:
        self._auth = auth
        self._ttl_seconds = max(
            0.1,
            ttl_seconds
            if ttl_seconds is not None
            else float(os.getenv("AUTOTRENDER_INDEX_PRICE_CACHE_TTL_SECONDS", _DEFAULT_TTL_SECONDS)),
        )
        self._lock = RLock()
        self._payload: dict[str, Any] | None = None
        self._fetched_at: float | None = None
        self._last_error: str | None = None

    def get_index_price(self) -> dict[str, Any]:
        """Return the entire upstream index set from memory, refreshing only after TTL."""
        with self._lock:
            now = time.time()
            if (
                self._payload is not None
                and self._fetched_at is not None
                and now - self._fetched_at < self._ttl_seconds
            ):
                return self._response(stale=False)

            try:
                response = self._auth.get("/api/index-price")
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" not in content_type:
                    raise RuntimeError("AutoTrender Index Price returned a non-JSON response")
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                    raise RuntimeError("AutoTrender Index Price returned an unexpected JSON structure")
                # Preserve the complete upstream payload; do not filter out index symbols.
                self._payload = payload
                self._fetched_at = time.time()
                self._last_error = None
                return self._response(stale=False)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                if self._payload is not None and self._fetched_at is not None:
                    return self._response(stale=True)
                raise RuntimeError("AutoTrender Index Price is unavailable and no cached data exists") from exc


    def viewer(self) -> dict[str, Any]:
        """Return a read-only, redacted view of the cached index-price payload."""
        import re

        def redact(value: Any) -> Any:
            if isinstance(value, dict):
                cleaned = {}
                for key, item in value.items():
                    if re.search(r"(token|secret|password|credential|authorization|cookie|api.?key)", str(key), re.I):
                        cleaned[key] = "[REDACTED]"
                    else:
                        cleaned[key] = redact(item)
                return cleaned
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, str) and re.search(r"(?i)(bearer\\s+|access_token=|refresh_token=)", value):
                return "[REDACTED]"
            return value

        with self._lock:
            age = None if self._fetched_at is None else round(max(0.0, time.time() - self._fetched_at), 3)
            payload = redact(self._payload) if self._payload is not None else None
            return {
                "read_only": True,
                "entries": [{
                    "key": "autotrend:index-price",
                    "source": "process memory",
                    "record_count": len(self._payload.get("data", {})) if isinstance(self._payload, dict) and isinstance(self._payload.get("data"), dict) else 0,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._fetched_at)) if self._fetched_at is not None else None,
                    "age_seconds": age,
                    "ttl_seconds": self._ttl_seconds,
                    "data": payload,
                }] if payload is not None else [],
                "cached": payload is not None,
                "last_refresh_error": self._last_error,
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            age = None if self._fetched_at is None else max(0.0, time.time() - self._fetched_at)
            return {
                "cached": self._payload is not None,
                "cache_ttl_seconds": self._ttl_seconds,
                "cache_age_seconds": round(age, 3) if age is not None else None,
                "last_refresh_error": self._last_error,
            }

    def _response(self, *, stale: bool) -> dict[str, Any]:
        assert self._payload is not None and self._fetched_at is not None
        age = max(0.0, time.time() - self._fetched_at)
        return {
            **self._payload,
            "cache": {
                "source": "memory",
                "stale": stale,
                "age_seconds": round(age, 3),
                "ttl_seconds": self._ttl_seconds,
                "last_refresh_error": self._last_error,
            },
        }
