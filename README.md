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

## StaticIP.in outbound proxy

The backend supports an authenticated StaticIP.in HTTP proxy for outbound REST/API traffic. Copy the **Host, Port, Username and Password** from the issued IP's details in the StaticIP.in dashboard; do not assume the hostname from an example. Keep these values only in `backend/.env`.

Set:

```dotenv
OUTBOUND_PROXY_ENABLED=true
OUTBOUND_PROXY_SCHEME=http
OUTBOUND_PROXY_HOST=your-dashboard-host.staticip.in
OUTBOUND_PROXY_PORT=443
OUTBOUND_PROXY_USERNAME=your-staticip-username
OUTBOUND_PROXY_PASSWORD=your-staticip-password
```

Restart the backend after changing these values. Before using broker order endpoints, verify the actual proxy egress address from the backend directory:

```powershell
python -m app.core.proxy_check
```

The command calls `https://api64.ipify.org` through the configured proxy and prints the observed public IP. It must match the static IP shown in the StaticIP.in dashboard and the address registered with Zerodha.

Kite REST calls (including login-session exchange and account/order REST methods), APIPLATFORM REST sessions, and AutoTrender REST sessions use the configured proxy. The Kite market-data WebSocket remains direct because Zerodha does not require the registered static IP for WebSocket market data and the authenticated proxy WebSocket path has not been validated. The Selenium browser steps used to collect APIPLATFORM and AutoTrender website cookies also remain direct; their subsequent Python REST calls use the proxy. If either provider binds its browser cookies to the source IP, we will need to route that browser bootstrap through the proxy as a separate step. Localhost traffic is excluded from the process proxy. The proxy is disabled by default.

Do not paste proxy credentials into chat, source files, GitHub, or frontend environment variables. If the egress check does not return the exact dashboard IP, leave the proxy disabled for broker activity and resolve the proxy configuration first.

## Daily session reset

`KITE_SESSION_RESET_ENABLED=true`, `KITE_SESSION_RESET_TIME=23:00`, and `KITE_SESSION_RESET_TIMEZONE=Asia/Kolkata` are the defaults. Change the time to any valid `HH:MM` value or disable the reset by setting the enabled flag to `false`. This expires the app's stored session; it does not revoke a token on Zerodha's servers.

Zerodha still controls its login and authentication requirements. The app can initiate login and process the callback automatically, but cannot bypass required Zerodha authentication or promise a permanent session.

## Current scope and safety

This milestone does not place orders. It establishes Kite authentication, read-only market data, WebSocket caching, and a MySQL foundation. Automated execution will be implemented only after market-data correctness, session recovery, and persistence tests pass.
