-- Unified canonical trade records. Apply after 001_initial.sql.
CREATE TABLE IF NOT EXISTS trade_records (
    trade_id CHAR(36) NOT NULL PRIMARY KEY,
    execution_mode ENUM('PAPER','LIVE') NOT NULL,
    source VARCHAR(80) NOT NULL,
    strategy VARCHAR(120) NULL,
    underlying VARCHAR(80) NULL,
    instrument_symbol VARCHAR(120) NULL,
    status VARCHAR(40) NOT NULL,
    version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    is_dirty BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    persisted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_trade_mode_status (execution_mode, status),
    KEY ix_trade_underlying_status (underlying, status),
    KEY ix_trade_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
    event_id CHAR(36) NOT NULL PRIMARY KEY,
    trade_id CHAR(36) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    event_version BIGINT UNSIGNED NOT NULL,
    event_at DATETIME(6) NOT NULL,
    payload JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_trade_event_version (trade_id, event_version, event_type),
    KEY ix_trade_event_time (trade_id, event_at),
    CONSTRAINT fk_trade_lifecycle_trade FOREIGN KEY (trade_id) REFERENCES trade_records(trade_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
