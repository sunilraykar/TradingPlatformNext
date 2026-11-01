
import json
from sqlalchemy import text
from app.db.session import get_engine


def show(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def main():
    with get_engine().connect() as conn:

        print("\n========== SOURCE / STATE / REASON COUNTS ==========\n")

        counts = conn.execute(text("""
            SELECT source, state, reason, COUNT(*) AS total
            FROM trade_entry_condition_logs
            GROUP BY source, state, reason
            ORDER BY source, state, reason
        """)).mappings().all()

        for row in counts:
            print(dict(row))

        print("\n========== SAMPLE RECORDS ==========\n")

        samples = conn.execute(text("""
            SELECT *
            FROM trade_entry_condition_logs
            WHERE source = 'AUTOTRENDER'
            ORDER BY occurred_at DESC
            LIMIT 15
        """)).mappings().all()

        for row in samples:
            print("\n" + "=" * 90)
            for key, value in row.items():
                if key in ("signal_data", "details"):
                    print(f"\n{key}:\n{show(value)}")
                else:
                    print(f"{key}: {value}")

        print("\n========== JSON FIELD TYPES ==========\n")

        for column in ("signal_data", "details"):
            print(f"\n{column}:")
            for row in samples:
                value = row.get(column)
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                print(type(value).__name__)
                if isinstance(value, dict):
                    print("Keys:", list(value.keys()))


if __name__ == "__main__":
    main()
