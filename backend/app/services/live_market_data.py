"""Lifecycle manager for Kite WebSocket market data."""
import logging
from threading import RLock
from typing import Any

from app.core.config import get_settings
from app.providers.kite.ticker import KiteTickerAdapter
from app.services.live_quotes import live_quotes

logger = logging.getLogger(__name__)

_lock = RLock()
_ticker: KiteTickerAdapter | None = None
_state = "stopped"
_subscriptions: dict[int, str] = {}


def _on_state(value: str) -> None:
    global _state
    with _lock:
        _state = value
        ticker = _ticker if value == "connected" else None
        subscriptions = dict(_subscriptions) if ticker is not None else {}
    # KiteTicker.connect(threaded=True) returns before the socket is ready.
    # Apply all desired subscriptions only from the actual on_connect callback.
    if ticker is not None:
        for mode in ("ltp", "quote", "full"):
            tokens = [token for token, wanted_mode in subscriptions.items() if wanted_mode == mode]
            if tokens:
                try:
                    ticker.subscribe(tokens, mode=mode)
                except Exception:
                    logger.exception("Could not apply Kite %s subscriptions after connect", mode)


def start() -> dict[str, Any]:
    """Start the ticker for the current configured Kite session."""
    global _ticker, _state
    settings = get_settings()
    if not settings.kite_api_key:
        _state = "login_required"
        return status()
    with _lock:
        if _ticker is not None:
            return status()
        token = _active_token()
        if not token:
            _state = "login_required"
            return status()
        # Forward each cached Kite tick into the canonical Trade Terminal cache.
        # Import lazily to avoid a module cycle (trade_terminal imports this module).
        from app.services import trade_terminal
        live_quotes.add_listener(trade_terminal.apply_quote_to_open_trades)
        ticker = KiteTickerAdapter(settings.kite_api_key, token, live_quotes, _on_state)
        _ticker = ticker
        _state = "connecting"
        try:
            # Subscriptions are applied by _on_state("connected"), not here:
            # connect(threaded=True) is asynchronous and the socket may not be ready.
            ticker.connect(threaded=True)
        except Exception:
            _ticker = None
            _state = "error"
            raise
    return status()


def _active_token() -> str:
    from app.providers.kite.registry import get_active_kite_token
    return get_active_kite_token()


def stop() -> None:
    global _ticker, _state
    with _lock:
        ticker, _ticker = _ticker, None
        _state = "stopped"
    if ticker is not None:
        try:
            ticker.close()
        except (RuntimeError, OSError):
            logger.exception("Failed to close Kite ticker")
            _state = "error"


def subscribe(instrument_tokens: list[int], mode: str = "quote") -> dict[str, Any]:
    if mode not in {"ltp", "quote", "full"}:
        raise ValueError("mode must be ltp, quote, or full")
    unique_tokens = list(dict.fromkeys(int(token) for token in instrument_tokens))
    if not unique_tokens or any(token <= 0 for token in unique_tokens):
        raise ValueError("instrument_tokens must contain positive integer tokens")
    with _lock:
        for token in unique_tokens:
            _subscriptions[token] = mode
        ticker = _ticker
        connected = _state == "connected"
    if ticker is None:
        try:
            start()
        except Exception:
            # Desired subscriptions remain registered; startup/reconnect can retry.
            logger.exception("Kite ticker start failed; desired subscriptions retained in memory")
    elif connected:
        try:
            ticker.subscribe(unique_tokens, mode=mode)
        except Exception:
            # Keep the desired set; the next successful on_connect reapplies it.
            logger.exception("Kite subscription deferred; desired tokens retained in memory")
    return status()


def unsubscribe(instrument_tokens: list[int]) -> dict[str, Any]:
    unique_tokens = list(dict.fromkeys(int(token) for token in instrument_tokens))
    with _lock:
        for token in unique_tokens:
            _subscriptions.pop(token, None)
        ticker = _ticker
    if ticker is not None and unique_tokens:
        ticker.unsubscribe(unique_tokens)
    return status()


def status() -> dict[str, Any]:
    with _lock:
        return {
            "state": _state,
            "connected": _state == "connected",
            "subscribed_tokens": sorted(_subscriptions),
            "subscriptions": [{"instrument_token": token, "mode": mode} for token, mode in sorted(_subscriptions.items())],
            "subscription_count": len(_subscriptions),
        }



# AutoTrender's supported underlying indices and their Kite instrument-master names.
# These are index spot instruments, not every option contract in the derivative chain.
_AUTOTRENDER_INDEX_SYMBOLS = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "BANKNIFTY": ("NSE", "NIFTY BANK"),
    "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
    "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"),
    "SENSEX": ("BSE", "SENSEX"),
    "BANKEX": ("BSE", "BANKEX"),
}


def subscribe_autotrender_indices() -> dict[str, Any]:
    """Register default in-memory Kite subscriptions for all AutoTrender indices."""
    from sqlalchemy import text
    from app.db.session import get_engine

    desired = list(_AUTOTRENDER_INDEX_SYMBOLS.values())
    rows = []
    try:
        with get_engine().connect() as connection:
            for exchange, symbol in desired:
                row = connection.execute(
                    text("""
                        SELECT instrument_token, exchange, tradingsymbol
                        FROM instruments
                        WHERE source = 'KITE' AND is_active = TRUE
                          AND exchange = :exchange AND tradingsymbol = :symbol
                        ORDER BY instrument_version DESC
                        LIMIT 1
                    """),
                    {"exchange": exchange, "symbol": symbol},
                ).mappings().first()
                if row:
                    rows.append(dict(row))
    except Exception:
        logger.exception("Could not resolve AutoTrender index tokens from instrument master")
        return {
            "registered": 0,
            "missing_symbols": [symbol for _, symbol in desired],
            "error": "instrument_master_unavailable",
            "subscriptions": status(),
        }

    tokens = [int(row["instrument_token"]) for row in rows]
    if tokens:
        subscribe(tokens, mode="quote")
    resolved = {(row["exchange"], row["tradingsymbol"]) for row in rows}
    missing = [symbol for exchange, symbol in desired if (exchange, symbol) not in resolved]
    logger.info(
        "AutoTrender default index subscriptions registered: %s; missing: %s",
        [row["tradingsymbol"] for row in rows], missing,
    )
    return {
        "registered": len(tokens),
        "resolved_symbols": [row["tradingsymbol"] for row in rows],
        "missing_symbols": missing,
        "subscriptions": status(),
    }


def reset() -> None:
    stop()
    with _lock:
        _subscriptions.clear()
    live_quotes.clear()
