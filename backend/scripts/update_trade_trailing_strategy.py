"""
Update the trailing-strategy snapshot on every persisted trade (open and closed).

Run from the backend directory:
  python scripts\\update_trade_trailing_strategy.py          # dry run
  python scripts\\update_trade_trailing_strategy.py --apply  # write changes

The script uses the same backend/.env and SQLAlchemy database configuration as the app.
It updates payload.trailing_strategy and payload.target_stop_loss_settings to match
the current ui.targetStopLossStrategy and its related target/stop settings. It does
not change the trade's lifecycle status, actual strategy name, trailing-active state,
prices, P&L, or timestamps.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine


SETTING_KEYS = (
    "targetStopLossStrategy",
    "defaultTargetPercent",
    "defaultTargetType",
    "trailingDistance",
    "trailingDistanceType",
    "trailingTarget",
    "trailingTargetType",
)
DEFAULTS: dict[str, Any] = {
    "targetStopLossStrategy": "M-TTTS",
    "defaultTargetPercent": 10,
    "defaultTargetType": "PERCENTAGE",
    "trailingDistance": 5,
    "trailingDistanceType": "POINTS",
    "trailingTarget": 10,
    "trailingTargetType": "POINTS",
}
ALLOWED_STRATEGIES = {"L-FT-FSL", "M-FT-TSL", "M-TTTS", "H-PT-TSL"}


def decode_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def load_current_settings(connection) -> dict[str, Any]:
    keys = [f"ui.{key}" for key in SETTING_KEYS]
    params = {f"k{i}": key for i, key in enumerate(keys)}
    placeholders = ", ".join(f":k{i}" for i in range(len(keys)))
    rows = connection.execute(
        text(f"SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN ({placeholders})"),
        params,
    ).mappings().all()

    settings = dict(DEFAULTS)
    for row in rows:
        key = str(row["setting_key"])[3:]
        value = row["setting_value"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
        settings[key] = value

    strategy = str(settings["targetStopLossStrategy"])
    if strategy not in ALLOWED_STRATEGIES:
        raise ValueError(
            f"Unsupported ui.targetStopLossStrategy value {strategy!r}; "
            f"expected one of {sorted(ALLOWED_STRATEGIES)}. No trades were changed."
        )
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit updates. Without this flag, the script only previews the changes.",
    )
    args = parser.parse_args()

    engine = get_engine()
    with engine.begin() as connection:
        settings = load_current_settings(connection)
        strategy = str(settings["targetStopLossStrategy"])
        rows = connection.execute(
            text("SELECT trade_id, status, payload FROM trade_records ORDER BY created_at, trade_id")
        ).mappings().all()

        changes: list[tuple[str, str | None, str]] = []
        backup: list[dict[str, Any]] = []
        for row in rows:
            payload = decode_json(row["payload"])
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Trade {row['trade_id']} has a non-object payload; aborting before any updates."
                )

            backup.append({
                "trade_id": str(row["trade_id"]),
                "status": row["status"],
                "payload": payload,
            })

            current_nested = payload.get("target_stop_loss_settings")
            if not isinstance(current_nested, dict):
                current_nested = {}
            desired_nested = {**current_nested, **settings}
            desired_nested["targetStopLossStrategy"] = strategy

            if (
                payload.get("trailing_strategy") != strategy
                or payload.get("target_stop_loss_settings") != desired_nested
            ):
                updated_payload = {
                    **payload,
                    "trailing_strategy": strategy,
                    "target_stop_loss_settings": desired_nested,
                }
                changes.append((
                    str(row["trade_id"]),
                    row["status"],
                    json.dumps(updated_payload, separators=(",", ":"), ensure_ascii=False),
                ))

        print(f"Current default trailing strategy: {strategy}")
        print(f"Trades found in database: {len(rows)}")
        print(f"Trades needing update: {len(changes)}")
        print("Status scope: all persisted trades (open and closed).")
        if not args.apply:
            print("\nDRY RUN ONLY — no database changes made.")
            print("Review the counts, then rerun with --apply to commit.")
            return 0

        if not changes:
            print("All trade snapshots already match the current settings. Nothing to update.")
            return 0

        backup_dir = Path("backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (
            "trade_payloads_before_trailing_strategy_update_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".json"
        )
        backup_path.write_text(
            json.dumps(backup, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"Backup written to: {backup_path.resolve()}")

        for trade_id, _status, payload_json in changes:
            connection.execute(
                text("UPDATE trade_records SET payload = :payload WHERE trade_id = :trade_id"),
                {"payload": payload_json, "trade_id": trade_id},
            )

        print(f"Committed updates for {len(changes)} trades.")
        print("Trade status, actual strategy column, timestamps, prices, and P&L were not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
