# TradingPlatformNext

Clean-room rebuild of TradingPlatform. The legacy repository is a feature reference only; its architecture and Firebase data model are not reused.

## First milestone: Kite foundation

- Kite Connect REST client and Ticker adapter
- MySQL-backed application configuration/session persistence (schema included)
- In-memory live quote cache
- FastAPI endpoints for health, Kite session exchange, quotes, and subscriptions
- No Firebase dependency
- No broker orders enabled in this first milestone

## Local setup

1. Install Python 3.11.
2. Create a new MySQL database, for example `trading_platform_v2`.
3. Copy `backend/.env.example` to `backend/.env` and set credentials.
4. Create a virtual environment and install dependencies:

   ```powershell
   cd backend
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -e .[dev]
   ```

5. Apply the initial SQL schema to the new database:

   ```powershell
   mysql -u root -p trading_platform_v2 < ..\database\migrations\001_initial.sql
   ```

6. Start the API:

   ```powershell
   uvicorn app.main:app --reload
   ```

The Kite API key/secret and MySQL credentials must remain local in `.env`; never commit secrets.

## Current scope and safety

This milestone does not place orders. It establishes Kite authentication, read-only market data, WebSocket caching, and a MySQL foundation. Automated execution will be implemented only after market-data correctness, session recovery, and persistence tests pass.
