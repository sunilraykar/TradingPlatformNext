-- Remove volatile quote/cache fields previously serialized into trade payloads.
-- Live quotes and subscription diagnostics are process-local only.
UPDATE trade_records
SET payload = JSON_REMOVE(
    payload,
    '$.last_price',
    '$.unrealized_pnl',
    '$.last_market_data_at',
    '$.quote_state',
    '$.quote_received_at',
    '$.market_quote',
    '$.cached_quote',
    '$.subscription_state',
    '$.subscription_mode',
    '$.subscribed'
)
WHERE JSON_CONTAINS_PATH(
    payload, 'one',
    '$.last_price',
    '$.unrealized_pnl',
    '$.last_market_data_at',
    '$.quote_state',
    '$.quote_received_at',
    '$.market_quote',
    '$.cached_quote',
    '$.subscription_state',
    '$.subscription_mode',
    '$.subscribed'
);
