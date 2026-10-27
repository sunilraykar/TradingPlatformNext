"""One-time, backup-protected repair for Trade Terminal records.

Run from backend:
  python scripts/repair_trade_terminal_records.py          # dry run
  python scripts/repair_trade_terminal_records.py --apply  # write repairs

Repairs recognizable option underlyings from the contract symbol. For CLOSED
trades, it backfills only zero/missing realized P&L when recorded entry/exit
prices and original quantity support a non-zero calculation. It never invents
prices or overwrites non-zero realized P&L.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine

PREFIXES = (
    ("FINNIFTY", "FINNIFTY"),
    ("BANKNIFTY", "BANKNIFTY"),
    ("SENSEX", "SENSEX"),
    ("BANKEX", "BANKEX"),
    ("NIFTY", "NIFTY"),
)


def as_positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def infer_underlying(symbol: Any) -> str | None:
    symbol = str(symbol or "").strip().upper()
    for prefix, underlying in PREFIXES:
        if symbol.startswith(prefix):
            return underlying
    return None


def parse_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else None
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def repair(payload: dict[str, Any], column_underlying: Any) -> tuple[dict[str, Any], str | None, list[str]]:
    result = dict(payload)
    changes: list[str] = []
    inferred = infer_underlying(result.get("instrument_symbol") or result.get("tradingsymbol"))
    if inferred:
        old = str(result.get("underlying") or column_underlying or "").strip().upper()
        if old != inferred:
            changes.append(f"underlying {old or '(blank)'} -> {inferred}")
        result["underlying"] = inferred

    if str(result.get("status") or "").upper() == "CLOSED":
        try:
            existing_pnl = float(result.get("realized_pnl") or 0)
        except (TypeError, ValueError):
            existing_pnl = 0.0
        entry = as_positive_float(result.get("average_entry_price"))
        if entry is None:
            entry = as_positive_float(result.get("entry_price"))
        exit_price = as_positive_float(result.get("average_exit_price"))
        if exit_price is None:
            exit_price = as_positive_float(result.get("exit_price"))
        try:
            quantity = float(result.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        direction = str(result.get("direction") or "BUY").upper()
        if existing_pnl == 0 and entry is not None and exit_price is not None and quantity > 0 and direction in {"BUY", "SELL"}:
            factor = -1 if direction == "SELL" else 1
            calculated = round((exit_price - entry) * quantity * factor, 8)
            if calculated != 0:
                result["realized_pnl"] = calculated
                changes.append(f"realized_pnl 0 -> {calculated:g}")

    return result, inferred, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit changes; default is dry-run")
    args = parser.parse_args()

    engine = get_engine()
    updates: list[tuple[str, dict[str, Any], str | None, list[str], Any]] = []
    skipped = 0
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT trade_id, underlying, payload FROM trade_records ORDER BY updated_at DESC"
        )).mappings().all()

        for row in rows:
            payload = parse_payload(row["payload"])
            if payload is None:
                skipped += 1
                print(f"SKIP {row['trade_id']}: payload is not a JSON object")
                continue
            fixed, inferred, changes = repair(payload, row["underlying"])
            if changes:
                updates.append((str(row["trade_id"]), fixed, inferred, changes, row["payload"]))
                prefix = "APPLY" if args.apply else "DRY-RUN"
                print(f"{prefix} {row['trade_id']}: " + "; ".join(changes))

        if args.apply and updates:
            backup_path = Path.cwd() / (
                "trade_records_repair_backup_"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
            )
            backup_path.write_text(json.dumps([
                {"trade_id": trade_id, "payload": original}
                for trade_id, _, _, _, original in updates
            ], indent=2, default=str), encoding="utf-8")

            for trade_id, fixed, inferred, _, _ in updates:
                conn.execute(text(
                    "UPDATE trade_records SET underlying = COALESCE(:underlying, underlying), "
                    "payload = :payload WHERE trade_id = :trade_id"
                ), {
                    "trade_id": trade_id,
                    "underlying": inferred,
                    "payload": json.dumps(fixed, separators=(",", ":"), default=str),
                })
            print(f"Backup written to: {backup_path}")

    action = "updated" if args.apply else "would be updated"
    print(f"Scanned {len(rows)} records; {len(updates)} {action}; {skipped} skipped.")
    if not args.apply:
        print("Dry run only. Review the proposed changes, then rerun with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
