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

- Evaluate at the configurable `AutoTrender Signal Evaluation Interval (seconds)` from Settings → AutoTrender (default 5 seconds; allowed range 1–3600 seconds). The setting controls background signal-evaluation cadence, not the separate UI data refresh interval. Eligible fresh signals now attempt one PAPER option entry through the canonical Trade Terminal: nearest-strike contract in the nearest listed expiry, live positive Kite premium, one lot, and target from `targetFallbackPercent` (default 1%). Entries are blocked if no contract/premium is available or an AutoTrender PAPER trade is already active for that underlying. No broker orders are placed. The actual AutoTrender timestamp field still must be verified against an authenticated live response; unknown/missing fields fail closed. Entry evaluation is blocked on weekends, before the configured start time, and at or after the configured end/cutoff (default 15:25 IST). Signals must contain a recognized source timestamp and be no older than the configurable `AutoTrender Maximum Signal Age (seconds)` (default 300 seconds; allowed range 1–3600); missing, invalid, stale, or future-dated timestamps fail closed. Repeated eligible rows with the same timestamp are suppressed per index in-process.
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


## Unified Trade Terminal and persistence architecture

### Product requirement

Build one Trade Terminal that monitors every trade in the platform, regardless of origin or execution mode:
- AutoTrender-generated paper trades.
- Manually created paper trades.
- Real/live broker trades.
- Trades created by other existing strategies or workflows.

The terminal is the unified operational view for the full trade lifecycle. A trade must have one canonical identity and one authoritative persisted record; do not create separate duplicate trade records just because a trade appears in different UI tabs or originated from different strategies.

### Canonical trade record

Define a shared trade model with a stable TradeID and fields appropriate to the current platform, including:
- execution mode: PAPER or LIVE;
- origin/source and strategy;
- underlying/index, instrument symbol, exchange, expiry, strike and option type when applicable;
- direction, quantity, entry order/fill details and average entry price;
- current status, open quantity, exit order/fill details and average exit price;
- stop-loss, target, realized/unrealized P&L where available;
- created/updated timestamps, last market-data timestamp, and end-of-day status;
- audit/version metadata and broker order/trade IDs for live trades.

Use the existing trade schema where it already satisfies these needs; migrate or adapt it deliberately rather than maintaining parallel competing schemas. Paper and live trades must be clearly distinguished in the UI and API.

### Database plus runtime cache

- Persist each trade once in the authoritative database and use a runtime cache for fast execution-time state and terminal updates.
- On startup/recovery, hydrate the cache from persisted open trades and reconcile live trades against broker orders/positions before allowing new live entries.
- During execution, update in-memory/cache state for high-frequency quote and P&L changes. Do not write every tick or every polling iteration to the database.
- Flush durable changes on meaningful trade lifecycle events, including entry accepted/filled, partial fill, exit accepted/filled, stop-loss/target changes, cancellation/rejection, status transitions, manual operator actions, and other significant risk or ownership changes.
- Also persist the latest final state at end of trading day, including closure/expiry and final P&L. End-of-day flush is a safety net, not a replacement for persisting major lifecycle events.
- Use a dirty-state/version or equivalent mechanism, bounded flush/retry behavior, and idempotent writes so a crash cannot silently lose major changes or create duplicate trades. Cache is not the source of truth after restart.
- Distinguish ephemeral values (ticks, frequently refreshed unrealized P&L) from durable events. Record enough event/audit history to reconstruct why a trade was entered, changed, or exited without writing every market tick.

### Unified Trade Terminal UI

- One terminal lists all paper and live trades with filters for mode, status, strategy/source, index/instrument, and date.
- Clearly label PAPER versus LIVE; live positions and broker order state must be reconciled rather than inferred only from cached UI state.
- Show entry/exit, quantity, average prices, stop-loss, target, realized/unrealized P&L, status, timestamps, and last-sync/reconciliation state where applicable.
- Keep open trades visible and monitored during the session; provide completed trades and end-of-day history without creating duplicate records.
- Manual actions and automatic strategy actions must go through the same canonical trade lifecycle service and audit trail.

### Reliability and acceptance criteria

1. One canonical TradeID per trade across worker, API, cache, database, and terminal.
2. Repeated signals, retries, worker restarts, and duplicate broker callbacks cannot create duplicate trades or lifecycle events.
3. High-frequency quote updates do not each trigger database writes.
4. Major lifecycle changes are persisted promptly and idempotently; end-of-day state is flushed and verified.
5. Startup recovery rebuilds cache from DB and reconciles live state with the broker before enabling new live entries.
6. A failed DB write marks the trade dirty, retries safely, and surfaces degraded persistence state; do not claim a durable change until persisted.
7. Paper and live trades share the terminal but remain distinctly labeled and separately risk-controlled.
8. Tests cover cache/DB consistency, crash/restart recovery, concurrent updates, idempotent flushes, end-of-day persistence, and broker reconciliation.

### Implementation order

1. Inspect the current TradingPlatformNext trade model, storage layer, cache/runtime state, APIs, terminal UI, and broker synchronization. Reuse established abstractions where possible.
2. Define the canonical trade contract and persistence lifecycle before wiring in new AutoTrender trades.
3. Refactor existing paper/manual/live sources to feed the shared trade service without breaking current workflows.
4. Implement cache hydration, dirty tracking, durable lifecycle-event flushes, retry behavior, and end-of-day flush.
5. Build the unified terminal and filters on the canonical API.
6. Add tests for persistence and recovery, then connect the AutoTrender paper worker. Live order execution remains a separate, explicitly enabled scope.


## Unified Trade Terminal implementation status

The first implementation slice is on `feature/autotrender-index-auto-trading`:

- Added `trade_records` and `trade_lifecycle_events` in migration `database/migrations/002_trade_terminal.sql`. Apply this after `001_initial.sql` before using the terminal.
- Added a canonical in-process trade cache that hydrates from MySQL at backend startup. Material lifecycle changes are written immediately; quote/P&L refreshes stay in memory; a maintenance task retries dirty writes and performs a daily 15:30 IST snapshot flush.
- Added `/api/v1/trades` APIs for listing, creating PAPER trades, updating lifecycle state, retrying persistence, end-of-day flush, and read-only Kite net-position reconciliation.
- Added a Trade Terminal navigation tab with PAPER/LIVE labels, filters for mode/status/source/underlying/instrument/date, paper-trade creation and closing, persistence health, and manual live-position sync.
- Trade creation accepts an idempotency key; the UI reuses the same key across retries. Live positions receive stable IDs based on broker position identity. Manual live-trade lifecycle changes and live order placement are blocked.
- Added backend tests for cache-only market updates, paper creation, idempotency, lifecycle versioning, dirty-write retry, live-position reconciliation, and flattening. GitHub Actions checks are used to verify the backend and frontend builds.

### Not yet complete

- Apply the SQL migration in the configured MySQL database and verify startup hydration against the real database.
- Run and review the latest GitHub Actions results before considering the code validated.
- Continuous quote subscription / unrealized-P&L refresh for paper trades, automatic broker reconciliation cadence, and live order/fill-to-trade linking still need a follow-up implementation pass.
- AutoTrender's automatic index paper-trade worker is not connected yet; it should be integrated only after this canonical lifecycle is verified.


## Configurable AutoTrender worker implementation status

The branch `feature/autotrender-60s-worker` adds the first scheduler slice:

- FastAPI lifespan starts and cleanly cancels the AutoTrender evaluator task. Its polling interval is configurable from Settings → AutoTrender via `AutoTrender Signal Evaluation Interval (seconds)` (default 5 seconds; allowed range 1–3600 seconds). The status endpoint reports the active interval.
- The worker reads the existing dashboard-index and signal-threshold settings from `app_settings`, fetches each enabled index's latest AutoTrender intraday response, extracts the latest row, and calls the unit-tested evaluator.
- Each index is isolated: malformed or unavailable data is logged for that index without preventing the other enabled indices from being evaluated.
- `GET /api/v1/autotrender/worker/status` reports the most recent process-local cycle and per-index outcomes.
- Unit tests cover response extraction, configured enabled indices, and entry-window boundaries.

**Scope boundary:** this slice evaluates and logs eligible signal candidates only. It deliberately does not create PAPER trades yet. Option-contract expiry preference, fresh positive option-premium validation, target fallback, atomic one-active-trade-per-index protection, canonical Trade Terminal creation, and end-to-end tests must be completed before entries are enabled. No broker order placement is called.
