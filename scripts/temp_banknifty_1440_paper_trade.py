"""Temporary BANKNIFTY 14:40 AutoTrender paper-entry evaluator.

Run from the backend virtual environment while the local API is running:
    python scripts/temp_banknifty_1440_paper_trade.py

Uses the AutoTrender intraday response for today's 14:40 IST row and current saved
Settings. It refuses to create a trade if that timestamp is absent or evaluates to
WAIT. It creates a PAPER terminal record only; it never places a broker order.
The existing ATM selector uses the current cached/live Kite spot to choose the strike.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
BASE_URL = os.getenv("TRADING_PLATFORM_API", "http://127.0.0.1:8000/api/v1").rstrip("/")
TARGET_TIME = "14:40"
INDEX = "BANKNIFTY"
TIMEOUT_SECONDS = 20


def get(path: str) -> dict:
    response = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected JSON response from {path}")
    return value


def post(path: str, payload: dict) -> dict:
    response = requests.post(f"{BASE_URL}{path}", json=payload, timeout=TIMEOUT_SECONDS)
    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"POST {path} failed ({response.status_code}): {detail}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected JSON response from {path}")
    return value


def number(value):
    if isinstance(value, dict):
        for key in ("value", "type", "signal", "label", "status", "name", "text"):
            if key in value and value[key] is not None:
                result = number(value[key])
                if result is not None:
                    return result
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(",", "").replace("%", "")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def signal(value) -> str:
    if isinstance(value, dict):
        for key in ("type", "value", "signal", "label", "status", "name", "text"):
            if value.get(key) is not None:
                return str(value[key]).strip().upper()
    return str(value or "").strip().upper()


def row_time(row: dict) -> str:
    value = row.get("time", row.get("mtime", row.get("timestamp", row.get("dateTime", row.get("datetime", "")))))
    text = str(value).strip()
    # Handles plain HH:MM, HH:MM:SS, and ISO datetime strings.
    if "T" in text:
        text = text.split("T", 1)[1]
    if " " in text:
        text = text.rsplit(" ", 1)[-1]
    return text[:5]


def evaluate(row: dict, settings: dict) -> dict:
    call = number(row.get("call"))
    put = number(row.get("put"))
    total = call + put if call is not None and put is not None else None
    call_strength = call / total * 100 if total is not None and total > 0 else None
    put_strength = put / total * 100 if total is not None and total > 0 else None
    strength_diff = call_strength - put_strength if call_strength is not None and put_strength is not None else None

    price = number(row.get("price", row.get("close", row.get("ltp"))))
    vwap = number(row.get("vWap", row.get("vwap")))
    buffer = (price - vwap) / vwap * 100 if price is not None and vwap not in (None, 0) else None
    pcr = number(row.get("pcr"))
    vwap_signal = signal(row.get("vWapSignal", row.get("vwapSignal")))
    option_signal = signal(row.get("optionSignal"))

    strength_value = number(settings.get("callPutStrengthDiff"))
    strength_threshold = abs(10 if strength_value is None else strength_value)
    buffer_threshold = abs(number(settings.get("vwapPriceDifferenceBuffer")) if settings.get("vwapPriceDifferenceBuffer") is not None else 0.3)
    call_pcr_threshold = number(settings.get("callPcr"))
    put_pcr_threshold = number(settings.get("putPcr"))
    call_pcr_threshold = 1 if call_pcr_threshold is None else call_pcr_threshold
    put_pcr_threshold = 1 if put_pcr_threshold is None else put_pcr_threshold

    strength_pass = strength_diff is not None and strength_diff > strength_threshold
    buffer_pass = buffer is not None and abs(buffer) <= buffer_threshold
    bullish = strength_pass and buffer_pass and vwap_signal == "BUY" and option_signal == "BUY" and pcr is not None and pcr > call_pcr_threshold
    bearish = strength_pass and buffer_pass and vwap_signal == "SELL" and option_signal == "SELL" and pcr is not None and pcr < put_pcr_threshold
    entry = "BUY" if bullish else "SELL" if bearish else "WAIT"
    return {
        "enter": entry, "call": call, "put": put, "pcr": pcr, "price": price, "vwap": vwap,
        "vwap_signal": vwap_signal, "option_signal": option_signal,
        "call_strength": call_strength, "put_strength": put_strength, "strength_diff": strength_diff,
        "buffer_pct": buffer, "thresholds": {
            "callPutStrengthDiff": strength_threshold, "vwapPriceDifferenceBuffer": buffer_threshold,
            "callPcr": call_pcr_threshold, "putPcr": put_pcr_threshold,
        },
        "checks": {"strength": strength_pass, "buffer": buffer_pass},
    }


def main() -> int:
    today = datetime.now(IST).date().isoformat()
    print(f"Evaluating {INDEX} for {today} at {TARGET_TIME} IST using API intraday data and saved Settings.")
    intraday = get(f"/autotrender/indices/{INDEX}/intraday?mins=5")
    rows = intraday.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("Intraday API response does not contain a data array.")

    matches = [row for row in rows if isinstance(row, dict) and row_time(row) == TARGET_TIME]
    if not matches:
        available = sorted({row_time(row) for row in rows if isinstance(row, dict) and row_time(row)})
        raise RuntimeError(
            f"No {TARGET_TIME} row found for {INDEX} in today's API response. "
            f"Available times: {', '.join(available[:8])}{' ...' if len(available) > 8 else ''}. "
            "No terminal record was created."
        )
    row = matches[-1]
    settings_response = get("/settings")
    settings = settings_response.get("settings", {})
    decision = evaluate(row, settings)

    print(json.dumps({"timestamp": f"{today} {TARGET_TIME} IST", "decision": decision}, indent=2))
    if decision["enter"] == "WAIT":
        print("Decision is WAIT; refusing to insert a terminal trade.")
        return 2

    terminal_response = get("/trades?underlying=BANKNIFTY")
    existing_trades = terminal_response.get("trades")
    if not isinstance(existing_trades, list):
        raise RuntimeError("Trade Terminal response does not contain a trades array; refusing to create a trade.")
    terminal_statuses = {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}
    def belongs_to_banknifty(trade: dict) -> bool:
        # The API filter may return unrelated trades; validate the underlying locally too.
        underlying = str(trade.get("underlying") or trade.get("index") or "").strip().upper()
        if underlying:
            return underlying == INDEX
        symbol = str(trade.get("instrument_symbol") or trade.get("tradingsymbol") or "").strip().upper()
        return symbol.startswith("BANKNIFTY")

    open_banknifty_trades = [
        trade for trade in existing_trades
        if isinstance(trade, dict)
        and belongs_to_banknifty(trade)
        and str(trade.get("status") or "OPEN").upper() not in terminal_statuses
    ]
    if open_banknifty_trades:
        print("An active BANKNIFTY trade already exists; no new trade created.")
        print(json.dumps([{
            "trade_id": trade.get("trade_id"),
            "instrument_symbol": trade.get("instrument_symbol"),
            "status": trade.get("status", "OPEN"),
            "execution_mode": trade.get("execution_mode"),
            "source": trade.get("source"),
        } for trade in open_banknifty_trades], indent=2))
        return 0

    # Direction decides which option side to buy: bullish -> CE, bearish -> PE.
    contract = get(f"/kite/instruments/select-atm?index={INDEX}&signal={decision['enter']}")
    if contract.get("status") != "SELECTED":
        raise RuntimeError(f"ATM contract selection did not return SELECTED: {contract}")

    lot_size = int(contract.get("lot_size") or 0)
    if lot_size <= 0:
        raise RuntimeError("Selected contract has no valid lot size; no terminal record was created.")

    payload = {
        "execution_mode": "PAPER",
        "source": "TEMP_BANKNIFTY_1440",
        "strategy": "BANKNIFTY AutoTrender 14:40 temporary evaluation",
        "underlying": INDEX,
        "instrument_symbol": contract["tradingsymbol"],
        "exchange": contract.get("exchange", "NFO"),
        "direction": "BUY",
        "quantity": lot_size,
        "notes": (
            f"Temporary paper entry based on AutoTrender row {today} {TARGET_TIME} IST. "
            f"Underlying decision={decision['enter']}; VWAP={decision['vwap_signal']}; "
            f"Option={decision['option_signal']}; PCR={decision['pcr']}; "
            f"StrengthDiff={decision['strength_diff']}; BufferPct={decision['buffer_pct']}; "
            f"BufferThreshold={decision['thresholds']['vwapPriceDifferenceBuffer']}%. "
            f"Contract selected by existing ATM selector using current Kite spot; "
            "no broker order placed."
        ),
    }
    result = post("/trades", payload)
    trade = result.get("trade", result)
    print(json.dumps({
        "result": "terminal paper record created",
        "trade_id": trade.get("trade_id"),
        "status": trade.get("status"),
        "execution_mode": trade.get("execution_mode"),
        "underlying_decision": decision["enter"],
        "contract": contract.get("tradingsymbol"),
        "option_type": contract.get("option_type"),
        "quantity": lot_size,
        "note": "No live order was placed. ATM strike selection used current Kite spot, not historical 14:40 spot.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
