"""Hide closed PAPER trades with realized losses at or below the configured threshold.

This is non-destructive: full trade records and lifecycle events remain in MySQL.
The script backs up matching payloads before setting hidden_from_terminal=true.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.db.session import get_engine

THRESHOLD = -5000.0
BACKUP_DIR = Path(__file__).resolve().parents[1] / "backups"


def realized_pnl(payload: dict) -> float | None:
    for key in ("net_realized_pnl", "gross_realized_pnl", "realized_pnl"):
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def main() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT trade_id, execution_mode, status, payload
            FROM trade_records
            WHERE execution_mode = 'PAPER'
              AND UPPER(status) = 'CLOSED'
        """)).mappings().all()

    matches = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or payload.get("hidden_from_terminal"):
            continue
        pnl = realized_pnl(payload)
        if pnl is not None and pnl <= THRESHOLD:
            matches.append({
                "trade_id": row["trade_id"],
                "execution_mode": row["execution_mode"],
                "status": row["status"],
                "realized_pnl": pnl,
                "payload": payload,
            })

    print(f"Rule: CLOSED PAPER trades with realized P&L <= INR {THRESHOLD:,.2f}")
    print(f"Matching records: {len(matches)}")
    for item in matches:
        print(f"  {item['trade_id']} | INR {item['realized_pnl']:,.2f}")

    if not matches:
        print("Nothing to hide.")
        return 0

    if "--yes" not in sys.argv:
        answer = input("Hide these records from Trade Terminal? Records will NOT be deleted. Type YES: ")
        if answer.strip() != "YES":
            print("Cancelled; no database changes made.")
            return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"hidden_loss_trades_{stamp}.json"
    backup_path.write_text(
        json.dumps(matches, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Backup written: {backup_path}")

    with engine.begin() as conn:
        for item in matches:
            conn.execute(text("""
                UPDATE trade_records
                SET payload = JSON_SET(payload, '$.hidden_from_terminal', TRUE),
                    version = version + 1,
                    updated_at = CURRENT_TIMESTAMP(6),
                    persisted_at = CURRENT_TIMESTAMP(6)
                WHERE trade_id = :trade_id
                  AND execution_mode = 'PAPER'
                  AND UPPER(status) = 'CLOSED'
            """), {"trade_id": item["trade_id"]})

    print(f"Hidden {len(matches)} trade(s). No records or lifecycle events were deleted.")
    print("Restart the backend to reload the updated trade payloads into its cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
