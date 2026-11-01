
import json
from sqlalchemy import text
from app.db.session import get_engine


def display(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def main():
    with get_engine().connect() as conn:
        print("\n=== SIGNAL DATA COUNTS ===")

        counts = conn.execute(text("""
            SELECT
                state,
                reason,
                COUNT(*) AS total,
                MIN(occurred_at) AS first_record,
                MAX(occurred_at) AS last_record,
                MIN(signal_timestamp) AS first_signal,
                MAX(signal_timestamp) AS last_signal
            FROM trade_entry_condition_logs
            WHERE source = 'AUTOTRENDER'
              AND signal_data IS NOT NULL
            GROUP BY state, reason
            ORDER BY state, reason
        """)).mappings().all()

        for row in counts:
            print(dict(row))

        print("\n=== SAMPLE SIGNAL RECORDS ===")

        samples = conn.execute(text("""
            SELECT *
            FROM (
                SELECT
                    t.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY state, reason
                        ORDER BY occurred_at
                    ) AS rn
                FROM trade_entry_condition_logs t
                WHERE source = 'AUTOTRENDER'
                  AND signal_data IS NOT NULL
            ) ranked
            WHERE rn <= 3
            ORDER BY state, reason, occurred_at
        """)).mappings().all()

        for row in samples:
            print("\n" + "=" * 100)
            for key, value in row.items():
                if key == "rn":
                    continue
                if key in ("signal_data", "details"):
                    print(f"\n{key}:\n{display(value)}")
                else:
                    print(f"{key}: {value}")


if __name__ == "__main__":
    main()
