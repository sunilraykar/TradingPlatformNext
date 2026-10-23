# Architecture principles

- MySQL is the only application database. Firebase is not used.
- The legacy repository is a feature/behavior reference, not an architecture template.
- Kite Connect is an external provider, not a persistence layer.
- High-frequency live quote updates stay in an in-memory cache.
- Durable application configuration, provider session metadata, trade records, and audit events belong in MySQL.
- Do not persist every tick by default. Persist snapshots/history according to explicit requirements.
- Any order/fill/risk event added later must be persisted immediately and reconciled with Kite.
- No live-order endpoints are included in the first milestone.
