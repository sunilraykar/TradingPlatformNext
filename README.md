# TradingPlatformNext

Clean-room rebuild of TradingPlatform. The legacy repository is a feature reference only; its architecture and Firebase data model are not reused.

## First milestone: Kite foundation

- Kite Connect REST client and Ticker adapter
- MySQL-backed application configuration/session persistence (schema included)
- Encrypted Kite access-token storage
- Automatic session restore and browser-based login initiation
- Automatic callback handling at `/api/v1/kite/callback`
- Configurable daily local session reset (default 11:00 PM Asia/Kolkata)
- In-memory live quote cache
- FastAPI endpoints for health, Kite session exchange, quotes, subscriptions
- No broker orders enabled in this first milestone

## Local setup

1. Install Python 3.11 or 3.12.
2. Create a new MySQL database, for example `trading_platform_v2`.
3. Copy `backend/.env.example` to `backend/.env` and set credentials.
4. Generate a Fernet key and set it as `KITE_TOKEN_ENCRYPTION_KEY` in the local `.env`. Generate one with:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
5. Install dependencies:

   ```powershell
   cd backend
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -e .[dev]
   ```

6. Apply the initial SQL schema to the new database:

   ```powershell
   mysql -u root -p trading_platform_v2 < ..\database\migrations\001_initial.sql
   ```

7. Start the API:

   ```powershell
   uvicorn app.main:app --reload
   ```

The Kite API key/secret, encryption key, and MySQL credentials must remain local in `.env`; never commit secrets. The redirect URL used in the Kite developer console must match the local callback URL. The application does not ask you to paste request tokens or access tokens.

## StaticIP.in proxy for Kite/Zerodha only

The authenticated StaticIP.in proxy is applied explicitly to **Kite Connect REST requests only**, including Kite session exchange, profile/session validation, instrument downloads, quotes, and account REST methods. Configure the proxy using the Host, Port, Username and Password shown for the issued IP in the StaticIP.in dashboard. Keep these values only in `backend/.env`.

Set:

```dotenv
OUTBOUND_PROXY_ENABLED=true
OUTBOUND_PROXY_SCHEME=http
OUTBOUND_PROXY_HOST=your-dashboard-host.staticip.in
OUTBOUND_PROXY_PORT=443
OUTBOUND_PROXY_USERNAME=your-staticip-username
OUTBOUND_PROXY_PASSWORD=your-staticip-password
```

Restart the backend after changing these values. Verify Kite proxy egress from the backend directory:

```powershell
python -m app.core.proxy_check
```

The check makes a standalone request through the configured Kite proxy and prints the observed public IP. Compare it with the StaticIP.in address registered with Zerodha.

**All other traffic remains direct:** Kite market-data WebSocket, AutoTrender REST and browser authentication, APIPLATFORM REST and browser authentication, and Firebase. The application does not set process-wide `HTTP_PROXY` or `HTTPS_PROXY` environment variables. The proxy configuration is passed only to KiteConnect REST clients.

Do not paste proxy credentials into chat, source files, GitHub, or frontend environment variables. If the egress check does not return the expected dashboard IP, leave the proxy disabled for broker activity and resolve the proxy configuration before enabling it.

## Daily session reset

`KITE_SESSION_RESET_ENABLED=true`, `KITE_SESSION_RESET_TIME=23:00`, and `KITE_SESSION_RESET_TIMEZONE=Asia/Kolkata` are the defaults. Change the time to any valid `HH:MM` value or disable the reset by setting the enabled flag to `false`. This expires the app's stored session; it does not revoke a token on Zerodha's servers.

Zerodha still controls its login and authentication requirements. The app can initiate login and process the callback automatically, but cannot bypass required Zerodha authentication or promise a permanent session.

## Current scope and safety

This milestone does not place orders. It establishes Kite authentication, read-only market data, WebSocket caching, and a MySQL foundation. Automated execution will be implemented only after market-data correctness, session recovery, and persistence tests pass.
