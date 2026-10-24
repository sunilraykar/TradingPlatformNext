import json
import os
from pathlib import Path
from threading import RLock

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

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
                    except (NoSuchElementException, WebDriverException):
                        pass
                    wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
                    ).click()
                    wait.until(lambda browser: "/login" not in browser.current_url)

                cookies = driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
                cookie = next((item for item in cookies if item.get("name") == "authcookie"), None)
                if not cookie:
                    raise RuntimeError(
                        "AutoTrender login did not provide authcookie. Check account access or CAPTCHA."
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
                    raise RuntimeError("AutoTrender rejected the captured session cookie.")
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
                    except WebDriverException:
                        pass

    def get(self, endpoint: str, **kwargs):
        if not self.authenticated and (not self._restore_saved_cookie() or not self._validate_session()):
            raise RuntimeError("AutoTrender is not authenticated.")
        url = endpoint if endpoint.startswith("http") else API_BASE_URL + endpoint
        return self.session.get(url, timeout=30, **kwargs)

    def is_authenticated(self) -> bool:
        return bool(self.authenticated and self._validate_session())

    def _validate_session(self) -> bool:
        if not self.authenticated and not self.session.cookies.get("authcookie"):
            return False
        try:
            response = self.session.get(
                API_BASE_URL + "/api/index-price",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://theautotrender.com/",
                },
                timeout=15,
            )
            self.authenticated = (
                response.ok
                and "text/html" not in response.headers.get("Content-Type", "").lower()
            )
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
