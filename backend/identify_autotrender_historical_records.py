
import csv
import json
from pathlib import Path

from sqlalchemy import text
from app.db.session import get_engine


OUTPUT_FILE = Path("autotrender_historical_audit.csv")


def parse_json(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def signal_type(signal_data, key):
    value = signal_data.get(key)
    if isinstance(value, dict):
        value = value.get("type")
    return str(value or "").strip().upper()


def main():
    query = text("""
        SELECT
            id,
            occurred_at,
            source,
            underlying,
            state,
            reason,
            signal_timestamp,
            direction,
            signal_data,
            details
        FROM trade_entry_condition_logs
        WHERE source = 'AUTOTRENDER'
        ORDER BY occurred_at, id
    """)

    with get_engine().connect() as connection:
        records = connection.execute(query).mappings().all()

    findings = []

    for row in records:
        signal_data = parse_json(row["signal_data"])
        details = parse_json(row["details"])

        option_signal = signal_type(signal_data, "optionSignal")
        vwap_signal = signal_type(signal_data, "vWapSignal")

        strength_difference = number(
            details.get("strength_difference")
        )
        pcr = number(
            (signal_data.get("pcr") or {}).get("value")
        )
        vwap_gap = number(
            details.get("vwap_difference_percent")
        )

        stored_direction = str(row["direction"] or "").upper()

        expected_direction = None
        if option_signal == vwap_signal and option_signal in ("BUY", "SELL"):
            expected_direction = option_signal

        flags = []

        # A disagreement between the two independent direction signals.
        if option_signal in ("BUY", "SELL") and vwap_signal in ("BUY", "SELL"):
            if option_signal != vwap_signal:
                flags.append("OPTION_VWAP_SIGNAL_DISAGREEMENT")

        # Stored direction conflicts with the agreed signal direction.
        if expected_direction and stored_direction != expected_direction:
            flags.append("STORED_DIRECTION_MISMATCH")

        # Detect cases where the old strength-sign rule would give
        # a different direction from the agreed signals.
        strength_direction = None
        if strength_difference is not None and strength_difference != 0:
            strength_direction = (
                "BUY" if strength_difference > 0 else "SELL"
            )

            if expected_direction and strength_direction != expected_direction:
                flags.append("STRENGTH_SIGN_DIRECTION_CONFLICT")

        if not flags:
            continue

        findings.append({
            "id": row["id"],
            "occurred_at": row["occurred_at"],
            "underlying": row["underlying"],
            "state": row["state"],
            "reason": row["reason"],
            "signal_timestamp": row["signal_timestamp"],
            "stored_direction": stored_direction or None,
            "expected_direction": expected_direction,
            "option_signal": option_signal,
            "vwap_signal": vwap_signal,
            "strength_difference": strength_difference,
            "strength_sign_direction": strength_direction,
            "pcr": pcr,
            "vwap_difference_percent": vwap_gap,
            "flags": "|".join(flags),
        })

    columns = [
        "id",
        "occurred_at",
        "underlying",
        "state",
        "reason",
        "signal_timestamp",
        "stored_direction",
        "expected_direction",
        "option_signal",
        "vwap_signal",
        "strength_difference",
        "strength_sign_direction",
        "pcr",
        "vwap_difference_percent",
        "flags",
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(findings)

    print("\nAUTOTRENDER HISTORICAL DATA AUDIT")
    print("--------------------------------")
    print(f"Total records scanned : {len(records)}")
    print(f"Potentially affected  : {len(findings)}")
    print(f"Report generated      : {OUTPUT_FILE.resolve()}")

    flag_counts = {}
    for item in findings:
        for flag in item["flags"].split("|"):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    print("\nFindings by category:")
    for flag, count in sorted(flag_counts.items()):
        print(f"  {flag}: {count}")

    print("\nFirst 20 flagged records:")
    for item in findings[:20]:
        print(
            f"ID={item['id']} | "
            f"{item['underlying']} | "
            f"DB={item['occurred_at']} | "
            f"Signal={item['signal_timestamp']} | "
            f"Stored={item['stored_direction']} | "
            f"Expected={item['expected_direction']} | "
            f"Flags={item['flags']}"
        )


if __name__ == "__main__":
    main()
