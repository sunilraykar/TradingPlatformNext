"""Read-only AutoTrender entry diagnostic. Does NOT create or place trades.
Run from backend folder: python ..\\diagnose_autotrender_entry.py NIFTY
Supported: NIFTY, BANKNIFTY, FINNIFTY, SENSEX
"""
from __future__ import annotations
import sys
from datetime import datetime
from app.services import autotrender_paper_trade_worker as worker
from app.services.autotrender_signal_evaluator import evaluate_signal


def fmt(value, digits=4):
    return "MISSING" if value is None else (f"{value:.{digits}f}" if isinstance(value, float) else str(value))

def report(label, passed, detail):
    print(f"[{'PASS' if passed else 'FAIL'}] {label}: {detail}")

def main():
    index = (sys.argv[1] if len(sys.argv) > 1 else "NIFTY").upper()
    if index not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}:
        print("Use one of: NIFTY, BANKNIFTY, FINNIFTY, SENSEX")
        raise SystemExit(2)
    now = datetime.now(worker.IST)
    print(f"\nAutoTrender read-only entry diagnostic | {index} | {now.isoformat()}")
    print("No trade is created or placed by this script.\n")
    settings = worker._read_settings()
    print("CONFIGURED SETTINGS")
    for key in ("callPutStrengthDiff", "callPcr", "putPcr", "vwapPriceDifferenceBuffer", "autoTrenderEntryStartTime", "autoTrenderEntryEndTime", "autoTrenderMaxSignalAgeSeconds", "autoTrenderWorkerIntervalSeconds"):
        print(f"  {key}: {settings.get(key)}")
    enabled = worker.enabled_indices(settings.get("autoTrenderDashboardIndices"))
    report("Index enabled", index in enabled, f"enabled indices={enabled}")
    window_ok, window_reason = worker._entry_window(now, settings)
    report("Trading time window", window_ok, window_reason)
    if index not in enabled:
        print("\nSTOP: selected index is disabled in AutoTrender settings.")
        raise SystemExit(1)
    try:
        payload = worker._fetch_intraday(index)
    except Exception as exc:
        report("Fetch current AutoTrender snapshot", False, f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)
    row = worker.latest_intraday_row(payload)
    if not row:
        report("Latest intraday row", False, "no row found in provider response")
        print("Payload top-level keys:", list(payload) if isinstance(payload, dict) else type(payload).__name__)
        raise SystemExit(1)
    print("\nLATEST ROW FIELDS")
    for key in ("time", "timestamp", "datetime", "created_at", "call", "put", "pcr", "price", "close", "ltp", "vWap", "vwap", "vWapSignal", "vwapSignal", "optionSignal"):
        if key in row:
            print(f"  {key}: {row.get(key)}")
    max_age = worker._max_signal_age_seconds(settings.get("autoTrenderMaxSignalAgeSeconds"))
    fresh, freshness_reason, signal_ts = worker.signal_freshness(row, now, max_age)
    report("Signal freshness", fresh, f"{freshness_reason}; timestamp={signal_ts}; max_age={max_age}s")
    result = evaluate_signal(row, call_put_strength_diff=float(settings["callPutStrengthDiff"]), call_pcr=float(settings["callPcr"]), put_pcr=float(settings["putPcr"]), vwap_price_difference_buffer=float(settings["vwapPriceDifferenceBuffer"]))
    def num(*keys):
        for key in keys:
            value = row.get(key)
            try:
                if value not in (None, ""):
                    return float(str(value).replace(",", "").replace("%", "").strip())
            except (TypeError, ValueError):
                pass
        return None
    call, put = num("call", "callStrength", "call_strength"), num("put", "putStrength", "put_strength")
    price, vwap, pcr = num("price", "close", "ltp"), num("vWap", "vwap"), num("pcr")
    strength_diff = abs(call - put) if call is not None and put is not None else None
    buffer_pct = abs((price - vwap) / price * 100) if price and vwap and price > 0 and vwap > 0 else None
    vwap_sig = str(row.get("vWapSignal", row.get("vwapSignal", row.get("VWAP Signal", "")))).strip().upper()
    option_sig = str(row.get("optionSignal", row.get("Option Signal", ""))).strip().upper()
    print("\nENTRY CONDITION SUMMARY")
    strength_limit = abs(float(settings["callPutStrengthDiff"]))
    report("Absolute strength difference", strength_diff is not None and strength_diff > strength_limit, f"|Call-Put|={fmt(strength_diff)}; required > {strength_limit}")
    report("Signals agree", vwap_sig in ("BUY", "SELL") and vwap_sig == option_sig, f"VWAP={vwap_sig or 'MISSING'}; Option={option_sig or 'MISSING'}")
    buffer_limit = abs(float(settings["vwapPriceDifferenceBuffer"]))
    report("VWAP-price buffer", buffer_pct is not None and buffer_pct <= buffer_limit, f"absolute buffer={fmt(buffer_pct)}%; required <= {buffer_limit}%")
    if option_sig == "BUY":
        report("BUY PCR", pcr is not None and pcr > float(settings["callPcr"]), f"PCR={fmt(pcr)}; required > {settings['callPcr']}")
    elif option_sig == "SELL":
        report("SELL PCR", pcr is not None and pcr < float(settings["putPcr"]), f"PCR={fmt(pcr)}; required < {settings['putPcr']}")
    else:
        report("PCR direction", False, f"unrecognized option signal {option_sig or 'MISSING'}")
    print(f"\nBACKEND EVALUATOR: eligible={result.eligible}; direction={result.direction}; reason={result.reason}")
    print(f"Calculated strength difference={fmt(result.strength_difference)}")
    print(f"Calculated VWAP buffer={fmt(result.vwap_difference_percent)}%")
    if not window_ok:
        print(f"WORKER BLOCKER: {window_reason}")
    elif not fresh:
        print(f"WORKER BLOCKER: {freshness_reason}")
    elif not result.eligible:
        print(f"WORKER BLOCKER: evaluator rejected this row ({result.reason})")
    else:
        print("Evaluator conditions pass. Trade creation can still be blocked by duplicate-signal or active-trade/contract/quote checks.")
    print("\nNote: current worker fetches intraday data with mins=5; this script uses that same worker fetch path.")

if __name__ == "__main__":
    main()
