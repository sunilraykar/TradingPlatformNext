"""Fetch a live APIPLATFORM option chain and print a compact response sample.

Run from the backend directory:
    .venv\\Scripts\\python.exe scripts\\test_apiplatform_option_chain.py --symbol NIFTY --expiry YYYY-MM-DD --lot-size 65

The script uses the shared APIPLATFORM auth/session and never prints credentials or JWTs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.providers.apiplatform.client import APIPLATFORMClientError
from app.providers.apiplatform.auth import APIPLATFORMAuthError
from app.providers.apiplatform.registry import (
    get_apiplatform_client,
    shutdown_apiplatform,
)


def _sample(value: Any, depth: int = 3) -> Any:
    """Return a bounded structure sample without dumping large option-chain arrays."""
    if isinstance(value, dict):
        if depth <= 0:
            return {"type": "object", "keys": list(value)[:40]}
        return {
            key: _sample(item, depth - 1)
            for key, item in list(value.items())[:60]
        }
    if isinstance(value, list):
        result: dict[str, Any] = {"type": "array", "count": len(value)}
        if value:
            result["first_item"] = _sample(value[0], depth - 1)
        return result
    if isinstance(value, str):
        return value if len(value) <= 160 else value[:157] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="APIPLATFORM symbol, e.g. NIFTY")
    parser.add_argument("--expiry", required=True, help="Expiry in YYYY-MM-DD format")
    parser.add_argument("--lot-size", required=True, type=int, help="Contract lot size")
    parser.add_argument(
        "--save-json",
        help="Optional path to save the complete response for offline inspection",
    )
    args = parser.parse_args()

    try:
        client = get_apiplatform_client()
        print("APIPLATFORM session:", "authenticated" if client.auth.status()["authenticated"] else "login required")
        payload = client.fetch_option_chain(
            symbol=args.symbol,
            expiry=args.expiry,
            lot_size=args.lot_size,
        )
        print("\nLIVE OPTION-CHAIN REQUEST: SUCCESS")
        print("API base URL:", client.base_url.rstrip("/"))
        print("Endpoint: GET /optionChain/fetch-data")
        print("Request:", json.dumps({
            "symbol": args.symbol,
            "expiry": args.expiry,
            "lotSize": args.lot_size,
        }))
        print("\nRESPONSE STRUCTURE SAMPLE:")
        print(json.dumps(_sample(payload), indent=2, ensure_ascii=False, default=str))

        if args.save_json:
            output_path = Path(args.save_json).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print(f"\nFull response saved to: {output_path}")
        return 0
    except (APIPLATFORMAuthError, APIPLATFORMClientError) as exc:
        print(f"APIPLATFORM request failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Diagnostic failed: {exc}", file=sys.stderr)
        return 1
    finally:
        shutdown_apiplatform()


if __name__ == "__main__":
    raise SystemExit(main())
