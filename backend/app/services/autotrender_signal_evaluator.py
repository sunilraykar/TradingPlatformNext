"""Fail-closed AutoTrender signal evaluation for automatic PAPER entries.

Direction comes from the signed put-minus-call strength percentage. VWAP Signal
and Option Signal are confirmation filters: they must be valid and agree, but
their labels do not determine bullish versus bearish direction.
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
    """Evaluate the signed put-minus-call strength rule and confirmation filters."""
    call = _number(row.get("call", row.get("callStrength", row.get("call_strength"))))
    put = _number(row.get("put", row.get("putStrength", row.get("put_strength"))))
    price = _number(row.get("price", row.get("close", row.get("ltp"))))
    vwap = _number(row.get("vWap", row.get("vwap")))
    vwap_signal = _signal(row.get("vWapSignal", row.get("vwapSignal", row.get("VWAP Signal"))))
    option_signal = _signal(row.get("optionSignal", row.get("Option Signal")))

    if call is None or put is None or call < 0 or put < 0 or call + put <= 0:
        return Evaluation(False, None, "INVALID_STRENGTH")
    if price is None or vwap is None or price <= 0 or vwap <= 0:
        return Evaluation(False, None, "INVALID_PRICE_OR_VWAP")

    total = call + put
    call_pct = call / total * 100
    put_pct = put / total * 100
    # Positive Call-minus-Put difference is bullish; negative is bearish.
    # This direction convention is shared with the frontend and test fixtures.
    signed_strength_difference = call_pct - put_pct
    strength_difference = abs(signed_strength_difference)
    strength_threshold = abs(call_put_strength_diff)
    vwap_difference = abs((price - vwap) / price * 100)

    # Return the calculated metrics even when a later confirmation check fails.
    if vwap_signal not in {"BUY", "SELL"} or option_signal not in {"BUY", "SELL"}:
        return Evaluation(False, None, "INVALID_OR_MISSING_SIGNAL", strength_difference, vwap_difference)
    if vwap_signal != option_signal:
        return Evaluation(False, None, "SIGNALS_DISAGREE", strength_difference, vwap_difference)

    # Keep the configured rule strictly greater than the threshold.
    at_strength_threshold = math.isclose(
        strength_difference, strength_threshold, rel_tol=0.0, abs_tol=1e-9
    )
    if strength_difference < strength_threshold or at_strength_threshold:
        return Evaluation(False, None, "STRENGTH_DIFFERENCE_NOT_ABOVE_THRESHOLD", strength_difference, vwap_difference)
    if vwap_difference > abs(vwap_price_difference_buffer):
        return Evaluation(False, None, "VWAP_GAP_NOT_BELOW_THRESHOLD", strength_difference, vwap_difference)

    pcr = _number(row.get("pcr", row.get("PCR")))
    if pcr is None:
        return Evaluation(False, None, "INVALID_OR_MISSING_PCR", strength_difference, vwap_difference)
    if signed_strength_difference > 0 and pcr <= call_pcr:
        return Evaluation(False, None, "PCR_DIRECTION_CONDITION_FAILED", strength_difference, vwap_difference)
    if signed_strength_difference < 0 and pcr >= put_pcr:
        return Evaluation(False, None, "PCR_DIRECTION_CONDITION_FAILED", strength_difference, vwap_difference)

    direction = "BUY" if signed_strength_difference > 0 else "SELL"
    return Evaluation(True, direction, "ELIGIBLE", strength_difference, vwap_difference)
