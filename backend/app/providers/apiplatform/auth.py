from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SITE_URL = "https://nseoptionchain.ltpcalculator.com/"
LOGIN_URL = "https://apiplatform-auth.ltpcalculator.com/login"
SESSION_FILE = Path(__file__).resolve().parents[3] / "runtime" / "apiplatform" / "session.json"
API_BASE_URL = "https://apiplatform.ltpcalculator.com"
TOKEN_SAFETY_MARGIN_SECONDS = 300


class APIPLATFORMAuthError(RuntimeError):
    """Raised when APIPLATFORM authentication cannot be established."""


def _decode_jwt_expiry(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        expiry = claims.get("exp")
        return float(expiry) if expiry is not None else None
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def _find_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("token", "access_token", "accessToken", "jwt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        return _find_token(data)
    return None


class APIPLATFORMAuth:
    """Own the shared HTTP session and refreshable APIPLATFORM JWT.

    Cloudflare cookies are collected from the official option-chain website in a
    short-lived Selenium browser session. Basic Auth and JWT values never leave
    the backend process or its private runtime session file.
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "dnt": "1",
                "origin": "https://nseoptionchain.ltpcalculator.com",
                "referer": SITE_URL,
            }
        )
        self.token: str | None = None
        self.expires_at: float | None = None
        self.user_agent: str | None = None
        self._lock = RLock()
        self._load_persisted_session()

    def _load_persisted_session(self) -> bool:
        try:
            saved = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            token = saved.get("token")
            expires_at = float(saved.get("expires_at", 0))
            if not token or expires_at <= time.time() + TOKEN_SAFETY_MARGIN_SECONDS:
                return False
            self.token = str(token)
            self.expires_at = expires_at
            self.session.headers["Authorization"] = f"Bearer {self.token}"
            logger.info("Restored persisted APIPLATFORM JWT session")
            return True
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def _save_session(self) -> None:
        if not self.token or not self.expires_at:
            return
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"token": self.token, "expires_at": self.expires_at},
            separators=(",", ":"),
        )
        fd, temp_name = tempfile.mkstemp(
            prefix="session-", suffix=".tmp", dir=SESSION_FILE.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                # Windows does not expose POSIX file modes.
                pass
            os.replace(temp_name, SESSION_FILE)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _apply_browser_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.session.cookies.clear()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            kwargs: dict[str, Any] = {"path": cookie.get("path") or "/"}
            if cookie.get("domain"):
                kwargs["domain"] = cookie["domain"]
            if cookie.get("secure") is not None:
                kwargs["secure"] = bool(cookie["secure"])
            self.session.cookies.set(name, value, **kwargs)

    def _browser_cookies(self) -> tuple[list[dict[str, Any]], str]:
        options = Options()
        if get_settings().apiplatform_headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        if os.name != "nt":
            options.add_argument("--no-sandbox")

        driver = None
        try:
            logger.info("Starting browser session for APIPLATFORM website cookies")
            driver = webdriver.Chrome(options=options)
            driver.get(SITE_URL)
            WebDriverWait(driver, 30).until(
                lambda browser: browser.execute_script("return document.readyState") == "complete"
            )
            # Allow the site and its edge security layer to finish setting cookies.
            time.sleep(5)
            cookies = driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
            user_agent = driver.execute_script("return navigator.userAgent")
            if not cookies:
                raise APIPLATFORMAuthError(
                    "The option-chain website did not provide browser cookies. "
                    "Check site availability or complete any browser verification."
                )
            return cookies, str(user_agent or "")
        except APIPLATFORMAuthError:
            raise
        except Exception as exc:
            raise APIPLATFORMAuthError(
                f"Could not establish the APIPLATFORM website browser session ({type(exc).__name__})."
            ) from exc
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.debug("Could not close the APIPLATFORM browser cleanly", exc_info=True)

    def login(self, force: bool = False) -> dict[str, object]:
        with self._lock:
            if (
                not force
                and self.token
                and self.expires_at
                and self.expires_at > time.time() + TOKEN_SAFETY_MARGIN_SECONDS
            ):
                return {"authenticated": True, "message": "Existing APIPLATFORM session is active."}
            if not force and self._load_persisted_session():
                return {"authenticated": True, "message": "Saved APIPLATFORM session restored."}

            basic_auth = get_settings().apiplatform_basic_auth.strip()
            if not basic_auth:
                raise APIPLATFORMAuthError(
                    "Set APIPLATFORM_BASIC_AUTH in backend/.env. Store the complete "
                    "Basic authorization value there; do not put it in frontend code."
                )

            cookies, user_agent = self._browser_cookies()
            self._apply_browser_cookies(cookies)
            self.user_agent = user_agent
            if user_agent:
                # Reuse the browser identity for subsequent APIPLATFORM API calls,
                # not only for the initial Basic-auth login request.
                self.session.headers["User-Agent"] = user_agent
            headers = {
                "accept": "application/json, text/plain, */*",
                "authorization": basic_auth,
                "content-type": "application/json",
                "origin": "https://nseoptionchain.ltpcalculator.com",
                "referer": SITE_URL,
            }
            if user_agent:
                headers["user-agent"] = user_agent

            try:
                response = self.session.post(
                    LOGIN_URL,
                    headers=headers,
                    json={},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:
                raise APIPLATFORMAuthError(
                    f"APIPLATFORM login request failed ({type(exc).__name__})."
                ) from exc
            except ValueError as exc:
                raise APIPLATFORMAuthError(
                    "APIPLATFORM login returned a non-JSON response."
                ) from exc

            token = _find_token(payload)
            if not token:
                raise APIPLATFORMAuthError(
                    "APIPLATFORM login response did not contain a recognized JWT field."
                )

            expiry = _decode_jwt_expiry(token)
            if expiry is None and isinstance(payload, dict):
                data = payload.get("data")
                expires_in = payload.get("expires_in")
                if expires_in is None and isinstance(data, dict):
                    expires_in = data.get("expires_in")
                try:
                    expiry = time.time() + max(60, int(expires_in))
                except (TypeError, ValueError):
                    # The provider response may omit expiry metadata. Use a
                    # conservative one-hour cache and let a 401 trigger refresh.
                    expiry = time.time() + 3600

            self.token = token
            self.expires_at = expiry
            self.session.headers.update(
                {
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                    "priority": "u=1, i",
                }
            )
            if user_agent:
                chrome_version = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent)
                if chrome_version:
                    version = chrome_version.group(1)
                    self.session.headers["sec-ch-ua"] = (
                        f'"Not:A-Brand";v="8", "Chromium";v="{version}", '
                        f'"Google Chrome";v="{version}"'
                    )
                if "Windows NT" in user_agent:
                    self.session.headers["sec-ch-ua-platform"] = '"Windows"'
                elif "Macintosh" in user_agent:
                    self.session.headers["sec-ch-ua-platform"] = '"macOS"'
                elif "Linux" in user_agent:
                    self.session.headers["sec-ch-ua-platform"] = '"Linux"'
                self.session.headers["sec-ch-ua-mobile"] = "?0"
            self.session.headers["Authorization"] = f"Bearer {token}"
            self._save_session()
            logger.info("APIPLATFORM login succeeded; JWT session persisted")
            return {"authenticated": True, "message": "APIPLATFORM login successful."}

    def ensure_authenticated(self) -> None:
        if (
            self.token
            and self.expires_at
            and self.expires_at > time.time() + TOKEN_SAFETY_MARGIN_SECONDS
        ):
            return
        self.login()

    def invalidate(self, expected_token: str | None = None) -> None:
        with self._lock:
            if expected_token and self.token != expected_token:
                return
            self.token = None
            self.expires_at = None
            self.session.headers.pop("Authorization", None)
            try:
                SESSION_FILE.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove expired APIPLATFORM session file")

    def status(self) -> dict[str, object]:
        active = bool(
            self.token
            and self.expires_at
            and self.expires_at > time.time() + TOKEN_SAFETY_MARGIN_SECONDS
        )
        return {
            "authenticated": active,
            "expires_at": self.expires_at,
            "session_source": "persisted_or_runtime" if active else None,
        }

    def shutdown(self) -> None:
        self.session.close()
