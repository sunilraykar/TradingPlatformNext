# APIPLATFORM authentication

TradingPlatformNext authenticates to APIPLATFORM from the Python backend. The browser
frontend must never receive or submit the APIPLATFORM Basic authorization value.

## Configuration

Copy `backend/.env.example` to `backend/.env` if it does not already exist, then set:

```dotenv
APIPLATFORM_BASIC_AUTH=Basic <your-private-value>
APIPLATFORM_HEADLESS=true
```

Use the complete Basic authorization value provided for your own account. Do not paste it
into source files, frontend `VITE_*` variables, issue reports, or Git commits. The value
shared in the earlier troubleshooting conversation should be considered exposed; rotate
it with the provider before configuring this integration.

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

## Backend endpoints

- `POST /api/v1/apiplatform/auth/login` — restore the saved session or establish a new one.
- `GET /api/v1/apiplatform/auth/status` — return authentication state and expiry only.

The endpoints never return the Basic authorization value, browser cookies, or JWT.

## Using the shared API client

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

## Browser and CORS boundary

The frontend calls only the TradingPlatformNext FastAPI endpoint. APIPLATFORM credentials,
Cloudflare cookies, and JWTs remain server-side. The existing FastAPI CORS middleware
continues to handle browser preflight requests for the configured frontend origins.
