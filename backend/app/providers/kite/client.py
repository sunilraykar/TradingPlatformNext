import logging
from dataclasses import dataclass
from typing import Any

from kiteconnect import KiteConnect
from kiteconnect.exceptions import InputException

from app.core.outbound_proxy import get_proxy_map

logger = logging.getLogger(__name__)


class KiteProviderError(RuntimeError):
    """Provider-facing error with a safe, actionable message."""


class KiteOrderRejectedError(KiteProviderError):
    """A definitive broker-side order rejection; submitting it again is unsafe."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = int(code or 400)


@dataclass(frozen=True)
class KiteSession:
    access_token: str
    user_id: str


def _is_authentication_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        "incorrect api_key", "incorrect api key", "access_token",
        "tokenexception", "invalid session", "session expired", "unauthorized",
    ))


def _provider_error(operation: str, exc: Exception) -> KiteProviderError:
    if _is_authentication_error(exc):
        from app.providers.kite.registry import clear_active_kite_session
        from app.services.live_market_data import invalidate_session
        invalidate_session()
        clear_active_kite_session()
    detail = str(exc).strip() or exc.__class__.__name__
    return KiteProviderError("Kite " + operation.replace("_", " ") + " request failed: " + detail)


class KiteProvider:
    """Thin Kite Connect adapter. REST calls use the configured outbound proxy."""

    def __init__(self, api_key: str, access_token: str = "") -> None:
        if not api_key:
            raise ValueError("Kite API key is required")
        self._client = KiteConnect(api_key=api_key, proxies=get_proxy_map())
        if access_token:
            self._client.set_access_token(access_token)

    @staticmethod
    def login_url(api_key: str) -> str:
        return KiteConnect(api_key=api_key).login_url()

    @staticmethod
    def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> KiteSession:
        client = KiteConnect(api_key=api_key, proxies=get_proxy_map())
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
            raise _provider_error("session validation", exc) from exc

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            records = self._client.instruments(exchange) if exchange else self._client.instruments()
        except Exception as exc:
            raise _provider_error("instrument master download", exc) from exc
        return records

    def historical_data(
        self, instrument_token: int, from_date, to_date, interval: str = "5minute",
        continuous: bool = False, oi: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch Kite historical candles through the authenticated REST provider."""
        return self._account_call(
            "historical_data", instrument_token, from_date, to_date, interval,
            continuous=continuous, oi=oi,
        )

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
            raise _provider_error("quote", exc) from exc

    def _account_call(self, operation: str, *args, **kwargs):
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            return getattr(self._client, operation)(*args, **kwargs)
        except Exception as exc:
            # Keep the full traceback in backend logs and surface Kite's rejection
            # message to the UI. Never log request headers, API keys or access tokens.
            logger.exception("Kite %s request failed", operation)
            raise _provider_error(operation, exc) from exc

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
        """Place an order and distinguish definite broker input rejections from transport failures."""
        if not self._client.access_token:
            raise KiteProviderError("Kite session is not configured")
        try:
            return self._client.place_order(variety="regular", **params)
        except InputException as exc:
            detail = str(exc).strip() or "Kite rejected the order parameters"
            logger.warning("Kite rejected order request: %s", detail)
            raise KiteOrderRejectedError(
                "Kite rejected the order: " + detail,
                code=getattr(exc, "code", 400),
            ) from exc
        except Exception as exc:
            logger.exception("Kite place_order request failed")
            raise _provider_error("place_order", exc) from exc

    def place_gtt(self, **params) -> dict[str, Any]:
        return self._account_call("place_gtt", **params)

    def modify_order(self, order_id: str, variety: str = "regular", **params) -> str:
        return self._account_call("modify_order", variety=variety, order_id=order_id, **params)

    def cancel_order(self, order_id: str, variety: str = "regular") -> str:
        return self._account_call("cancel_order", variety=variety, order_id=order_id)
