"""Automatic exit monitor for open AutoTrender option trades.

The monitor reads the authenticated AutoTrender intraday feed directly. It does
not depend on frontend persistence or the Historical Data view. Each source row
is evaluated once per trade, and all deterioration counters are independent.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.services import autotrender_kite_execution, autotrender_paper_trade_worker, trade_terminal

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
POLL_SECONDS = 60
MAX_READING_AGE_SECONDS = 300
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(15, 30)
_TERMINAL = {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value", value.get("type"))
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _signal(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", value.get("type", value.get("signal", "")))
    return str(value or "").strip().upper()


def _entry_direction(trade: dict[str, Any]) -> str | None:
    snapshot = trade.get("entry_signal_snapshot")
    if isinstance(snapshot, dict):
        value = str(snapshot.get("signal_direction") or "").strip().upper()
        if value in {"BUY", "SELL"}:
            return value
    strategy = str(trade.get("strategy") or "")
    match = re.search(r"\b(BUY|SELL)\b", strategy, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    option_type = str(trade.get("instrument_symbol") or "").upper()
    if re.search(r"\d+CE$", option_type):
        return "BUY"
    if re.search(r"\d+PE$", option_type):
        return "SELL"
    return None


def _market_window(now: datetime) -> bool:
    local = now.astimezone(IST)
    return local.weekday() < 5 and MARKET_OPEN <= local.time().replace(tzinfo=None) < MARKET_CLOSE


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _reading(row: dict[str, Any], direction: str, now: datetime) -> dict[str, Any] | None:
    timestamp = autotrender_paper_trade_worker.parse_signal_timestamp(row)
    if timestamp is None:
        return None
    age = (now.astimezone(IST) - timestamp).total_seconds()
    if age < 0 or age > MAX_READING_AGE_SECONDS:
        return None

    call = _number(_row_value(row, "call", "Call", "callStrength", "call_strength"))
    put = _number(_row_value(row, "put", "Put", "putStrength", "put_strength"))
    pcr = _number(_row_value(row, "pcr", "PCR", "Pcr"))
    diff = _number(_row_value(row, "diff", "Diff"))
    vwap_signal = _signal(_row_value(row, "vWapSignal", "vwapSignal", "VWAP Signal"))
    option_signal = _signal(_row_value(row, "optionSignal", "Option Signal"))

    if call is None or put is None or call + put <= 0:
        strength_advantage = None
    else:
        call_pct = call / (call + put) * 100
        put_pct = put / (call + put) * 100
        strength_advantage = call_pct - put_pct if direction == "BUY" else put_pct - call_pct

    return {
        "timestamp": timestamp.isoformat(),
        "call": call,
        "put": put,
        "strength_advantage": strength_advantage,
        "diff": diff,
        "pcr": pcr,
        "vwap_signal": vwap_signal,
        "option_signal": option_signal,
    }


def _advance_counter(previous: float | None, current: float | None, *, adverse: bool) -> int:
    if previous is None or current is None:
        return 0
    return (1 if adverse else 0)


def _evaluate_trade(trade: dict[str, Any], row: dict[str, Any], now: datetime) -> tuple[dict[str, Any], str | None]:
    direction = _entry_direction(trade)
    if direction not in {"BUY", "SELL"}:
        return {}, None
    reading = _reading(row, direction, now)
    if reading is None:
        return {}, None

    old = trade.get("autotrend_exit_monitor")
    state = dict(old) if isinstance(old, dict) else {}
    if state.get("last_source_timestamp") == reading["timestamp"]:
        return {}, None

    previous = state.get("last_reading") if isinstance(state.get("last_reading"), dict) else {}
    counters = dict(state.get("counters") or {})
    for name in ("strength", "dominance", "pcr"):
        counters[name] = int(counters.get(name) or 0)

    vwap_signal = reading["vwap_signal"]
    option_signal = reading["option_signal"]
    reason = None
    if vwap_signal in {"BUY", "SELL"} and vwap_signal != direction:
        reason = "AUTOTRENDER_VWAP_SIGNAL_REVERSAL"
    elif option_signal in {"BUY", "SELL"} and option_signal != direction:
        reason = "AUTOTRENDER_OPTION_SIGNAL_REVERSAL"
    elif vwap_signal in {"BUY", "SELL"} and option_signal in {"BUY", "SELL"} and vwap_signal != option_signal:
        reason = "AUTOTRENDER_DIRECTIONAL_SIGNAL_MISMATCH"

    previous_strength = _number(previous.get("strength_advantage"))
    current_strength = reading["strength_advantage"]
    if current_strength is not None and previous_strength is not None:
        adverse_strength = current_strength < previous_strength
        counters["strength"] = counters["strength"] + 1 if adverse_strength else 0
    # The first valid observation seeds the comparison; it is not a decline.

    previous_diff = _number(previous.get("diff"))
    current_diff = reading["diff"]
    if current_diff is not None and previous_diff is not None:
        # BUY dominance is stronger as Diff becomes more negative.
        # SELL dominance is stronger as Diff becomes more positive.
        adverse_dominance = current_diff > previous_diff if direction == "BUY" else current_diff < previous_diff
        counters["dominance"] = counters["dominance"] + 1 if adverse_dominance else 0

    previous_pcr = _number(previous.get("pcr"))
    current_pcr = reading["pcr"]
    if current_pcr is not None and previous_pcr is not None:
        adverse_pcr = current_pcr < previous_pcr if direction == "BUY" else current_pcr > previous_pcr
        counters["pcr"] = counters["pcr"] + 1 if adverse_pcr else 0

    state = {
        "last_source_timestamp": reading["timestamp"],
        "last_reading": reading,
        "counters": counters,
        "updated_at": now.astimezone(IST).isoformat(),
    }
    if reason is None:
        for name in ("strength", "dominance", "pcr"):
            if counters[name] >= 4:
                reason = "AUTOTRENDER_" + name.upper() + "_DETERIORATION_4_READINGS"
                break
    return {"autotrend_exit_monitor": state}, reason


def run_cycle(now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)
    if not _market_window(current):
        return {"state": "outside_market_window", "checked": 0, "exits": 0}

    trades = [
        trade for trade in trade_terminal.list_trades(source="AUTOTRENDER")
        if str(trade.get("status") or "OPEN").upper() not in _TERMINAL
        and str(trade.get("execution_mode") or "").upper() in {"PAPER", "LIVE"}
    ]
    if not trades:
        return {"state": "idle", "checked": 0, "exits": 0}

    by_underlying: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        underlying = str(trade.get("underlying") or "").strip().upper()
        if underlying in autotrender_paper_trade_worker.INDEX_CATEGORIES:
            by_underlying.setdefault(underlying, []).append(trade)

    checked = exits = 0
    outcomes: dict[str, Any] = {}
    for underlying, index_trades in by_underlying.items():
        try:
            payload = autotrender_paper_trade_worker._fetch_intraday(underlying)
            row = autotrender_paper_trade_worker.latest_intraday_row(payload)
            if row is None:
                outcomes[underlying] = {"state": "skipped", "reason": "no_intraday_row"}
                continue
            for trade in index_trades:
                latest = trade_terminal.get_trade(str(trade["trade_id"]))
                if not latest or str(latest.get("status") or "OPEN").upper() in _TERMINAL:
                    continue
                updates, reason = _evaluate_trade(latest, row, current)
                if not updates:
                    continue
                checked += 1
                if reason:
                    risk_updates = {
                        **updates,
                        "autotrend_exit_trigger": reason,
                        "autotrend_exit_triggered_at": current.isoformat(),
                    }
                    if str(latest.get("execution_mode") or "").upper() == "LIVE":
                        result = autotrender_kite_execution.submit_exit(latest, reason, risk_updates)
                        outcomes[str(latest["trade_id"])] = {
                            "state": "kite_exit_requested",
                            "reason": reason,
                            "order_status": result.get("status"),
                        }
                    else:
                        trade_terminal.update_trade(str(latest["trade_id"]), {
                            **risk_updates,
                            "status": "CLOSED",
                            "exit_reason": reason,
                            "exit_method": "PAPER_MARKET_SIMULATION",
                            "auto_exit_at": current.isoformat(),
                        }, event_type="AUTOTRENDER_EXIT_MONITOR")
                        outcomes[str(latest["trade_id"])] = {"state": "paper_closed", "reason": reason}
                    exits += 1
                else:
                    event_type = "BROKER_RISK_STATE" if str(latest.get("execution_mode") or "").upper() == "LIVE" else "AUTOTRENDER_EXIT_MONITOR"
                    trade_terminal.update_trade(str(latest["trade_id"]), updates, event_type=event_type)
        except Exception as exc:
            logger.exception("AutoTrender exit monitor failed for underlying=%s", underlying)
            outcomes[underlying] = {"state": "error", "reason": type(exc).__name__}
    return {"state": "running", "checked": checked, "exits": exits, "outcomes": outcomes}


async def run_scheduler() -> None:
    logger.info("AutoTrender exit monitor started; polling every %s seconds", POLL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AutoTrender exit monitor cycle failed")
        await asyncio.sleep(POLL_SECONDS)
