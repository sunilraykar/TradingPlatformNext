from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests

from app.providers.apiplatform.auth import (
    API_BASE_URL,
    APIPLATFORMAuth,
    APIPLATFORMAuthError,
)


class APIPLATFORMClientError(RuntimeError):
    """Raised when an APIPLATFORM API request fails."""


class APIPLATFORMClient:
    """Authenticated APIPLATFORM API client sharing one auth/session instance."""

    def __init__(
        self,
        auth: APIPLATFORMAuth,
        base_url: str = API_BASE_URL,
        timeout: int = 30,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        url = endpoint if endpoint.startswith(("http://", "https://")) else urljoin(
            self.base_url, endpoint.lstrip("/")
        )
        try:
            self.auth.ensure_authenticated()
            response = self.auth.session.request(
                method.upper(),
                url,
                timeout=kwargs.pop("timeout", self.timeout),
                **kwargs,
            )
            if response.status_code in (401, 403):
                previous_token = self.auth.token
                self.auth.invalidate(expected_token=previous_token)
                self.auth.login()
                response = self.auth.session.request(
                    method.upper(),
                    url,
                    timeout=kwargs.pop("timeout", self.timeout),
                    **kwargs,
                )
            response.raise_for_status()
            return response
        except APIPLATFORMAuthError:
            raise
        except requests.RequestException as exc:
            failed_response = getattr(exc, "response", None)
            status = getattr(failed_response, "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            diagnostics: list[str] = []
            if failed_response is not None:
                content_type = failed_response.headers.get("Content-Type")
                server = failed_response.headers.get("Server")
                request_id = (
                    failed_response.headers.get("CF-Ray")
                    or failed_response.headers.get("X-Request-ID")
                )
                if content_type:
                    diagnostics.append(f"content-type={content_type}")
                if server:
                    diagnostics.append(f"server={server}")
                if request_id:
                    diagnostics.append(f"request-id={request_id}")
                body = " ".join((failed_response.text or "").split())
                if body:
                    diagnostics.append(f"response={body[:800]}")
            detail = f" {'; '.join(diagnostics)}" if diagnostics else ""
            raise APIPLATFORMClientError(
                f"APIPLATFORM request failed{suffix}: {type(exc).__name__}.{detail}"
            ) from exc

    def get(self, endpoint: str, **kwargs: Any) -> Any:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs: Any) -> Any:
        return self.request("POST", endpoint, **kwargs)

    def fetch_option_chain(
        self,
        symbol: str,
        expiry: str,
        lot_size: int,
    ) -> dict[str, Any]:
        response = self.get(
            "/optionChain/fetch-data",
            params={"symbol": symbol, "expiry": expiry, "lotSize": lot_size},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIPLATFORMClientError(
                "APIPLATFORM option-chain endpoint returned a non-JSON response."
            ) from exc
        if not isinstance(payload, dict):
            raise APIPLATFORMClientError(
                "APIPLATFORM option-chain endpoint returned an unexpected payload."
            )
        return payload
