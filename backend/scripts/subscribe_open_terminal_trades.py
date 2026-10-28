"""Re-register Kite WebSocket subscriptions for all active Trade Terminal trades.

Run from the backend directory while the FastAPI backend is running:
    python scripts/subscribe_open_terminal_trades.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:8000"
ENDPOINT = BASE_URL + "/api/v1/trades/subscribe-open-market-data"


def main() -> int:
    request = urllib.request.Request(
        ENDPOINT,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Request failed: HTTP {exc.code}\n{body}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(
            f"Cannot reach backend at {BASE_URL}. Make sure FastAPI is running.\n{exc}",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(f"Backend returned invalid JSON: {exc}", file=sys.stderr)
        return 1

    print("WebSocket state:", result.get("websocket", {}).get("state"))
    print("Registered tokens:", result.get("websocket", {}).get("subscription_count"))
    print("Active trades:", result.get("active_count"))
    summary = result.get("result", {})
    print("Resolved/subscription requests:", summary.get("subscribed"))
    print("Unresolved:", summary.get("unresolved"))
    print("\nTrade subscription diagnostics:")
    for trade in result.get("active_terminal_trades", []):
        print(
            f"- {trade.get('exchange')}:{trade.get('symbol')} "
            f"token={trade.get('instrument_token')} "
            f"registered={trade.get('subscription_registered')} "
            f"quote={trade.get('quote_available')} "
            f"status={trade.get('diagnostic')}"
        )

    websocket = result.get("websocket", {})
    if not websocket.get("connected"):
        print(
            "\nWARNING: desired subscriptions are registered in backend memory, "
            "but Kite WebSocket is not connected. Check Kite login/session and backend logs."
        )
        return 2
    if summary.get("unresolved", 0):
        print("\nWARNING: Some trades could not be resolved to Kite instrument tokens.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
