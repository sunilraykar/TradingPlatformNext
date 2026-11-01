
"""
Historical AutoTrender Audit
============================

READ-ONLY: This script never updates or deletes database records.

Database:
    trade_entry_condition_logs

Checks all AUTOTRENDER records across all underlyings.

Approved rules:
    1. abs(CallStrength - PutStrength) > 10
    2. VWAP Signal and Option Signal must agree
    3. BUY: PCR > 1.40
    4. SELL: PCR < 0.85
    5. VWAP gap <= 0.10%

Direction is determined by the agreeing signals.
Strength difference sign does NOT determine direction.

Outputs:
    historical_enter_audit.csv
    historical_false_wait_candidates.csv
"""

import csv
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text
from app.db.session import get_engine


# ============================================================
# CONFIGURATION
# ============================================================

STRENGTH_THRESHOLD = Decimal("10")
CALL_PCR_THRESHOLD = Decimal("1.40")
PUT_PCR_THRESHOLD = Decimal("0.85")
VWAP_BUFFER_PERCENT = Decimal("0.10")

BACKEND_DIR = Path(__file__).resolve().parent

OUTPUT_ALL = BACKEND_DIR / "historical_enter_audit.csv"
OUTPUT_CANDIDATES = BACKEND_DIR / "historical_false_wait_candidates.csv"


# ============================================================
# DATABASE CONNECTION
# ============================================================

engine = get_engine()

if engine is None:
    raise RuntimeError(
        "Could not initialize the TradingPlatformNext database engine."
    )


# ============================================================
# HELPERS
# ============================================================

def parse_json(value):
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except (ValueError, TypeError):
            return {}

    return {}


def to_decimal(value):
    if value is None:
        return None

    try:
        cleaned = re.sub(r"[,\s₹%]", "", str(value).strip())

        if not cleaned or cleaned.lower() in ("none", "null", "nan"):
            return None

        return Decimal(cleaned)

    except (InvalidOperation, ValueError):
        return None


def get_nested(data, *keys):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def get_signal(data, key):
    value = get_nested(data, key, "value")

    if value is None:
        return None

    return str(value).strip().upper()


# ============================================================
# EVALUATE ONE RECORD
# ============================================================

def evaluate_record(row):

    signal_data = parse_json(row["signal_data"])
    details = parse_json(row["details"])

    option_signal = get_signal(signal_data, "optionSignal")
    vwap_signal = get_signal(signal_data, "vWapSignal")

    pcr = to_decimal(
        get_nested(signal_data, "pcr", "value")
    )

    price = to_decimal(
        get_nested(signal_data, "price", "value")
    )

    vwap = to_decimal(
        get_nested(signal_data, "vWap", "value")
    )

    strength_difference = to_decimal(
        details.get("strength_difference")
    )

    if strength_difference is None:
        strength_difference = to_decimal(
            get_nested(signal_data, "diff", "value")
        )

    reasons = []

    # --------------------------------------------------------
    # 1. STRENGTH DIFFERENCE
    # --------------------------------------------------------

    if strength_difference is None:
        reasons.append("MISSING_STRENGTH_DIFFERENCE")

    elif abs(strength_difference) <= STRENGTH_THRESHOLD:
        reasons.append("STRENGTH_DIFFERENCE_NOT_ABOVE_THRESHOLD")

    # --------------------------------------------------------
    # 2. SIGNAL AGREEMENT
    # --------------------------------------------------------

    if option_signal not in ("BUY", "SELL"):
        reasons.append("INVALID_OPTION_SIGNAL")

    if vwap_signal not in ("BUY", "SELL"):
        reasons.append("INVALID_VWAP_SIGNAL")

    if (
        option_signal in ("BUY", "SELL")
        and vwap_signal in ("BUY", "SELL")
        and option_signal != vwap_signal
    ):
        reasons.append("SIGNALS_DISAGREE")

    # --------------------------------------------------------
    # 3. PCR CONFIRMATION
    # --------------------------------------------------------

    if pcr is None:
        reasons.append("MISSING_PCR")

    elif option_signal == "BUY":
        if pcr <= CALL_PCR_THRESHOLD:
            reasons.append("PCR_BUY_CONDITION_FAILED")

    elif option_signal == "SELL":
        if pcr >= PUT_PCR_THRESHOLD:
            reasons.append("PCR_SELL_CONDITION_FAILED")

    # --------------------------------------------------------
    # 4. PRICE AND VWAP VALIDATION
    # --------------------------------------------------------

    if price is None or price <= 0:
        reasons.append("INVALID_PRICE")

    if vwap is None or vwap <= 0:
        reasons.append("INVALID_VWAP")

    vwap_gap = None

    if (
        price is not None
        and price > 0
        and vwap is not None
        and vwap > 0
    ):
        vwap_gap = (
            abs((price - vwap) / price) * Decimal("100")
        )

        if vwap_gap > VWAP_BUFFER_PERCENT:
            reasons.append("VWAP_GAP_ABOVE_THRESHOLD")

    # --------------------------------------------------------
    # 5. EXPECTED ENTER VALUE
    # --------------------------------------------------------

    if reasons:
        expected_enter = "WAIT"

    elif option_signal == "BUY" and vwap_signal == "BUY":
        expected_enter = "BUY"

    elif option_signal == "SELL" and vwap_signal == "SELL":
        expected_enter = "SELL"

    else:
        expected_enter = "WAIT"

    # --------------------------------------------------------
    # 6. POSSIBLE FALSE-WAIT CANDIDATE
    # --------------------------------------------------------

    logged_state = str(row["state"] or "").upper()

    # These are rejected evaluation records, not confirmed
    # historical UI Enter values.
    false_wait_candidate = (
        expected_enter in ("BUY", "SELL")
        and logged_state == "REJECTED"
    )

    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "underlying": row["underlying"],
        "signal_timestamp": row["signal_timestamp"],
        "logged_state": row["state"],
        "logged_reason": row["reason"],
        "logged_direction": row["direction"],
        "expected_enter": expected_enter,
        "false_wait_candidate": false_wait_candidate,
        "option_signal": option_signal,
        "vwap_signal": vwap_signal,
        "strength_difference": (
            str(strength_difference)
            if strength_difference is not None else ""
        ),
        "pcr": str(pcr) if pcr is not None else "",
        "price": str(price) if price is not None else "",
        "vwap": str(vwap) if vwap is not None else "",
        "vwap_gap_percent": (
            str(vwap_gap) if vwap_gap is not None else ""
        ),
        "evaluation_reasons": "; ".join(reasons),
    }


# ============================================================
# FETCH ALL AUTOTRENDER RECORDS
# ============================================================

QUERY = text("""
    SELECT
        id,
        occurred_at,
        underlying,
        state,
        reason,
        signal_timestamp,
        direction,
        signal_data,
        details
    FROM trade_entry_condition_logs
    WHERE source = 'AUTOTRENDER'
      AND signal_data IS NOT NULL
    ORDER BY occurred_at ASC, id ASC
""")


# ============================================================
# CSV EXPORT
# ============================================================

def export_csv(path, rows):

    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = [
            "id",
            "occurred_at",
            "underlying",
            "signal_timestamp",
            "logged_state",
            "logged_reason",
            "logged_direction",
            "expected_enter",
            "false_wait_candidate",
            "option_signal",
            "vwap_signal",
            "strength_difference",
            "pcr",
            "price",
            "vwap",
            "vwap_gap_percent",
            "evaluation_reasons",
        ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("HISTORICAL AUTOTRENDER AUDIT")
    print("=" * 70)

    print(f"Strength threshold : {STRENGTH_THRESHOLD}")
    print(f"BUY PCR threshold  : {CALL_PCR_THRESHOLD}")
    print(f"SELL PCR threshold : {PUT_PCR_THRESHOLD}")
    print(f"VWAP buffer        : {VWAP_BUFFER_PERCENT}%")
    print()

    # Read-only query
    with engine.connect() as connection:
        records = connection.execute(QUERY).mappings().all()

    print(f"Records loaded: {len(records)}")

    audit_rows = []

    for row in records:
        try:
            audit_rows.append(evaluate_record(row))

        except Exception as exc:
            print(
                f"Error evaluating record {row['id']}: {exc}"
            )

    # Export complete audit
    export_csv(OUTPUT_ALL, audit_rows)

    # Export possible false-WAIT candidates
    candidates = [
        row for row in audit_rows
        if row["false_wait_candidate"]
    ]

    export_csv(OUTPUT_CANDIDATES, candidates)

    # --------------------------------------------------------
    # SUMMARY BY UNDERLYING
    # --------------------------------------------------------

    summary = {}

    for row in audit_rows:

        underlying = row["underlying"] or "UNKNOWN"

        if underlying not in summary:
            summary[underlying] = {
                "total": 0,
                "BUY": 0,
                "SELL": 0,
                "WAIT": 0,
                "false_wait": 0,
            }

        item = summary[underlying]

        item["total"] += 1
        item[row["expected_enter"]] += 1

        if row["false_wait_candidate"]:
            item["false_wait"] += 1

    print()
    print("-" * 70)
    print("AUDIT SUMMARY")
    print("-" * 70)

    for underlying in sorted(summary):

        item = summary[underlying]

        print(
            f"{underlying:15} "
            f"Total={item['total']:7} "
            f"BUY={item['BUY']:7} "
            f"SELL={item['SELL']:7} "
            f"WAIT={item['WAIT']:7} "
            f"False-WAIT candidates={item['false_wait']:6}"
        )

    print()
    print(f"Total records audited : {len(audit_rows)}")
    print(f"False-WAIT candidates : {len(candidates)}")
    print()
    print(f"Full audit: {OUTPUT_ALL}")
    print(f"Candidates: {OUTPUT_CANDIDATES}")
    print()
    print("Database changes: NONE (READ ONLY)")
    print("=" * 70)


if __name__ == "__main__":
    main()
