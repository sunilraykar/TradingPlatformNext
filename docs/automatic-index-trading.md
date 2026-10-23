# AutoTrender Index Automatic Trading

## Source of truth for strategy rules

Reuse the existing strategy from the legacy repository `sunilraykar/TradingPlatform`, especially `backend/app/services/autotrender/options_paper_trade_worker.py`. Do not invent a new strategy or ask the user to redefine these rules. Port the established logic and settings into TradingPlatformNext, adapting persistence and architecture only where necessary.

## Existing signal eligibility rules

1. Read the latest AutoTrender intraday row already persisted by the legacy pipeline; use the existing configured settings under `config.autoTrender` and `config.marketIntelligence`.
2. Require both `VWAP Signal` and `Option Signal` to normalize to exactly `BUY` or `SELL`, and require them to match. BUY means bullish; SELL means bearish.
3. Require valid, positive spot price and VWAP, valid non-negative call/put strength values with positive combined strength, and a valid PCR. Missing/invalid data fails closed.
4. Calculate call and put strength percentages from `call / (call + put) * 100` and `put / (call + put) * 100`. Use absolute call-minus-put strength difference and require it to be strictly greater than configured `autoTrender.callPutStrengthDiff` (default 10).
5. Bullish eligibility requires PCR strictly greater than `autoTrender.callPcr` (default 1.0); bearish eligibility requires PCR strictly less than `autoTrender.putPcr` (default 1.0).
6. Require absolute VWAP price difference `abs((price - VWAP) / price * 100)` to be strictly less than `autoTrender.vwapPriceDifferenceBuffer` (default 0.3%).
7. The direction from strength/PCR conditions must agree with the matching VWAP/Option BUY or SELL signal. If bullish and bearish eligibility overlap, reject as ambiguous.

## Existing automatic-entry behavior to preserve

- Evaluate every 60 seconds during market hours.
- Automatic option entries are blocked before 10:00 IST and at/after 15:25 IST. This does not disable processing/exits for existing trades.
- Use the configured enabled index list (`autoTrender.dashboardIndices`) and the existing per-index expiry preference (`callPutExpiry`).
- Resolve the nearest-strike eligible CE for bullish signals and PE for bearish signals from Kite's instrument master; require a valid expiry and positive live option premium.
- Use the existing target fallback `marketIntelligence.targetFallbackPercent` (default 1.0%) and preserve existing exit behavior rather than creating a competing exit implementation.
- Maximum one active automatic trade per underlying index (not merely per full option symbol). This rule applies only to automatic trades; manual options trades remain unaffected.
- Legacy paper trades are persisted in Firebase with `Type=Options`. TradingPlatformNext must implement equivalent isolated persistence/audit records appropriate to its architecture.
- The legacy automatic worker is paper-trading only and must not call broker order-placement methods. Live execution is a later, separately approved scope; never silently enable it.

## Porting and implementation plan

1. Inspect legacy worker, settings, instrument/expiry resolution, active-trade duplicate prevention, and exit worker before coding. Preserve existing formulas and edge-case behavior.
2. Implement a pure, unit-tested signal evaluator in TradingPlatformNext with parity tests for BUY/SELL matching, threshold boundaries, invalid/missing fields, ambiguous PCR thresholds, and direction disagreement.
3. Add a 60-second paper-trade evaluator using the actual AutoTrender response/schema and configured settings; expose diagnostics for each index and skip reason.
4. Implement atomic/idempotent maximum-one-active-trade-per-index protection; do not affect manual trade flows.
5. Reuse/port option contract resolution, positive quote checks, configured expiry preference, target fallback, and existing exits. Add tests before enabling the worker.
6. Add UI controls/status consistent with the current app settings, but do not invent replacement strategy settings where the legacy settings already exist.

## Safety invariants

- Paper-only by default; no broker order placement.
- Stale, unknown, invalid, contradictory, or repeated signals never create a trade.
- Automatic entries respect the 10:00–15:25 IST window and market-open gate.
- At most one active automatic trade per underlying index; manual options are unaffected.
- Preserve exit processing and audit every evaluation, skip reason, entry, and exit.
- Never commit credentials, access tokens, or account secrets.
