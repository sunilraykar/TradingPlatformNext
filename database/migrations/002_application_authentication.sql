-- Application identity is independent from Kite/APIPLATFORM/AutoTrender provider sessions.
CREATE TABLE IF NOT EXISTS app_users (
    user_id CHAR(36) NOT NULL,
    email VARCHAR(320) NOT NULL,
    password_hash VARCHAR(255) NULL,
    role ENUM('SUPER_ADMIN', 'VIEWER') NOT NULL DEFAULT 'VIEWER',
    status ENUM('PENDING_VERIFICATION', 'ACTIVE', 'DISABLED') NOT NULL DEFAULT 'PENDING_VERIFICATION',
    email_verified_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_app_users_email (email),
    KEY ix_app_users_role_status (role, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS app_user_sessions (
    token_hash CHAR(64) NOT NULL,
    user_id CHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    user_agent VARCHAR(512) NULL,
    ip_address VARCHAR(45) NULL,
    PRIMARY KEY (token_hash),
    KEY ix_app_sessions_user (user_id, revoked_at, expires_at),
    CONSTRAINT fk_app_sessions_user FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS app_auth_tokens (
    token_hash CHAR(64) NOT NULL,
    user_id CHAR(36) NOT NULL,
    purpose ENUM('VERIFY_EMAIL', 'INVITE', 'RESET_PASSWORD') NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    consumed_at DATETIME(6) NULL,
    PRIMARY KEY (token_hash),
    KEY ix_app_auth_tokens_user_purpose (user_id, purpose, consumed_at),
    CONSTRAINT fk_app_auth_tokens_user FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS app_auth_login_attempts (
    identity_hash CHAR(64) NOT NULL,
    failed_attempts INT UNSIGNED NOT NULL DEFAULT 0,
    window_started_at DATETIME(6) NOT NULL,
    locked_until DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (identity_hash),
    KEY ix_app_login_attempts_lock (locked_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
