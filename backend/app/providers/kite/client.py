from dataclasses import dataclass
import logging
from typing import Any

from kiteconnect import KiteConnect


logger = logging.getLogger(__name__)


class KiteProviderError(RuntimeError):
    """Provider-facing error with a safe, actionable message."""


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

    def quotes(self, instruments: list[str | int]) -> dict[str, Any]:
        """Fetch quotes by Kite instrument token or EXCHANGE:TRADINGSYMBOL key."""
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        keys = [str(instrument) for instrument in instruments]
        try:
            return self._client.quote(*keys)
        except Exception as exc:
            raise KiteProviderError("Kite quote request failed") from exc

    def _account_call(self, operation: str, *args, **kwargs):
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            return getattr(self._client, operation)(*args, **kwargs)
        except Exception as exc:
            # Keep the full traceback in backend logs and surface Kite's rejection
            # message to the UI. Never log request headers, API keys or access tokens.
            logger.exception("Kite %s request failed", operation)
            detail = str(exc).strip() or exc.__class__.__name__
            raise KiteProviderError(
                f"Kite {operation.replace('_', ' ')} request failed: {detail}"
            ) from exc

    def margins(self, segment: str | None = None) -> dict[str, Any]:
        return self._account_call("margins", segment) if segment else self._account_call("margins")

    def positions(self) -> dict[str, Any]:
        return self._account_call("positions")

    def orders(self) -> list[dict[str, Any]]:
        return self._account_call("orders")

    def holdings(self) -> list[dict[str, Any]]:
        return self._account_call("holdings")

    def trades(self) -> list[dict[str, Any]]:
        return self._account_call("trades")

    def place_order(self, **params) -> str:
        return self._account_call("place_order", variety="regular", **params)

    def place_gtt(self, **params) -> dict[str, Any]:
        return self._account_call("place_gtt", **params)

    def modify_order(self, order_id: str, variety: str = "regular", **params) -> str:
        return self._account_call("modify_order", variety=variety, order_id=order_id, **params)

    def cancel_order(self, order_id: str, variety: str = "regular") -> str:
        return self._account_call("cancel_order", variety=variety, order_id=order_id)
