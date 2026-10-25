"""Configurable-interval AutoTrender signal-monitoring worker.

This first scheduler slice deliberately evaluates and logs signals only. It does not
create trades until option-contract, live-premium, target, and duplicate-entry wiring
has been integrated and validated.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.providers.autotrender.auth import AutoTrenderAuth
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
    """Extract the newest row from supported AutoTrender response envelopes."""
    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        return rows[-1] if rows else None
    if not isinstance(payload, dict):
        return None

    # Some responses wrap the actual row under data.trend/summary.
    for key in ("data", "intraday", "records", "rows", "items", "result", "trend", "summary", "strength"):
        value = payload.get(key)
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            return rows[-1] if rows else None
        if isinstance(value, dict):
            if any(field in value for field in ("call", "callStrength", "call_strength")):
                return value
            nested = latest_intraday_row(value)
            if nested is not None:
                return nested

    if any(field in payload for field in ("call", "callStrength", "call_strength")):
        return payload
    return None


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
            parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    elif isinstance(raw, str):
        candidate = raw.strip()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    if fmt in ("%H:%M:%S", "%H:%M"):
                        now_ist = datetime.now(IST)
                        parsed = parsed.replace(year=now_ist.year, month=now_ist.month, day=now_ist.day)
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
    if age < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
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


def run_cycle(now: datetime | None = None) -> dict[str, Any]:
    """Evaluate each enabled index once; never creates or modifies trades."""
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
        return result

    outcomes: dict[str, Any] = {}
    for index in enabled_indices(settings.get("autoTrenderDashboardIndices")):
        if index not in INDEX_CATEGORIES:
            continue
        try:
            payload = _fetch_intraday(index)
            row = latest_intraday_row(payload)
            if row is None:
                outcomes[index] = {"state": "skipped", "reason": "no_intraday_row"}
                continue
            max_age = _poll_interval_seconds(settings.get("autoTrenderMaxSignalAgeSeconds", DEFAULT_MAX_SIGNAL_AGE_SECONDS))
            fresh, freshness_reason, signal_timestamp = signal_freshness(row, current, max_age)
            if not fresh:
                outcomes[index] = {"state": "rejected", "reason": freshness_reason}
                continue
            signal_id = signal_timestamp.isoformat()
            if _LAST_SIGNAL_BY_INDEX.get(index) == signal_id:
                outcomes[index] = {"state": "skipped", "reason": "duplicate_signal"}
                continue
            evaluation: Evaluation = evaluate_signal(
                row,
                call_put_strength_diff=float(settings["callPutStrengthDiff"]),
                call_pcr=float(settings["callPcr"]),
                put_pcr=float(settings["putPcr"]),
                vwap_price_difference_buffer=float(settings["vwapPriceDifferenceBuffer"]),
            )
            if evaluation.eligible:
                _LAST_SIGNAL_BY_INDEX[index] = signal_id
            outcomes[index] = {
                "state": "eligible_candidate" if evaluation.eligible else "rejected",
                "signal_timestamp": signal_id,
                "direction": evaluation.direction,
                "reason": evaluation.reason,
                "strength_difference": evaluation.strength_difference,
                "vwap_difference_percent": evaluation.vwap_difference_percent,
            }
            logger.info(
                "AutoTrender evaluation index=%s eligible=%s direction=%s reason=%s",
                index, evaluation.eligible, evaluation.direction, evaluation.reason,
            )
        except Exception as exc:  # isolate a failed index so other indices still evaluate
            logger.exception("AutoTrender worker failed for index %s", index)
            outcomes[index] = {"state": "error", "reason": type(exc).__name__}

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
