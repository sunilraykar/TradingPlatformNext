-- Initial MySQL schema for TradingPlatformNext.
-- Apply to a NEW database only. No Firebase tables or legacy schema are assumed.

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key VARCHAR(190) NOT NULL PRIMARY KEY,
    setting_value JSON NOT NULL,
    description VARCHAR(500) NULL,
    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    updated_by VARCHAR(100) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS provider_sessions (
    provider_session_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    provider_code VARCHAR(40) NOT NULL,
    provider_user_id VARCHAR(100) NOT NULL,
    access_token_encrypted VARBINARY(4096) NOT NULL,
    session_date DATE NOT NULL,
    status ENUM('ACTIVE','EXPIRED','REVOKED','ERROR') NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_provider_session_day (provider_code, provider_user_id, session_date),
    KEY ix_provider_sessions_status (provider_code, status, session_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS instruments (
    instrument_token BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    exchange VARCHAR(30) NOT NULL,
    tradingsymbol VARCHAR(100) NOT NULL,
    name VARCHAR(150) NULL,
    instrument_type VARCHAR(30) NULL,
    segment VARCHAR(40) NULL,
    expiry DATE NULL,
    strike DECIMAL(18,6) NULL,
    lot_size INT UNSIGNED NULL,
    tick_size DECIMAL(12,6) NULL,
    source VARCHAR(40) NOT NULL DEFAULT 'KITE',
    instrument_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_instrument_symbol (exchange, tradingsymbol),
    KEY ix_instrument_expiry (segment, expiry),
    KEY ix_instrument_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    snapshot_type VARCHAR(50) NOT NULL,
    snapshot_key VARCHAR(190) NOT NULL,
    trading_date DATE NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    payload JSON NOT NULL,
    payload_hash BINARY(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_snapshot_idempotency (snapshot_type, snapshot_key, captured_at, payload_hash),
    KEY ix_snapshot_day_type (trading_date, snapshot_type),
    KEY ix_snapshot_key_time (snapshot_key, captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(80) NOT NULL,
    entity_type VARCHAR(80) NOT NULL,
    entity_id VARCHAR(190) NOT NULL,
    event_at DATETIME(6) NOT NULL,
    actor VARCHAR(100) NULL,
    payload JSON NOT NULL,
    correlation_id CHAR(36) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_audit_entity_time (entity_type, entity_id, event_at),
    KEY ix_audit_type_time (event_type, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
