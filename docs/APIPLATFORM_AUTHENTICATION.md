# APIPLATFORM authentication and option-chain diagnostics

TradingPlatformNext authenticates to APIPLATFORM from the Python backend. The browser
frontend must never receive or submit the APIPLATFORM Basic authorization value.

## Configuration

Copy `backend/.env.example` to `backend/.env` if it does not already exist, then set:

```dotenv
APIPLATFORM_BASE_URL=https://apiplatform.ltpcalculator.com
APIPLATFORM_BASIC_AUTH=Basic <your-private-value>
APIPLATFORM_HEADLESS=true
```

Use the complete Basic authorization value provided for your own account. Do not paste it
into source files, frontend `VITE_*` variables, issue reports, or Git commits. If a real
Basic authorization value has been exposed outside your private local environment, rotate
it with the provider before continuing.

The API base URL is read from `APIPLATFORM_BASE_URL`. It defaults to
`https://apiplatform.ltpcalculator.com`.

## Login flow

1. The backend opens `https://nseoptionchain.ltpcalculator.com/` using Selenium Chrome.
2. It collects cookies supplied by that website and the browser user-agent.
3. It copies those cookies into one shared `requests.Session`.
4. It posts an empty JSON object to `https://apiplatform-auth.ltpcalculator.com/login`
   with the configured Basic authorization value.
5. It extracts the returned JWT, applies it as a Bearer token, and saves the token and
   expiry to `backend/runtime/apiplatform/session.json`.
6. On backend startup, an unexpired saved JWT is restored without launching Chrome or
   performing another login. Expired tokens are refreshed on the next authenticated API
   request.

The runtime session directory is excluded from Git. Do not commit it or share its contents.

## Backend authentication endpoints

- `POST /api/v1/apiplatform/auth/login` — restore the saved session or establish a new one.
- `GET /api/v1/apiplatform/auth/status` — return authentication state and expiry only.

The endpoints never return the Basic authorization value, browser cookies, or JWT.

## Option-chain client

Backend services should use the shared client instead of creating their own login/session:

```python
from app.providers.apiplatform.registry import get_apiplatform_client

client = get_apiplatform_client()
payload = client.fetch_option_chain(
    symbol="NIFTY",
    expiry="YYYY-MM-DD",
    lot_size=65,
)
```

The option-chain request is sent to `GET /optionChain/fetch-data` with
`symbol`, `expiry`, and `lotSize` query parameters. The client refreshes the session
once after an upstream HTTP 401/403 response.

## Live option-chain diagnostic

Run this from the `backend` directory after configuring `.env`:

```powershell
.\.venv\Scripts\python.exe .\scripts\test_apiplatform_option_chain.py --symbol NIFTY --expiry YYYY-MM-DD --lot-size 65
```

Replace the expiry with an expiry accepted by APIPLATFORM and use the lot size for that
contract. The script creates its own backend client process. Because the persisted session file
stores the JWT but not browser cookies, the diagnostic explicitly establishes a fresh
browser-backed login before requesting data when its new session has no cookies. It then
fetches one live response and prints a bounded sample of its structure (including top-level
fields and sample `symbolMarketDirection`, `RS_Status`, and `OptionChain` structures
when present). It does not print authentication credentials or JWTs.

To save the complete response for local inspection, add:

```powershell
--save-json .\runtime\apiplatform\option-chain-sample.json
```

Treat saved response data as local diagnostic output; do not commit it unless explicitly
reviewed and sanitized.

## Browser and CORS boundary

The frontend must call only a TradingPlatformNext FastAPI endpoint. APIPLATFORM credentials,
Cloudflare cookies, and JWTs remain server-side. The existing FastAPI CORS middleware
continues to handle browser preflight requests for configured frontend origins.
