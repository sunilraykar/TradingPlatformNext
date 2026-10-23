from dataclasses import dataclass
from typing import Any

from kiteconnect import KiteConnect


class KiteProviderError(RuntimeError):
    """Safe provider-facing error that avoids exposing secrets."""


@dataclass(frozen=True)
class KiteSession:
    access_token: str
    user_id: str


class KiteProvider:
    """Thin Kite Connect adapter. No order-placement methods are exposed in milestone 1."""

    def __init__(self, api_key: str, access_token: str = "") -> None:
        if not api_key:
            raise ValueError("Kite API key is required")
        self._client = KiteConnect(api_key=api_key)
        if access_token:
            self._client.set_access_token(access_token)

    @staticmethod
    def login_url(api_key: str) -> str:
        return KiteConnect(api_key=api_key).login_url()

    @staticmethod
    def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> KiteSession:
        client = KiteConnect(api_key=api_key)
        try:
            data = client.generate_session(request_token, api_secret=api_secret)
        except Exception as exc:
            raise KiteProviderError("Kite session exchange failed") from exc
        token = data.get("access_token")
        user_id = data.get("user_id")
        if not token or not user_id:
            raise KiteProviderError("Kite returned an incomplete session")
        return KiteSession(access_token=token, user_id=user_id)

    def profile(self) -> dict[str, Any]:
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            return self._client.profile()
        except Exception as exc:
            raise KiteProviderError("Kite session validation failed") from exc

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            records = self._client.instruments(exchange) if exchange else self._client.instruments()
        except Exception as exc:
            raise KiteProviderError("Kite instrument master download failed") from exc
        return records

    def quote(self, instrument_token: int) -> dict[str, Any]:
        return self.quotes([instrument_token]).get(str(instrument_token), {})

    def quotes(self, instrument_tokens: list[int]) -> dict[str, Any]:
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        keys = [str(token) for token in instrument_tokens]
        try:
            return self._client.quote(*keys)
        except Exception as exc:
            raise KiteProviderError("Kite quote request failed") from exc
