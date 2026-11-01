
import csv
import json
from pathlib import Path
from collections import Counter

from sqlalchemy import text
from app.db.session import get_engine


OUTPUT = Path("autotrender_direction_candidates.csv")


def parse_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return {}


def number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def nested_value(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def main():
    query = text("""
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
        ORDER BY occurred_at, id
    """)

    with get_engine().connect() as conn:
        records = conn.execute(query).mappings().all()

    findings = []
    counts = Counter()

    for row in records:
        signal = parse_json(row["signal_data"])
        details = parse_json(row["details"])

        option_signal = str(
            nested_value(signal, "optionSignal", "type") or ""
        ).strip().upper()

        vwap_signal = str(
            nested_value(signal, "vWapSignal", "value") or ""
        ).strip().upper()

        stored_direction = str(row["direction"] or "").strip().upper()

        strength = number(details.get("strength_difference"))
        pcr = number(nested_value(signal, "pcr", "value"))
        price = number(nested_value(signal, "price", "value"))
        vwap = number(nested_value(signal, "vWap", "value"))
        vwap_gap = number(details.get("vwap_difference_percent"))

        expected_direction = None
        if (
            option_signal in ("BUY", "SELL")
            and option_signal == vwap_signal
        ):
            expected_direction = option_signal

        # The old signed-strength direction rule.
        strength_direction = None
        if strength is not None and strength != 0:
            strength_direction = "BUY" if strength > 0 else "SELL"

        flags = []

        if (
            expected_direction
            and strength_direction
            and expected_direction != strength_direction
        ):
            flags.append("STRENGTH_SIGN_CONFLICTS_WITH_SIGNALS")

        if (
            expected_direction
            and stored_direction in ("BUY", "SELL")
            and stored_direction != expected_direction
        ):
            flags.append("STORED_DIRECTION_CONFLICTS_WITH_SIGNALS")

        if not flags:
            continue

        counts.update(flags)

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
            "strength_difference": strength,
            "strength_sign_direction": strength_direction,
            "pcr": pcr,
            "price": price,
            "vwap": vwap,
            "vwap_difference_percent": vwap_gap,
            "flags": "|".join(flags),
        })

    columns = [
        "id", "occurred_at", "underlying", "state", "reason",
        "signal_timestamp", "stored_direction", "expected_direction",
        "option_signal", "vwap_signal", "strength_difference",
        "strength_sign_direction", "pcr", "price", "vwap",
        "vwap_difference_percent", "flags"
    ]

    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(findings)

    print("\nAUTOTRENDER DIRECTION HISTORY AUDIT")
    print("-----------------------------------")
    print("Records scanned:", len(records))
    print("Direction candidates:", len(findings))
    print("CSV:", OUTPUT.resolve())

    print("\nFindings by category:")
    for name, count in counts.items():
        print(f"{name}: {count}")

    print("\nCandidates by underlying:")
    for underlying, count in Counter(
        item["underlying"] for item in findings
    ).items():
        print(f"{underlying}: {count}")

    print("\nFirst 20 candidates:")
    for item in findings[:20]:
        print(
            f"ID={item['id']} | "
            f"{item['underlying']} | "
            f"DB={item['occurred_at']} | "
            f"Signal={item['signal_timestamp']} | "
            f"Expected={item['expected_direction']} | "
            f"Strength={item['strength_difference']} | "
            f"Flags={item['flags']}"
        )


if __name__ == "__main__":
    main()
