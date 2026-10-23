# AutoTrender Index Automatic Trading

## Goal

Add an isolated feature that evaluates AutoTrender index signals and can generate trades only when configured signal-quality and risk gates pass. Keep this work on the feature branch; do not merge it into main until reviewed and tested.

## Current repository constraints

- AutoTrender already exposes index intraday data for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX and BANKEX.
- Kite market-data/session support exists.
- The README's original milestone explicitly states automated execution is gated on market-data correctness, session recovery and persistence tests.
- Signal fields and their semantics must be confirmed against real AutoTrender responses before treating them as executable signals.

## Staged implementation

1. **Signal observer (read-only):** Normalize AutoTrender payloads for supported indices; record timestamp, source payload hash, signal, underlying price, and data freshness. Do not place orders.
2. **Signal quality engine:** Evaluate configurable conditions and return PASS, WAIT, or REJECT with human-readable reasons. Missing, stale, or contradictory data must fail closed.
3. **Paper trading:** Convert passing signals into simulated orders, enforce one-entry-per-signal/idempotency, track positions, exits, stop loss, target, and daily risk limits. No broker order calls.
4. **Review UI and audit:** Show signal inputs, gate results, paper trades, and audit history.
5. **Live execution (separate explicit opt-in):** Only after paper validation, add a disabled-by-default live execution mode, order reconciliation, duplicate protection, broker rejection handling, kill switch, position limits, daily loss cap, and tests. Never silently switch from paper to live.

## Required configuration before strategy implementation

- Which indices to enable.
- Exact AutoTrender fields and values that count as a valid BUY/SELL signal.
- Whether to trade index futures or options; for options, expiry/strike selection rules.
- Entry timing and stale-signal timeout.
- Stop-loss, target/trailing exit, maximum trades per day, maximum quantity, and daily loss cap.
- Whether to begin with paper trading (recommended) or define a later live-execution phase.

## Safety invariants

- Default mode is paper; live execution remains unavailable until deliberately implemented and enabled.
- Unknown or missing signal values never trigger a trade.
- Repeated polling of the same signal cannot create duplicate trades.
- No order is sent without a valid instrument, lot-size validation, fresh data, configured risk limits, and a healthy broker session.
- Every decision and simulated/real order lifecycle event is auditable.
- Do not commit credentials, tokens, or account secrets.
