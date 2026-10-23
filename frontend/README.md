# AutoTrender frontend

Read-only React/Vite dashboard for the backend's AutoTrender market-data endpoints.

## Local development (Windows PowerShell)

Start the FastAPI backend in one terminal:

```powershell
cd C:\Users\sunil\TradingPlatformNext\backend
python -m uvicorn app.main:app --reload
```

In a second terminal, start the frontend:

```powershell
cd C:\Users\sunil\TradingPlatformNext\frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Open the local URL printed by Vite (normally `http://127.0.0.1:5173`). The Vite development server proxies `/api` and `/health` to `http://127.0.0.1:8000`. Override the proxy target with `VITE_BACKEND_PROXY_TARGET` if needed.

The dashboard uses the backend's configured AutoTrender credentials through `POST /api/v1/autotrender/auth/login`; credentials must not be entered into or stored by the frontend. Check `backend/.env.example` for backend configuration.

## Build

```powershell
npm run build
```

The generated static site is written to `dist/`. Production hosting must route API requests to the FastAPI backend or set `VITE_API_BASE_URL` to the API origin before building.

## Scope

- FINNIFTY and SENSEX index selector
- Configurable intraday interval and intraday table
- Option change-in-open-interest graph and series summaries
- AutoTrender connection status and error display
- No broker order placement
