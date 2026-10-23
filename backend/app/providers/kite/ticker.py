from collections.abc import Callable

from kiteconnect import KiteTicker

from app.services.live_quotes import LiveQuoteCache


class KiteTickerAdapter:
    """Owns a KiteTicker instance and forwards ticks into the in-memory cache."""

    def __init__(
        self,
        api_key: str,
        access_token: str,
        cache: LiveQuoteCache,
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        self._ticker = KiteTicker(api_key, access_token)
        self._cache = cache
        self._on_state = on_state or (lambda _: None)
        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error
        self._ticker.on_reconnect = self._on_reconnect

    def connect(self, threaded: bool = True) -> None:
        self._ticker.connect(threaded=threaded)

    def subscribe(self, instrument_tokens: list[int], mode: str = "quote") -> None:
        if not instrument_tokens:
            return
        self._ticker.subscribe(instrument_tokens)
        modes = {"ltp": self._ticker.MODE_LTP, "quote": self._ticker.MODE_QUOTE, "full": self._ticker.MODE_FULL}
        if mode not in modes:
            raise ValueError(f"Unsupported Kite ticker mode: {mode}")
        self._ticker.set_mode(modes[mode], instrument_tokens)

    def unsubscribe(self, instrument_tokens: list[int]) -> None:
        if instrument_tokens:
            self._ticker.unsubscribe(instrument_tokens)

    def close(self) -> None:
        self._ticker.close()

    def _on_ticks(self, _ws, ticks: list[dict]) -> None:
        for tick in ticks:
            token = tick.get("instrument_token")
            if token is not None:
                self._cache.update(int(token), tick)

    def _on_connect(self, _ws, _response) -> None:
        self._on_state("connected")

    def _on_close(self, _ws, code, reason) -> None:
        self._on_state(f"closed:{code}:{reason}")

    def _on_error(self, _ws, code, reason) -> None:
        self._on_state(f"error:{code}:{reason}")

    def _on_reconnect(self, _ws, attempts) -> None:
        self._on_state(f"reconnecting:{attempts}")
