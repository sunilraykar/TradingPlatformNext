-- Normalize the existing JSON snapshot into one row per APIPLATFORM symbol.
-- Apply after 004_apiplatform_all_symbols_mi.sql.
-- The existing snapshot is expanded in place so its original fetched_at is retained.

ALTER TABLE All_symbols_MI
    ADD COLUMN symbol VARCHAR(64) NULL AFTER symbol_master_id,
    ADD COLUMN category VARCHAR(32) NULL AFTER symbol;

-- Reuse the existing row for the first symbol in each stored JSON snapshot.
UPDATE All_symbols_MI
SET
    symbol = JSON_UNQUOTE(JSON_EXTRACT(response_payload, '$[0].symbol')),
    category = JSON_UNQUOTE(JSON_EXTRACT(response_payload, '$[0].category'))
WHERE JSON_TYPE(response_payload) = 'ARRAY'
  AND JSON_LENGTH(response_payload) > 0;

-- Expand the remaining JSON array elements into individual rows.
INSERT INTO All_symbols_MI (symbol, category, fetched_at, created_at)
SELECT
    jt.symbol,
    jt.category,
    snapshot.fetched_at,
    snapshot.created_at
FROM All_symbols_MI AS snapshot
JOIN JSON_TABLE(
    snapshot.response_payload,
    '$[*]' COLUMNS (
        row_number FOR ORDINALITY,
        symbol VARCHAR(64) PATH '$.symbol',
        category VARCHAR(32) PATH '$.category'
    )
) AS jt
    ON jt.row_number > 1
WHERE jt.symbol IS NOT NULL
  AND jt.category IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM All_symbols_MI AS existing
      WHERE existing.fetched_at = snapshot.fetched_at
        AND existing.symbol = jt.symbol
  );

-- The JSON payload and its whole-response hash are no longer needed per row.
ALTER TABLE All_symbols_MI
    MODIFY COLUMN symbol VARCHAR(64) NOT NULL,
    MODIFY COLUMN category VARCHAR(32) NOT NULL,
    DROP COLUMN response_payload,
    DROP COLUMN payload_hash,
    ADD KEY ix_all_symbols_mi_symbol_category (symbol, category),
    ADD KEY ix_all_symbols_mi_fetched_at_symbol (fetched_at, symbol);
