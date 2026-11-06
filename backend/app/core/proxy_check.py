"""Verify the public egress IP used by Kite REST requests.

Run from the backend directory:
    python -m app.core.proxy_check
"""
import ipaddress

import requests

from app.core.outbound_proxy import get_proxy_map, proxy_status


def main() -> int:
    status = proxy_status()
    if not status["enabled"] or not status["configured"]:
        print("Kite outbound proxy is not enabled or its settings are incomplete.")
        print("Configure OUTBOUND_PROXY_* in backend/.env and restart the backend.")
        return 2

    response = requests.get(
        "https://api64.ipify.org?format=json",
        proxies=get_proxy_map(),
        timeout=15,
    )
    response.raise_for_status()
    address = response.json().get("ip", "")
    parsed = ipaddress.ip_address(address)
    print(f"Kite proxy egress IP: {parsed}")
    print(f"Address family: IPv{parsed.version}")
    print("Compare this address with the StaticIP.in IP registered with Zerodha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
