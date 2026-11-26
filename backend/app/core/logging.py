import logging
import traceback
from threading import Lock

from sqlalchemy import text

from app.db.session import get_engine


class _OptionChainLiveQuotesAccessFilter(logging.Filter):
    """Hide the large instrument_tokens query string from Uvicorn access logs."""

    _PATH = "/api/v1/apiplatform/option-chain/live-quotes"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            request_target = args[2]
            if isinstance(request_target, str):
                path = request_target.split("?", 1)[0]
                if path == self._PATH:
                    sanitized = list(args)
                    sanitized[2] = path
                    record.args = tuple(sanitized)
        return True


class _PersistedLogHandler(logging.Handler):
    """Persist actionable application logs to MySQL without recursively logging DB failures."""

    _lock = Lock()
    _active = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("sqlalchemy", "uvicorn.access")):
            return
        if record.levelno < logging.WARNING and not record.name.startswith("app."):
            return
        if _PersistedLogHandler._active:
            return
        category = "SYSTEM"
        name = record.name.lower()
        if "autotrend" in name:
            category = "AUTOTRENDER"
        elif "kite" in name:
            category = "KITE_EXECUTION"
        elif "websocket" in name or "live_market_data" in name:
            category = "WEBSOCKET"
        elif "worker" in name or "scheduler" in name:
            category = "WORKER"
        elif "api" in name or "routes" in name:
            category = "API"
        severity = "CRITICAL" if record.levelno >= logging.CRITICAL else "ERROR" if record.levelno >= logging.ERROR else "WARNING" if record.levelno >= logging.WARNING else "INFO"
        details = ""
        if record.exc_info:
            details = "".join(traceback.format_exception(*record.exc_info))
        elif record.stack_info:
            details = record.stack_info
        try:
            _PersistedLogHandler._active = True
            with _PersistedLogHandler._lock, get_engine().begin() as conn:
                conn.execute(text("""INSERT INTO application_error_logs
                    (category,severity,message,details,symbol,operation)
                    VALUES (:category,:severity,:message,:details,:symbol,:operation)"""), {
                    "category": category,
                    "severity": severity,
                    "message": record.getMessage()[:6000],
                    "details": details[:20000] or None,
                    "symbol": getattr(record, "symbol", None),
                    "operation": record.name[:120],
                })
        except Exception:
            # Logging must never destabilize the application.
            pass
        finally:
            _PersistedLogHandler._active = False


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    root = logging.getLogger()
    if not any(isinstance(h, _PersistedLogHandler) for h in root.handlers):
        root.addHandler(_PersistedLogHandler())

    # Keep normal access logging, but don't emit hundreds of instrument tokens
    # on every option-chain quote poll.
    logging.getLogger("uvicorn.access").addFilter(_OptionChainLiveQuotesAccessFilter())
