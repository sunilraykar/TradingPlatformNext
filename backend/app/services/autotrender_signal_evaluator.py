"""Fail-closed AutoTrender signal evaluation for automatic PAPER entries.

This module intentionally contains no network, database, or broker calls so strategy
thresholds can be tested independently of the worker and trade lifecycle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("value", "price", "ltp", "close", "current", "amount", "number"):
            if value.get(key) not in (None, "") and not isinstance(value.get(key), (dict, list)):
                value = value[key]
                break
        else:
            return None
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _signal(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("type", "value", "signal", "label", "status", "name", "text"):
            candidate = value.get(key)
            if candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                value = candidate
                break
        else:
            return ""
    return str(value or "").strip().upper()


@dataclass(frozen=True)
class Evaluation:
    eligible: bool
    direction: str | None
    reason: str
    strength_difference: float | None = None
    vwap_difference_percent: float | None = None


def evaluate_signal(
    row: dict[str, Any],
    *,
    call_put_strength_diff: float = 10.0,
    call_pcr: float = 1.0,
    put_pcr: float = 1.0,
    vwap_price_difference_buffer: float = 0.3,
) -> Evaluation:
    """Evaluate one latest intraday row using documented AutoTrender entry rules."""
    call = _number(row.get("call", row.get("callStrength", row.get("call_strength"))))
    put = _number(row.get("put", row.get("putStrength", row.get("put_strength"))))
    price = _number(row.get("price", row.get("close", row.get("ltp"))))
    vwap = _number(row.get("vWap", row.get("vwap")))
    pcr = _number(row.get("pcr"))
    vwap_signal = _signal(row.get("vWapSignal", row.get("vwapSignal", row.get("VWAP Signal"))))
    option_signal = _signal(row.get("optionSignal", row.get("Option Signal")))

    if vwap_signal not in {"BUY", "SELL"} or option_signal not in {"BUY", "SELL"}:
        return Evaluation(False, None, "INVALID_OR_MISSING_SIGNAL")
    if vwap_signal != option_signal:
        return Evaluation(False, None, "SIGNALS_DISAGREE")
    if call is None or put is None or call < 0 or put < 0 or call + put <= 0:
        return Evaluation(False, None, "INVALID_STRENGTH")
    if price is None or vwap is None or price <= 0 or vwap <= 0:
        return Evaluation(False, None, "INVALID_PRICE_OR_VWAP")
    if pcr is None or pcr < 0:
        return Evaluation(False, None, "INVALID_PCR")

    total = call + put
    call_pct = call / total * 100
    put_pct = put / total * 100
    strength_difference = abs(call_pct - put_pct)
    vwap_difference = abs((price - vwap) / price * 100)
    strength_threshold = abs(call_put_strength_diff)

    # Floating-point arithmetic can represent an exact boundary (e.g. 55/45 = 10)
    # as a value infinitesimally above it. Treat numerically equal values as equal,
    # preserving the strict "greater than" rule.
    at_strength_threshold = math.isclose(
        strength_difference, strength_threshold, rel_tol=0.0, abs_tol=1e-9
    )
    if strength_difference < strength_threshold or at_strength_threshold:
        return Evaluation(False, None, "STRENGTH_DIFFERENCE_NOT_ABOVE_THRESHOLD", strength_difference, vwap_difference)
    if vwap_difference >= abs(vwap_price_difference_buffer):
        return Evaluation(False, None, "VWAP_GAP_NOT_BELOW_THRESHOLD", strength_difference, vwap_difference)

    bullish = option_signal == "BUY" and pcr > call_pcr
    bearish = option_signal == "SELL" and pcr < put_pcr
    if bullish and bearish:
        return Evaluation(False, None, "AMBIGUOUS_DIRECTION", strength_difference, vwap_difference)
    if not bullish and not bearish:
        return Evaluation(False, None, "PCR_DIRECTION_CONDITION_FAILED", strength_difference, vwap_difference)

    return Evaluation(True, option_signal, "ELIGIBLE", strength_difference, vwap_difference)
