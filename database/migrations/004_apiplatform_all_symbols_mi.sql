-- Dedicated storage for the APIPLATFORM LIVE all-symbol master.
-- Apply after 003_remove_quote_cache_from_trade_payloads.sql.

CREATE TABLE IF NOT EXISTS All_symbols_MI (
    symbol_master_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    fetched_at DATETIME(6) NOT NULL,
    response_payload JSON NOT NULL,
    payload_hash BINARY(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_all_symbols_mi_fetched_at (fetched_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
