"""Read-only audit of persisted AutoTrender historical UI Enter values.

Run from backend:
    python ..\\scripts\\audit_autotrender_historical_enter.py

Thresholds are loaded from the same ui.* records used by the application
Settings. The script does not substitute its own threshold defaults and never
modifies the database.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import text
from app.db.session import get_engine


SETTING_KEYS = (
    "callPutStrengthDiff",
    "callPcr",
    "putPcr",
    "vwapPriceDifferenceBuffer",
)


def num(value: Any) -> float | None:
    if isinstance(value, dict):
        value = next((value.get(k) for k in ("value", "price", "ltp", "close") if value.get(k) not in (None, "")), None)
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def sig(value: Any) -> str:
    if isinstance(value, dict):
        value = next((value.get(k) for k in ("type", "value", "signal", "label", "status") if value.get(k) not in (None, "")), "")
    return str(value or "").strip().upper()


def raw_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def decode_setting(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def read_thresholds(connection) -> dict[str, float]:
    placeholders = ",".join(f":key{i}" for i in range(len(SETTING_KEYS)))
    rows = connection.execute(
        text(
            "SELECT setting_key, setting_value FROM app_settings "
            f"WHERE setting_key IN ({placeholders})"
        ),
        {f"key{i}": "ui." + key for i, key in enumerate(SETTING_KEYS)},
    ).mappings().all()

    saved = {str(row["setting_key"])[3:]: decode_setting(row["setting_value"]) for row in rows}
    missing = [key for key in SETTING_KEYS if key not in saved]
    if missing:
        raise RuntimeError(
            "Cannot run audit: these Settings values are missing from app_settings: "
            + ", ".join(missing)
            + ". Save them in the application Settings and rerun."
        )

    thresholds: dict[str, float] = {}
    for key in SETTING_KEYS:
        value = num(saved[key])
        if value is None:
            raise RuntimeError(
                f"Cannot run audit: Settings value '{key}' is not a valid number: {saved[key]!r}"
            )
        thresholds[key] = abs(value) if key in {"callPutStrengthDiff", "vwapPriceDifferenceBuffer"} else value
    return thresholds


def main() -> None:
    sql = text("""
        SELECT row_id, trading_date, index_name, interval_minutes, row_time,
               call_value, put_value, pcr_value, price_value, vwap_value,
               vwap_signal, option_signal, call_strength, put_strength,
               strength_diff, enter_signal, raw_row
        FROM autotrender_intraday_rows
        ORDER BY trading_date, index_name, interval_minutes, row_time, row_id
    """)
    with get_engine().connect() as conn:
        thresholds = read_thresholds(conn)
        records = conn.execute(sql).mappings().all()

    strength_threshold = thresholds["callPutStrengthDiff"]
    call_pcr_threshold = thresholds["callPcr"]
    put_pcr_threshold = thresholds["putPcr"]
    vwap_buffer_threshold = thresholds["vwapPriceDifferenceBuffer"]

    fields = [
        "row_id", "trading_date", "index", "interval_minutes", "time",
        "historical_enter", "expected_enter", "classification", "reason",
        "call_strength", "put_strength", "strength_difference", "pcr",
        "price", "vwap", "vwap_buffer_percent", "vwap_signal", "option_signal",
    ]
    audit: list[dict[str, Any]] = []
    for db in records:
        raw = raw_dict(db["raw_row"])
        call = num(db["call_strength"])
        put = num(db["put_strength"])
        if call is None or put is None:
            raw_call = num(db["call_value"] if db["call_value"] is not None else raw.get("call"))
            raw_put = num(db["put_value"] if db["put_value"] is not None else raw.get("put"))
            if raw_call is not None and raw_put is not None and raw_call + raw_put > 0:
                call = raw_call / (raw_call + raw_put) * 100
                put = raw_put / (raw_call + raw_put) * 100

        strength = abs(call - put) if call is not None and put is not None else None
        price = num(db["price_value"] if db["price_value"] is not None else raw.get("price"))
        vwap = num(db["vwap_value"] if db["vwap_value"] is not None else raw.get("vwap", raw.get("vWap")))
        pcr = num(db["pcr_value"] if db["pcr_value"] is not None else raw.get("pcr"))
        vs = sig(db["vwap_signal"] or raw.get("vwapSignal", raw.get("vWapSignal")))
        osig = sig(db["option_signal"] or raw.get("optionSignal"))
        buffer = abs((price - vwap) / price) * 100 if price is not None and vwap is not None and price > 0 and vwap > 0 else None

        expected, reason = "WAIT", "ELIGIBLE"
        if strength is None:
            reason = "INVALID_STRENGTH"
        elif price is None or vwap is None or price <= 0 or vwap <= 0:
            reason = "INVALID_PRICE_OR_VWAP"
        elif strength <= strength_threshold:
            reason = "STRENGTH_DIFFERENCE_NOT_ABOVE_THRESHOLD"
        elif buffer is None or buffer > vwap_buffer_threshold:
            reason = "VWAP_GAP_ABOVE_THRESHOLD"
        elif vs not in {"BUY", "SELL"} or osig not in {"BUY", "SELL"}:
            reason = "INVALID_OR_MISSING_SIGNAL"
        elif vs != osig:
            reason = "SIGNALS_DISAGREE"
        elif pcr is None:
            reason = "INVALID_OR_MISSING_PCR"
        elif vs == "BUY" and pcr <= call_pcr_threshold:
            reason = "PCR_DIRECTION_CONDITION_FAILED"
        elif vs == "SELL" and pcr >= put_pcr_threshold:
            reason = "PCR_DIRECTION_CONDITION_FAILED"
        else:
            expected = vs

        historical = str(db["enter_signal"] or "").strip().upper() or "MISSING"
        if historical == expected:
            classification = "MATCH"
        elif historical == "WAIT" and expected in {"BUY", "SELL"}:
            classification = "FALSE_WAIT"
        elif historical in {"BUY", "SELL"} and expected == "WAIT":
            classification = "FALSE_BUY_SELL"
        elif historical in {"BUY", "SELL"} and expected in {"BUY", "SELL"}:
            classification = "FALSE_DIRECTION"
        else:
            classification = "MISSING_HISTORICAL_ENTER"

        audit.append({
            "row_id": db["row_id"], "trading_date": db["trading_date"],
            "index": db["index_name"], "interval_minutes": db["interval_minutes"],
            "time": db["row_time"], "historical_enter": historical,
            "expected_enter": expected, "classification": classification,
            "reason": reason, "call_strength": call, "put_strength": put,
            "strength_difference": strength, "pcr": pcr, "price": price,
            "vwap": vwap, "vwap_buffer_percent": buffer,
            "vwap_signal": vs, "option_signal": osig,
        })

    out = Path(__file__).resolve().parents[1] / "backend"
    all_file = out / "autotrender_historical_enter_audit.csv"
    mismatch_file = out / "autotrender_historical_enter_mismatches.csv"
    for path, data in ((all_file, audit), (mismatch_file, [r for r in audit if r["classification"] != "MATCH"])):
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)

    counts = {key: sum(r["classification"] == key for r in audit) for key in (
        "MATCH", "FALSE_WAIT", "FALSE_BUY_SELL", "FALSE_DIRECTION", "MISSING_HISTORICAL_ENTER"
    )}
    print("READ-ONLY AUTOTRENDER HISTORICAL ENTER AUDIT")
    print("Thresholds loaded from saved application Settings:")
    print(f"  Call-Put Strength Difference: {strength_threshold:g}")
    print(f"  Call PCR: {call_pcr_threshold:g}")
    print(f"  Put PCR: {put_pcr_threshold:g}")
    print(f"  VWAP-Price Difference Buffer (%): {vwap_buffer_threshold:g}")
    print(f"Rows scanned: {len(audit)}")
    for key, value in counts.items():
        print(f"{key}: {value}")
    print(f"Full audit: {all_file}")
    print(f"Mismatches: {mismatch_file}")
    print("Database was NOT modified.")


if __name__ == "__main__":
    main()
