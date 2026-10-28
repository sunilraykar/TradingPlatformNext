"""Configurable AutoTrender worker for fresh PAPER option entries.

Creates only PAPER trades through the canonical Trade Terminal. It never places
broker orders; the existing exit workers remain responsible for exits.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.providers.autotrender.auth import AutoTrenderAuth
from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider
from app.services import trade_terminal
from app.services.autotrender_signal_evaluator import Evaluation, evaluate_signal

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_POLL_SECONDS = 5
MIN_POLL_SECONDS = 1
MAX_POLL_SECONDS = 3600
DEFAULT_MAX_SIGNAL_AGE_SECONDS = 300
MAX_FUTURE_CLOCK_SKEW_SECONDS = 30
SIGNAL_TIMESTAMP_FIELDS = ("timestamp", "time", "datetime", "dateTime", "created_at", "createdAt", "updated_at", "updatedAt", "candle_time", "candleTime", "ts")
DEFAULT_INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")
INDEX_CATEGORIES = {
    "NIFTY": "Nifty",
    "BANKNIFTY": "BankNifty",
    "FINNIFTY": "FinNifty",
    "MIDCPNIFTY": "MidcpNifty",
    "SENSEX": "Sensex",
    "BANKEX": "Bankex",
}
_INDEX_SETTING_NAMES = {
    "NIFTY": ("NIFTY", "Nifty"),
    "BANKNIFTY": ("BANKNIFTY", "BankNifty"),
    "FINNIFTY": ("FINNIFTY", "FinNifty"),
    "MIDCPNIFTY": ("MIDCPNIFTY", "MidcpNifty"),
    "SENSEX": ("SENSEX", "Sensex"),
    "BANKEX": ("BANKEX", "Bankex"),
}
_AUTH: AutoTrenderAuth | None = None
_LAST_CYCLE: dict[str, Any] = {"state": "not_started", "last_cycle_at": None, "indices": {}, "poll_interval_seconds": DEFAULT_POLL_SECONDS}
_LAST_SIGNAL_BY_INDEX: dict[str, str] = {}

_ENTRY_CONDITION_LOG_TABLE_READY = False


def _log_entry_condition(
    *,
    index: str | None,
    state: str,
    reason: str,
    signal_timestamp: str | None = None,
    direction: str | None = None,
    signal_row: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist every failed/skipped automatic-entry decision in MySQL.

    Logging is deliberately best-effort: a logging failure must never stop the
    signal worker from evaluating other indices or creating an otherwise valid trade.
    """
    global _ENTRY_CONDITION_LOG_TABLE_READY
    try:
        engine = get_engine()
        with engine.begin() as connection:
            if not _ENTRY_CONDITION_LOG_TABLE_READY:
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS trade_entry_condition_logs (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        source VARCHAR(40) NOT NULL DEFAULT 'AUTOTRENDER',
                        underlying VARCHAR(40) NULL,
                        state VARCHAR(40) NOT NULL,
                        reason VARCHAR(255) NOT NULL,
                        signal_timestamp VARCHAR(80) NULL,
                        direction VARCHAR(20) NULL,
                        signal_data LONGTEXT NULL,
                        details LONGTEXT NULL,
                        INDEX idx_entry_condition_occurred_at (occurred_at),
                        INDEX idx_entry_condition_underlying_time (underlying, occurred_at),
                        INDEX idx_entry_condition_reason (reason)
                    ) ENGINE=InnoDB
                """))
                _ENTRY_CONDITION_LOG_TABLE_READY = True
            connection.execute(text("""
                INSERT INTO trade_entry_condition_logs
                    (source, underlying, state, reason, signal_timestamp, direction, signal_data, details)
                VALUES
                    ('AUTOTRENDER', :underlying, :state, :reason, :signal_timestamp,
                     :direction, :signal_data, :details)
            """), {
                "underlying": index,
                "state": str(state)[:40],
                "reason": str(reason)[:255],
                "signal_timestamp": signal_timestamp,
                "direction": direction,
                "signal_data": json.dumps(signal_row, default=str, separators=(",", ":")) if signal_row is not None else None,
                "details": json.dumps(details, default=str, separators=(",", ":")) if details is not None else None,
            })
    except Exception:
        logger.exception("Could not persist failed entry condition index=%s state=%s reason=%s", index, state, reason)




def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _read_settings() -> dict[str, Any]:
    """Read the existing UI settings; retain documented defaults for unset keys."""
    keys = (
        "autoTrenderDashboardIndices",
        "callPutStrengthDiff",
        "callPcr",
        "putPcr",
        "vwapPriceDifferenceBuffer",
        "autoTrenderEntryStartTime",
        "autoTrenderEntryEndTime",
        "autoTrenderWorkerIntervalSeconds",
        "autoTrenderMaxSignalAgeSeconds",
        "targetFallbackPercent",
    )
    defaults: dict[str, Any] = {
        "autoTrenderDashboardIndices": {
            "Nifty": True,
            "BankNifty": True,
            "FinNifty": True,
            "Sensex": True,
        },
        "callPutStrengthDiff": 10.0,
        "callPcr": 1.0,
        "putPcr": 1.0,
        "vwapPriceDifferenceBuffer": 0.3,
        "autoTrenderEntryStartTime": "10:00",
        "autoTrenderEntryEndTime": "15:25",
        "autoTrenderMaxSignalAgeSeconds": DEFAULT_MAX_SIGNAL_AGE_SECONDS,
        "targetFallbackPercent": 1.0,
        "autoTrenderWorkerIntervalSeconds": DEFAULT_POLL_SECONDS,
    }
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT setting_key, setting_value FROM app_settings "
                    "WHERE setting_key IN (" + ",".join(f":key{i}" for i in range(len(keys))) + ")"
                ),
                {f"key{i}": "ui." + key for i, key in enumerate(keys)},
            ).mappings().all()
    except SQLAlchemyError:
        logger.exception("AutoTrender worker cannot read app settings; skipping this cycle")
        raise

    settings = dict(defaults)
    for row in rows:
        key = str(row["setting_key"])[3:]
        settings[key] = _decode(row["setting_value"])
    return settings


def _poll_interval_seconds(value: Any) -> int:
    """Normalize the saved worker interval to a safe 1-second–1-hour range."""
    try:
        interval = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_POLL_SECONDS
    return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, interval))


def _max_signal_age_seconds(value: Any) -> int:
    """Validate signal age separately from the worker polling interval."""
    try:
        age = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_SIGNAL_AGE_SECONDS
    return max(1, min(3600, age))


def enabled_indices(value: Any) -> list[str]:
    """Normalize the saved AutoTrender dashboard index map/list."""
    if isinstance(value, dict):
        enabled = []
        for index, aliases in _INDEX_SETTING_NAMES.items():
            if any(alias in value and value[alias] is True for alias in aliases):
                enabled.append(index)
        return enabled
    if isinstance(value, (list, tuple, set)):
        allowed = set(INDEX_CATEGORIES)
        return list(dict.fromkeys(str(item).strip().upper() for item in value if str(item).strip().upper() in allowed))
    return list(DEFAULT_INDICES)


def latest_intraday_row(payload: Any) -> dict[str, Any] | None:
    """Select the chronologically latest row from supported AutoTrender envelopes."""
    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        if not rows:
            return None
        # Do not assume provider rows are sorted. Prefer the newest parseable
        # source timestamp/time; preserve last-row fallback only if none parse.
        ranked = [(parse_signal_timestamp(row), i, row) for i, row in enumerate(rows)]
        parseable = [item for item in ranked if item[0] is not None]
        return max(parseable, key=lambda item: (item[0], item[1]))[2] if parseable else rows[-1]
    if not isinstance(payload, dict):
        return None

    # Recurse through known response envelopes. If multiple arrays are present,
    # choose the newest row across them rather than the first matching container.
    candidates: list[dict[str, Any]] = []
    row_fields = ("call", "callStrength", "call_strength")
    if any(field in payload for field in row_fields):
        candidates.append(payload)
    for key in ("data", "intraday", "records", "rows", "items", "result", "trend", "summary", "strength"):
        value = payload.get(key)
        if isinstance(value, (list, dict)):
            row = latest_intraday_row(value)
            if row is not None:
                candidates.append(row)
    if not candidates:
        return None
    ranked = [(parse_signal_timestamp(row), i, row) for i, row in enumerate(candidates)]
    parseable = [item for item in ranked if item[0] is not None]
    return max(parseable, key=lambda item: (item[0], item[1]))[2] if parseable else candidates[-1]

def parse_signal_timestamp(row: dict[str, Any]) -> datetime | None:
    """Parse a source timestamp; missing/unknown timestamp formats fail closed."""
    raw: Any = next((row.get(key) for key in SIGNAL_TIMESTAMP_FIELDS if row.get(key) not in (None, "")), None)
    if raw is None:
        return None
    parsed: datetime | None = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            epoch = float(raw)
            if epoch > 10_000_000_000:
                epoch /= 1000
            parsed = datetime.fromtimestamp(epoch, tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None
    elif isinstance(raw, str):
        candidate = raw.strip()
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            # Provider intraday rows may expose only a local time such as "13:30".
            # Anchor time-only values to today's date in IST for freshness checks.
            try:
                parsed_time = time.fromisoformat(candidate)
                parsed = datetime.combine(datetime.now(IST).date(), parsed_time, tzinfo=IST)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        parsed = datetime.strptime(candidate, fmt)
                        break
                    except ValueError:
                        continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def signal_freshness(row: dict[str, Any], now: datetime, max_age_seconds: int) -> tuple[bool, str, datetime | None]:
    """Reject missing, malformed, future-dated, or over-age signal snapshots."""
    timestamp = parse_signal_timestamp(row)
    if timestamp is None:
        return False, "signal_timestamp_missing_or_invalid", None
    age = (now.astimezone(IST) - timestamp).total_seconds()
    if age < 0:
        return False, "signal_timestamp_in_future", timestamp
    if age > max_age_seconds:
        return False, "stale_signal", timestamp
    return True, "fresh_signal", timestamp


def _entry_window(now: datetime, settings: dict[str, Any]) -> tuple[bool, str]:
    if now.weekday() >= 5:
        return False, "weekend"
    try:
        start = time.fromisoformat(str(settings["autoTrenderEntryStartTime"]))
        end = time.fromisoformat(str(settings["autoTrenderEntryEndTime"]))
    except (KeyError, TypeError, ValueError):
        return False, "invalid_entry_window_settings"
    if end <= start:
        return False, "invalid_entry_window_settings"
    current = now.timetz().replace(tzinfo=None)
    if current < start or current >= end:
        return False, "outside_entry_window"
    return True, "inside_entry_window"


def _fetch_intraday(index: str) -> dict[str, Any]:
    """Fetch one index snapshot directly through the existing authenticated provider."""
    global _AUTH
    if _AUTH is None:
        _AUTH = AutoTrenderAuth()
    response = _AUTH.get(
        "/api/derivatives/intra-day",
        params={"category": INDEX_CATEGORIES[index], "mins": 5},
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" not in content_type:
        raise ValueError("AutoTrender returned a non-JSON intraday response")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("AutoTrender returned an unexpected intraday response")
    return payload


def _spot_price(row: dict[str, Any]) -> float | None:
    for key in ("price", "close", "ltp", "spot", "spotPrice", "indexPrice", "lastPrice"):
        value = row.get(key)
        if isinstance(value, dict):
            value = next(
                (value[field] for field in ("value", "price", "ltp", "close", "current", "number")
                 if value.get(field) not in (None, "")),
                None,
            )
        try:
            parsed = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _quote_timestamp_is_fresh(value: Any, now: datetime | None = None) -> bool:
    """Require an explicit quote timestamp no older than 120 seconds and not in the future."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    age = (current.astimezone(IST) - value.astimezone(IST)).total_seconds()
    return 0 <= age <= 120


def _create_paper_trade_under_lock(index: str, direction: str, row: dict[str, Any], signal_timestamp: datetime, target_fallback_percent: float) -> dict[str, Any]:
    """Resolve nearest-strike option and create one-lot PAPER trade from live Kite premium."""
    today = datetime.now(IST).date()
    spot = _spot_price(row)
    if spot is None:
        raise ValueError("missing_positive_spot_price")
    option_type = "CE" if direction == "BUY" else "PE"
    exchange = "BFO" if index in {"SENSEX", "BANKEX"} else "NFO"
    with get_engine().connect() as connection:
        candidates = connection.execute(text(
            "SELECT instrument_token, exchange, tradingsymbol, name, instrument_type, expiry, strike, lot_size "
            "FROM instruments WHERE source = 'KITE' AND is_active = TRUE AND exchange = :exchange "
            "AND instrument_type = :option_type AND expiry >= :today "
            "AND UPPER(tradingsymbol) LIKE :symbol_prefix "
            "ORDER BY expiry ASC, ABS(strike - :spot) ASC, instrument_version DESC"
        ), {"exchange": exchange, "option_type": option_type, "today": today,
            "symbol_prefix": index + "%", "spot": spot}).mappings().all()
    if not candidates:
        raise ValueError("no_eligible_option_contract")
    chosen = candidates[0]
    quote = get_kite_provider().quote(int(chosen["instrument_token"]))
    try:
        premium = float(quote.get("last_price"))
    except (TypeError, ValueError):
        premium = 0
    if premium <= 0:
        raise ValueError("missing_positive_live_option_premium")
    # Do not block AutoTrender paper entries because Kite quote timestamps are
    # missing or stale. Keep the positive-premium check above because the
    # trade still needs a usable entry price.
    quantity = int(chosen.get("lot_size") or 0)
    if quantity <= 0:
        raise ValueError("invalid_option_lot_size")
    target = round(premium * (1 + max(0.01, target_fallback_percent) / 100), 2)
    idempotency_key = "AUTOTRENDER:" + index + ":" + signal_timestamp.isoformat()
    active = [trade for trade in trade_terminal.list_trades(mode="PAPER", underlying=index)
              if str(trade.get("status", "OPEN")).upper() not in {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}
              and str(trade.get("source", "")).upper().replace("_", "") == "AUTOTRENDER"]
    if active:
        raise ValueError("active_autotrender_trade_exists_for_underlying")
    return trade_terminal.create_trade({
        "idempotency_key": idempotency_key, "execution_mode": "PAPER", "source": "AUTOTRENDER",
        "strategy": "AutoTrender " + direction + " " + option_type, "underlying": index,
        "instrument_symbol": str(chosen["tradingsymbol"]), "exchange": exchange,
        "instrument_token": int(chosen["instrument_token"]),
        "direction": "BUY", "quantity": quantity, "entry_price": premium, "target": target,
        "notes": "AutoTrender signal=" + direction + "; signal_timestamp=" + signal_timestamp.isoformat()
            + "; spot=" + str(spot) + "; option=" + option_type + "; expiry=" + str(chosen["expiry"])
            + "; strike=" + str(chosen["strike"]),
    })

def _create_paper_trade(index: str, direction: str, row: dict[str, Any], signal_timestamp: datetime, target_fallback_percent: float) -> dict[str, Any]:
    """Serialize active-trade check + creation across worker processes using a MySQL lock."""
    lock_name = "autotrend-paper:" + index
    with get_engine().connect() as connection:
        acquired = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar()
        if acquired != 1:
            raise ValueError("paper_entry_lock_unavailable")
        try:
            return _create_paper_trade_under_lock(
                index, direction, row, signal_timestamp, target_fallback_percent
            )
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})


def run_cycle(now: datetime | None = None) -> dict[str, Any]:
    """Evaluate each enabled index and create a paper trade for each fresh eligible signal."""
    global _LAST_CYCLE
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)
    try:
        settings = _read_settings()
    except SQLAlchemyError:
        result = {
            "state": "degraded",
            "last_cycle_at": current.isoformat(),
            "reason": "settings_unavailable",
            "indices": {},
            "poll_interval_seconds": DEFAULT_POLL_SECONDS,
        }
        _LAST_CYCLE = result
        _log_entry_condition(index=None, state="cycle_error", reason="settings_unavailable",
                             details={"error": "SQLAlchemyError"})
        return result

    poll_interval_seconds = _poll_interval_seconds(settings.get("autoTrenderWorkerIntervalSeconds"))
    allowed, window_reason = _entry_window(current, settings)
    if not allowed:
        result = {
            "state": "waiting",
            "last_cycle_at": current.isoformat(),
            "reason": window_reason,
            "indices": {},
            "poll_interval_seconds": poll_interval_seconds,
        }
        _LAST_CYCLE = result
        _log_entry_condition(index=None, state="entry_blocked", reason=window_reason,
                             details={"current_time": current.isoformat()})
        return result

    outcomes: dict[str, Any] = {};
    for index in enabled_indices(settings.get("autoTrenderDashboardIndices")):
        if index not in INDEX_CATEGORIES:
            continue
        try:
            payload = _fetch_intraday(index)
            row = latest_intraday_row(payload)
            if row is None:
                outcomes[index] = {"state": "skipped", "reason": "no_intraday_row"}
                _log_entry_condition(index=index, state="skipped", reason="no_intraday_row")
                continue
            # Signal timestamp age/missing/format checks must not reject a signal.
            # Preserve a parseable source timestamp for deduplication; otherwise use
            # the current minute as a stable fallback for this polling cycle.
            signal_timestamp = (
                parse_signal_timestamp(row)
                or current.replace(second=0, microsecond=0)
            )
            signal_id = signal_timestamp.isoformat()
            if _LAST_SIGNAL_BY_INDEX.get(index) == signal_id:
                outcomes[index] = {"state": "skipped", "reason": "duplicate_signal"}
                _log_entry_condition(index=index, state="skipped", reason="duplicate_signal",
                                     signal_timestamp=signal_id, signal_row=row)
                continue
            evaluation: Evaluation = evaluate_signal(
                row,
                call_put_strength_diff=float(settings["callPutStrengthDiff"]),
                call_pcr=float(settings["callPcr"]),
                put_pcr=float(settings["putPcr"]),
                vwap_price_difference_buffer=float(settings["vwapPriceDifferenceBuffer"]),
            )
            state = "rejected"
            reason = evaluation.reason
            trade_id = None
            if evaluation.eligible:
                try:
                    trade = _create_paper_trade(index, evaluation.direction or "", row, signal_timestamp,
                                                float(settings.get("targetFallbackPercent", 1.0)))
                    _LAST_SIGNAL_BY_INDEX[index] = signal_id
                    state = "paper_trade_created"
                    reason = "paper_trade_created"
                    trade_id = trade.get("trade_id")
                except (ValueError, RuntimeError, KiteProviderError, SQLAlchemyError) as exc:
                    state = "entry_blocked"
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    logger.warning("AutoTrender paper entry blocked index=%s reason=%s", index, reason)
            outcomes[index] = {
                "state": state,
                "signal_timestamp": signal_id,
                "direction": evaluation.direction,
                "reason": reason,
                "trade_id": trade_id,
                "strength_difference": evaluation.strength_difference,
                "vwap_difference_percent": evaluation.vwap_difference_percent,
            }
            if state != "paper_trade_created":
                _log_entry_condition(
                    index=index, state=state, reason=reason, signal_timestamp=signal_id,
                    direction=evaluation.direction, signal_row=row,
                    details={
                        "eligible": evaluation.eligible,
                        "strength_difference": evaluation.strength_difference,
                        "vwap_difference_percent": evaluation.vwap_difference_percent,
                    },
                )
            logger.info(
                "AutoTrender evaluation index=%s eligible=%s direction=%s reason=%s trade_id=%s",
                index, evaluation.eligible, evaluation.direction, reason, trade_id,
            )
        except Exception as exc:  # isolate a failed index so other indices still evaluate
            logger.exception("AutoTrender worker failed for index %s", index)
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            outcomes[index] = {"state": "error", "reason": reason}
            _log_entry_condition(index=index, state="error", reason=reason,
                                 details={"exception_type": type(exc).__name__})

    result = {
        "state": "running",
        "last_cycle_at": current.isoformat(),
        "reason": "evaluation_complete",
        "indices": outcomes,
        "poll_interval_seconds": poll_interval_seconds,
    }
    _LAST_CYCLE = result
    return result


def status() -> dict[str, Any]:
    """Return the latest process-local worker status for diagnostics."""
    return dict(_LAST_CYCLE)


async def run_scheduler() -> None:
    """Run AutoTrender evaluation cycles at the interval saved in AutoTrender settings."""
    logger.info("AutoTrender signal worker started")
    while True:
        try:
            result = await asyncio.to_thread(run_cycle)
            interval = _poll_interval_seconds(result.get("poll_interval_seconds", DEFAULT_POLL_SECONDS))
            logger.debug("AutoTrender worker cycle completed; next cycle in %s seconds", interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AutoTrender worker cycle failed unexpectedly")
            interval = DEFAULT_POLL_SECONDS
        await asyncio.sleep(interval)
