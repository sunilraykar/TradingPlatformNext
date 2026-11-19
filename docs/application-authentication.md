# Application authentication

Application accounts are separate from broker/provider credentials. Signing out of TradingPlatformNext revokes the application browser session only; it does not revoke Kite, APIPLATFORM, or AutoTrender credentials.

## Roles

- **SUPER_ADMIN**: initial administrator, can invite Viewer accounts, enable/disable Viewer accounts, and manage their own browser sessions.
- **VIEWER**: read-only application access. The API rejects every non-GET/HEAD request for this role. The frontend hides Settings and Trade Terminal and blocks unsafe API calls.

Only the configured initial administrator can create the first account. Additional accounts are Viewer invitations; this API does not provide public registration or additional Super Admin creation.

## Database migration

Back up the target MySQL database first. Apply migrations in order:

1. `database/migrations/001_initial.sql`
2. `database/migrations/002_application_authentication.sql`

Do not apply the migration to a production database until the deployment and backup have been reviewed. The migration creates `app_users`, `app_user_sessions`, `app_auth_tokens`, and `app_auth_login_attempts`. It does not alter provider session records.

## Backend environment

Set these values in `backend/.env` (never commit that file):

- `APP_SECRET_KEY`: unique cryptographically random secret, at least 32 characters. Use a different key for every environment.
- `APP_PUBLIC_URL`: browser URL used in verification, invitation, and reset links.
- `INITIAL_SUPER_ADMIN_EMAIL`: exact email address allowed to create the first Super Admin.
- `SESSION_IDLE_TIMEOUT_MINUTES`: sliding idle timeout; default 720 minutes.
- `SESSION_ABSOLUTE_TIMEOUT_DAYS`: absolute session lifetime; default 7 days.
- `CORS_ALLOWED_ORIGINS`: comma-separated exact frontend origins. Include the browser origin when frontend and backend are on different origins.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`: SMTP delivery settings. SMTP is required outside development for verification, invitations, and password resets.

Generate a secret with a cryptographically secure random generator. Do not reuse the Kite encryption key, provider credentials, or a secret from another environment. Use HTTPS in production; production cookies are Secure and HttpOnly where appropriate.

## Initial administrator setup

1. Apply both SQL migrations to the intended development database.
2. Configure `APP_SECRET_KEY`, `APP_PUBLIC_URL`, `INITIAL_SUPER_ADMIN_EMAIL`, and SMTP in the backend environment.
3. Configure `CORS_ALLOWED_ORIGINS` to include the frontend origin.
4. Start the backend and frontend.
5. Open the frontend, choose **Initial setup**, and enter the configured administrator email and a password of at least 12 characters.
6. Open the one-time email verification link. In development without SMTP, the API returns a local action link in its response.
7. Sign in after verification.

The bootstrap endpoint is protected by a MySQL named lock and becomes unavailable as soon as any application user exists. If delivery fails during development/operations, the verification-resend endpoint can issue a fresh link.

## Sessions and request protection

- Each browser login receives a cryptographically random opaque session cookie. The database stores only its SHA-256 hash.
- Session cookies are HttpOnly, host-only, SameSite=Lax, and Secure outside development/test.
- Sessions expire after the configured idle or absolute timeout. The Sessions page lists device/browser sessions and allows individual revocation.
- Password reset and account disable revoke existing sessions.
- Unsafe API methods require an HMAC-signed double-submit CSRF token and an allowed Origin. CORS preflight OPTIONS requests are passed through without application authentication so browser preflight succeeds.
- Login failures are throttled per email/IP identity after eight failed attempts in a 15-minute window.
- Trade Terminal WebSocket connections require an authenticated application session.
- The Kite callback remains public for the broker redirect; all other application API routes require an application session.

## Development and production separation

Use separate MySQL databases, APP_SECRET_KEY values, frontend origins, backend URLs, and SMTP configuration for development and production. Do not point a development frontend at a production database. Do not copy production session cookies or secrets into development. Apply migrations separately after taking a database backup.

## Operational note

The GitHub commits add the migration and application code, but do not apply database migrations, create the first account, configure SMTP, or deploy/restart any VPS service. Those are environment-specific operations and must be performed deliberately.
