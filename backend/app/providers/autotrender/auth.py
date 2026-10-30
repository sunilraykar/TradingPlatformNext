import json
import logging
import os
from pathlib import Path
from threading import RLock

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

logger = logging.getLogger(__name__)


def _safe_response_excerpt(response, limit: int = 400) -> str:
    """Return a bounded response excerpt with common credential/token fields redacted."""
    import re

    text = str(getattr(response, "text", "") or "").replace("\n", " ").replace("\r", " ")
    pattern = r"(?i)(authcookie|cookie|token|password|authorization|access_token|refresh_token)(\s*[:=]\s*)[^\s,;}]+"
    return re.sub(pattern, r"\1\2[REDACTED]", text)[:limit]
LOGIN_URL = "https://theautotrender.com/login"
API_BASE_URL = "https://api.theautotrender.com:5959"
SESSION_FILE = Path(__file__).resolve().parents[3] / "runtime" / "autotrender" / "session.json"


class AutoTrenderAuth:
    """Authenticate to AutoTrender via its login page and retain its authcookie."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.authenticated = False
        self._lock = RLock()

    def login(self) -> dict[str, object]:
        with self._lock:
            if self._restore_saved_cookie() and self._validate_session():
                return {"authenticated": True, "message": "Saved AutoTrender session restored."}

            self.clear_saved_cookie()
            username = os.getenv("AUTOTRENDER_USERNAME", "").strip()
            password = os.getenv("AUTOTRENDER_PASSWORD", "")
            if not username or not password:
                raise RuntimeError(
                    "Set AUTOTRENDER_USERNAME and AUTOTRENDER_PASSWORD in backend/.env."
                )

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = None
            try:
                logger.info("AutoTrender auth diagnostic: starting headless Chrome login")
                driver = webdriver.Chrome(options=options)
                driver.get(LOGIN_URL)
                wait = WebDriverWait(driver, 30)

                def page_ready(browser):
                    if "/dashboard" in browser.current_url:
                        return "dashboard"
                    if browser.find_elements(By.NAME, "username"):
                        return "login"
                    return False

                state = wait.until(page_ready)
                logger.info(
                    "AutoTrender auth diagnostic: login page ready; page_state=%s final_url=%s",
                    state,
                    driver.current_url,
                )
                if state == "login":
                    username_field = wait.until(
                        EC.presence_of_element_located((By.NAME, "username"))
                    )
                    password_field = wait.until(
                        EC.presence_of_element_located((By.NAME, "password"))
                    )
                    username_field.clear()
                    username_field.send_keys(username)
                    password_field.clear()
                    password_field.send_keys(password)
                    try:
                        terms = driver.find_element(By.NAME, "terms")
                        if not terms.is_selected():
                            driver.execute_script("arguments[0].click();", terms)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Optional terms checkbox interaction failed: %s", exc)
                    wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
                    ).click()
                    wait.until(lambda browser: "/login" not in browser.current_url)
                    logger.info(
                        "AutoTrender auth diagnostic: submit completed; final_url=%s",
                        driver.current_url,
                    )

                cookies = driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
                cookie = next((item for item in cookies if item.get("name") == "authcookie"), None)
                if not cookie:
                    raise RuntimeError(
                        "AutoTrender login did not provide authcookie. Check account access or CAPTCHA."
                    )

                logger.info(
                    "AutoTrender auth diagnostic: authcookie captured; domain=%s path=%s "
                    "secure=%s http_only=%s expiry_present=%s",
                    cookie.get("domain"),
                    cookie.get("path", "/"),
                    cookie.get("secure"),
                    cookie.get("httpOnly"),
                    bool(cookie.get("expires")),
                )
                self.session.cookies.clear()
                self.session.cookies.set(
                    "authcookie",
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )
                self._save_cookie(cookie["value"])
                self.authenticated = True
                if not self._validate_session():
                    self.clear_saved_cookie()
                    raise RuntimeError(
                        "AutoTrender session validation failed using /api/market-summary. "
                        "Check backend logs for safe validation diagnostics."
                    )
                return {"authenticated": True, "message": "AutoTrender login successful."}
            except Exception as exc:
                self.authenticated = False
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"AutoTrender login failed: {type(exc).__name__}: {exc}") from exc
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Browser cleanup failed: %s", exc)

    @staticmethod
    def _response_rejects_session(response) -> bool:
        """Identify explicit upstream authentication failures, including JSON errors."""
        if response.status_code in (401, 403, 419, 440):
            return True

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            return True

        # AutoTrender can return an application-level rejection in a successful
        # HTTP response. Do not mistake that payload for an authenticated session.
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            message = " ".join(
                str(payload.get(key, "")) for key in ("message", "detail", "error", "status")
            ).lower()
            rejection_terms = (
                "session cookie", "invalid session", "session expired",
                "unauthorized", "not authenticated", "authentication required",
                "login required", "invalid token",
            )
            if any(term in message for term in rejection_terms):
                return True

        response_url = str(getattr(response, "url", "")).lower()
        return "/login" in response_url

    def get(self, endpoint: str, **kwargs):
        """Fetch an API endpoint, clearing rejected cookies and retrying login once."""
        url = endpoint if endpoint.startswith("http") else API_BASE_URL + endpoint

        # Serialize authentication and requests. Concurrent API calls must not
        # race to restore, delete, or replace the shared captured session cookie.
        with self._lock:
            if not self.authenticated:
                restored = self._restore_saved_cookie()
                if not restored or not self._validate_session():
                    # A cookie that failed validation must never be left on disk.
                    self.clear_saved_cookie()
                    logger.info("AutoTrender session unavailable; attempting automatic login")
                    self.login()

            response = self.session.get(url, timeout=30, **kwargs)
            if self._response_rejects_session(response):
                logger.warning("AutoTrender rejected the session; clearing cookie and logging in again")
                self.clear_saved_cookie()
                self.login()
                response = self.session.get(url, timeout=30, **kwargs)
                if self._response_rejects_session(response):
                    self.clear_saved_cookie()
                    raise RuntimeError(
                        "AutoTrender rejected the new session after re-login. "
                        "The captured session cookie was cleared; check AutoTrender credentials or account access."
                    )

            return response

    def is_authenticated(self) -> bool:
        return bool(self.authenticated and self._validate_session())

    def _validate_session(self) -> bool:
        if not self.authenticated and not self.session.cookies.get("authcookie"):
            return False
        try:
            response = self.session.get(
                API_BASE_URL + "/api/market-summary",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://theautotrender.com",
                    "Referer": "https://theautotrender.com/",
                },
                timeout=15,
            )
            rejected = self._response_rejects_session(response)
            self.authenticated = response.ok and not rejected
            logger.info(
                "AutoTrender auth diagnostic: API validation status=%s content_type=%s "
                "final_url=%s rejected=%s response_excerpt=%r",
                response.status_code,
                response.headers.get("Content-Type", ""),
                getattr(response, "url", ""),
                rejected,
                _safe_response_excerpt(response),
            )
            if not self.authenticated and rejected:
                logger.warning("AutoTrender rejected the saved session cookie; clearing it")
                self.clear_saved_cookie()
        except requests.RequestException:
            self.authenticated = False
        return self.authenticated

    def _restore_saved_cookie(self) -> bool:
        try:
            value = json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("authcookie")
            if not value:
                return False
            self.session.cookies.clear()
            self.session.cookies.set("authcookie", value)
            self.authenticated = True
            return True
        except (OSError, ValueError, AttributeError):
            self.authenticated = False
            return False

    def _save_cookie(self, value: str) -> None:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"authcookie": value}), encoding="utf-8")

    def clear_saved_cookie(self) -> None:
        self.authenticated = False
        self.session.cookies.clear()
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def shutdown(self) -> None:
        self.authenticated = False
        self.session.close()
