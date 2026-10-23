# Rebuild roadmap

## Milestone 1 — Kite foundation
- [x] Clean backend structure and dependency boundary
- [x] MySQL schema baseline
- [x] Kite session exchange endpoint
- [x] Read-only quote endpoint
- [x] WebSocket in-memory quote cache
- [x] Unit tests with mocked Kite provider
- [ ] Create remote GitHub repository and push initial commit
- [ ] Configure local MySQL and verify integration

## Milestone 2 — Instrument and live market-data services
- Instrument master ingestion and versioning
- Safe WebSocket subscribe/unsubscribe lifecycle
- Connection health, reconnect, and stale-quote detection
- Index and option-chain market-data requirements from feature references

## Milestone 3 — Feature parity
- Option Chain
- Swing Trades and Market Intelligence
- LTP Calculator
- Active Trades and trade details
- Configurable AutoExitProfitableTradesWorker

## Milestone 4 — Trading lifecycle
- Order/fill/position schema
- Paper execution
- Kite reconciliation and recovery
- Risk controls and live execution after verification
