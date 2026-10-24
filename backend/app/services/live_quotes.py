from datetime import UTC, datetime
from threading import RLock
from typing import Any, Callable


class LiveQuoteCache:
    """Thread-safe in-memory cache; intentionally does not write to a database."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._quotes: dict[int, dict[str, Any]] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def update(self, instrument_token: int, payload: dict[str, Any]) -> None:
        record = dict(payload)
        record["instrument_token"] = instrument_token
        record["received_at"] = datetime.now(UTC).isoformat()
        with self._lock:
            self._quotes[instrument_token] = record

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:\n        with self._lock:\n            if listener not in self._listeners:\n                self._listeners.append(listener)\n\n    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:\n        with self._lock:\n            if listener in self._listeners:\n                self._listeners.remove(listener)\n\n    def get(self, instrument_token: int) -> dict[str, Any] | None:
        with self._lock:
            value = self._quotes.get(instrument_token)
            return dict(value) if value else None

    def snapshot(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._quotes.items()}

    def clear(self) -> None:
        """Remove cached quotes when the active provider session is reset."""
        with self._lock:
            self._quotes.clear()


live_quotes = LiveQuoteCache()
