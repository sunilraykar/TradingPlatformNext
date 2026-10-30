import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_engine
from app.providers.kite.client import KiteProvider, KiteProviderError
from app.providers.kite.registry import configure_active_kite_session, get_kite_provider
from app.services import live_market_data
from app.services.instrument_master import import_instrument_master
from app.services.kite_sessions import KiteSessionStoreError, save_session
from app.services.live_quotes import live_quotes
from app.services.option_chain import get_option_chain

router = APIRouter()


_OPTION_CHAIN_UI = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingPlatformNext · Option Chain</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2c;--line:#29354c;--muted:#9aabc4;--text:#edf3ff;--green:#22c55e;--red:#f87171;--blue:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:22px 4vw;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:22px;margin:0}small,.muted{color:var(--muted)}main{padding:24px 4vw;max-width:1600px;margin:auto}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end;background:var(--panel);border:1px solid var(--line);padding:16px;border-radius:12px}
label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px}select,input,button{background:#0b1222;color:var(--text);border:1px solid #35435e;border-radius:7px;padding:10px 12px;font:inherit}
button{background:#164e63;border-color:#0e7490;cursor:pointer;font-weight:650}button:hover{background:#155e75}
.status{display:flex;gap:8px;align-items:center;padding:8px 12px;border:1px solid var(--line);border-radius:99px}.dot{width:8px;height:8px;border-radius:50%;background:#fbbf24}.dot.live{background:var(--green)}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}.metric{background:var(--panel);border:1px solid var(--line);padding:14px;border-radius:10px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;font-size:20px;margin-top:5px}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:900px;font-variant-numeric:tabular-nums}th,td{padding:10px 12px;text-align:right;border-bottom:1px solid #202b40;white-space:nowrap}thead th{background:#18243a;position:sticky;top:0;z-index:1}th.strike,td.strike{text-align:center;background:#172239;font-weight:700}.callhead{color:#86efac}.puthead{color:#fca5a5}.empty{color:var(--muted);text-align:center;padding:36px}.error{color:#fca5a5;padding:12px}.foot{padding:12px;color:var(--muted);font-size:12px}
@media(max-width:700px){.metrics{grid-template-columns:1fr}.controls>*{flex:1}.metrics strong{font-size:18px}}
</style></head>
<body>
<header><div><h1>TradingPlatformNext <span style="color:var(--blue)">/ Option Chain</span></h1><small>NIFTY 50 · BANK NIFTY · Kite live market data</small></div><div class="status"><span id="dot" class="dot"></span><span id="connection">Checking feed…</span></div></header>
<main>
<div class="controls">
<label>Underlying<select id="underlying"><option value="NIFTY">NIFTY 50</option><option value="BANKNIFTY">BANK NIFTY</option></select></label>
<label>Expiry<select id="expiry"><option value="">Nearest available</option></select></label>
<label>Strikes each side<select id="count"><option>5</option><option selected>10</option><option>15</option><option>20</option><option>30</option></select></label>
<button id="refresh">Refresh chain</button>
<span class="muted" id="updated">Waiting for first update…</span>
</div>
<div class="metrics">
<div class="metric"><span>Underlying</span><strong id="spot">—</strong></div>
<div class="metric"><span>Selected expiry</span><strong id="expiryLabel">—</strong></div>
<div class="metric"><span>Live subscriptions</span><strong id="subs">—</strong></div>
</div>
<div id="error" class="error" hidden></div>
<div class="tablewrap"><table><thead><tr><th colspan="4" class="callhead">CALLS (CE)</th><th class="strike">STRIKE</th><th colspan="4" class="puthead">PUTS (PE)</th></tr><tr><th class="callhead">LTP</th><th class="callhead">Volume</th><th class="callhead">OI</th><th class="callhead">Symbol</th><th class="strike">Price</th><th class="puthead">Symbol</th><th class="puthead">OI</th><th class="puthead">Volume</th><th class="puthead">LTP</th></tr></thead><tbody id="rows"><tr><td colspan="9" class="empty">Load an option chain to begin.</td></tr></tbody></table></div>
<div class="foot">Prices are supplied by your configured Kite session. A dash means no tick has been received yet. This screen does not place orders or provide a trading recommendation.</div>
</main>
<script>
const $=id=>document.getElementById(id);let busy=false,selectedExpiry="";
function fmt(v){return v===null||v===undefined||v===""?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:2})}
function side(v){if(!v)return ["—","—","—","—"];return [fmt(v.last_price),fmt(v.volume),fmt(v.oi),v.tradingsymbol||"—"]}
async function loadChain(){if(busy)return;busy=true;$("error").hidden=true;try{
const u=$("underlying").value,e=$("expiry").value,n=$("count").value;
const p=new URLSearchParams({underlying:u,strike_count:n});if(e)p.set("expiry",e);
const res=await fetch("/api/v1/kite/option-chain?"+p.toString(),{cache:"no-store"});
const data=await res.json();if(!res.ok)throw new Error(data.detail||"Could not load option chain");
if(!selectedExpiry||selectedExpiry!==u){selectedExpiry=u;const exp=$("expiry");exp.innerHTML="";for(const x of data.available_expiries||[]){const o=document.createElement("option");o.value=x;o.textContent=x;exp.appendChild(o)}if(data.expiry)exp.value=data.expiry}
$("spot").textContent=fmt(data.spot_price);$("expiryLabel").textContent=data.expiry||"—";$("subs").textContent=(data.live_subscription?.subscription_count??"—")+" instruments";
$("rows").innerHTML="";for(const r of data.strikes||[]){const c=side(r.CE),p2=side(r.PE),tr=document.createElement("tr");tr.innerHTML="<td>"+c[0]+"</td><td>"+c[1]+"</td><td>"+c[2]+"</td><td>"+c[3]+"</td><td class='strike'>"+fmt(r.strike)+"</td><td>"+p2[3]+"</td><td>"+p2[2]+"</td><td>"+p2[1]+"</td><td>"+p2[0]+"</td>";$("rows").appendChild(tr)}
if(!data.strikes?.length)$("rows").innerHTML="<tr><td colspan='9' class='empty'>No strikes returned for this expiry.</td></tr>";
$("updated").textContent="Updated "+new Date().toLocaleTimeString();const status=await fetch("/api/v1/kite/market-data/status",{cache:"no-store"}).then(x=>x.json());$("dot").classList.toggle("live",status.connected===true);$("connection").textContent=status.connected?"WebSocket connected":"Feed: "+status.state;
}catch(err){$("error").hidden=false;$("error").textContent=err.message;$("dot").classList.remove("live");$("connection").textContent="Feed unavailable"}finally{busy=false}}
$("underlying").addEventListener("change",()=>{selectedExpiry="";loadChain()});$("expiry").addEventListener("change",loadChain);$("count").addEventListener("change",loadChain);$("refresh").addEventListener("click",loadChain);loadChain();setInterval(loadChain,2000);
</script></body></html>
"""


@router.get("/option-chain", tags=["market intelligence"], response_class=HTMLResponse)
def option_chain_ui() -> HTMLResponse:
    """Serve the first browser-based option-chain screen."""
    return HTMLResponse(_OPTION_CHAIN_UI)




class SessionExchangeRequest(BaseModel):
    request_token: str = Field(min_length=1, max_length=256)


class QuoteResponse(BaseModel):
    instrument_token: int
    last_price: float
    last_trade_time: str | None = None
    received_at: str


class SubscriptionRequest(BaseModel):
    instrument_tokens: list[int] = Field(min_length=1, max_length=500)
    mode: str = "quote"


class UnsubscribeRequest(BaseModel):
    instrument_tokens: list[int] = Field(min_length=1, max_length=500)


def _exchange_and_save(request_token: str) -> str:
    settings = get_settings()
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Kite API credentials are not configured")
    try:
        session = KiteProvider.exchange_request_token(
            api_key=settings.kite_api_key,
            api_secret=settings.kite_api_secret,
            request_token=request_token,
        )
        save_session(session.user_id, session.access_token)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KiteSessionStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    configure_active_kite_session(session.access_token)
    try:
        live_market_data.start()
    except (RuntimeError, ValueError, OSError):
        # Authentication remains valid even if the WebSocket cannot connect yet.
        logging.getLogger(__name__).exception("Kite session saved but WebSocket startup failed")
    return session.user_id


class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any]


@router.get("/settings", tags=["settings"])
def read_application_settings() -> dict[str, Any]:
    """Read application UI settings from MySQL app_settings."""
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(text(
                "SELECT setting_key, setting_value FROM app_settings WHERE setting_key LIKE 'ui.%'"
            )).mappings().all()
        result: dict[str, Any] = {}
        for row in rows:
            key = str(row["setting_key"])[3:]
            value = row["setting_value"]
            if isinstance(value, str):
                import json
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass
            result[key] = value
        return {"settings": result}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Unable to read settings from MySQL. Check database configuration and migrations.") from exc


@router.put("/settings", tags=["settings"])
def write_application_settings(request: SettingsUpdateRequest) -> dict[str, Any]:
    """Persist non-secret application UI settings in MySQL."""
    import json
    import re
    from datetime import time

    time_keys = {
        "autoTrenderEntryStartTime": "10:00",
        "autoTrenderEntryEndTime": "14:00",
    }
    try:
        with get_engine().begin() as connection:
            existing_rows = connection.execute(text(
                "SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN (:start_key, :end_key)"
            ), {"start_key": "ui.autoTrenderEntryStartTime", "end_key": "ui.autoTrenderEntryEndTime"}).mappings().all()
            merged_times = dict(time_keys)
            for row in existing_rows:
                stored = row["setting_value"]
                if isinstance(stored, str):
                    try:
                        stored = json.loads(stored)
                    except (TypeError, ValueError):
                        pass
                merged_times[str(row["setting_key"])[3:]] = stored
            for key in time_keys:
                if key in request.settings:
                    merged_times[key] = request.settings[key]
            if any(key in request.settings for key in time_keys):
                for key, label in (("autoTrenderEntryStartTime", "start"), ("autoTrenderEntryEndTime", "end")):
                    candidate = merged_times[key]
                    if not isinstance(candidate, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", candidate):
                        raise HTTPException(status_code=422, detail=f"AutoTrender entry {label} time must use valid 24-hour HH:MM format")
                if time.fromisoformat(merged_times["autoTrenderEntryEndTime"]) <= time.fromisoformat(merged_times["autoTrenderEntryStartTime"]):
                    raise HTTPException(status_code=422, detail="AutoTrender entry end time must be later than the start time")
            for key, value in request.settings.items():
                if not isinstance(key, str) or not key or len(key) > 180:
                    raise HTTPException(status_code=422, detail="Invalid setting key")
                if key.lower().endswith(("password", "secret", "token", "api_key", "apikey")):
                    raise HTTPException(status_code=422, detail="Secrets and provider credentials must remain in backend environment configuration")
                connection.execute(text(
                    "INSERT INTO app_settings (setting_key, setting_value, description, is_secret) "
                    "VALUES (:key, CAST(:value AS JSON), 'Trading platform UI setting', FALSE) "
                    "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), "
                    "description = VALUES(description), is_secret = FALSE, version = version + 1"
                ), {"key": "ui." + key, "value": json.dumps(value, separators=(",", ":"))})
        return {"saved": True, "count": len(request.settings)}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Unable to save settings to MySQL. Check database configuration and migrations.") from exc


@router.get("/kite/login-url", tags=["kite"])
def kite_login_url() -> dict[str, str]:
    settings = get_settings()
    if not settings.kite_api_key:
        raise HTTPException(status_code=503, detail="KITE_API_KEY is not configured")
    return {"login_url": KiteProvider.login_url(settings.kite_api_key)}


@router.get("/kite/callback", tags=["kite"], response_class=HTMLResponse)
def kite_login_callback(
    request_token: str = Query(..., min_length=1, max_length=256),
    status: str = Query(default="success"),
) -> HTMLResponse:
    if status != "success":
        return HTMLResponse(
            "<h2>Kite login was not completed.</h2><p>You may close this tab and retry from TradingPlatformNext.</p>",
            status_code=401,
        )
    _exchange_and_save(request_token)
    return HTMLResponse(
        "<!doctype html><html><head><title>TradingPlatformNext</title></head>"
        "<body><h2>Kite login successful</h2>"
        "<p>Your session was saved securely. You may return to TradingPlatformNext.</p></body></html>"
    )


@router.post("/kite/session", tags=["kite"])
def exchange_session(payload: SessionExchangeRequest) -> dict[str, str]:
    user_id = _exchange_and_save(payload.request_token)
    return {"status": "authenticated", "user_id": user_id}


@router.get("/kite/session/status", tags=["kite"])
def kite_session_status() -> dict[str, str | bool]:
    provider = get_kite_provider()
    try:
        profile = provider.profile()
    except KiteProviderError:
        return {"authenticated": False, "status": "login_required"}
    return {
        "authenticated": True,
        "status": "authenticated",
        "user_id": str(profile.get("user_id", "")),
    }


@router.get("/kite/market-data/status", tags=["kite"])
def market_data_status() -> dict:
    return live_market_data.status()

@router.get("/kite/market-data/autotrender-indices", tags=["kite"])
def autotrender_index_quotes() -> dict:
    """Read latest AutoTrender index ticks from the Kite WebSocket quote cache."""
    from app.db.session import get_engine
    from sqlalchemy import text

    symbol_map = {
        ("NSE", "NIFTY 50"): "NIFTY",
        ("NSE", "NIFTY BANK"): "BANKNIFTY",
        ("NSE", "NIFTY FIN SERVICE"): "FINNIFTY",
        ("NSE", "NIFTY MID SELECT"): "MIDCPNIFTY",
        ("BSE", "SENSEX"): "SENSEX",
        ("BSE", "BANKEX"): "BANKEX",
    }
    token_rows = {}
    with get_engine().connect() as connection:
        rows = connection.execute(text("""
            SELECT instrument_token, exchange, tradingsymbol
            FROM instruments
            WHERE source = 'KITE' AND is_active = TRUE
        """)).mappings().all()
    for row in rows:
        symbol = symbol_map.get((row["exchange"], row["tradingsymbol"]))
        if symbol:
            token_rows[symbol] = int(row["instrument_token"])
    result = {}
    for symbol, token in token_rows.items():
        tick = live_quotes.get(token)
        if not tick:
            continue
        ohlc = tick.get("ohlc") or {}
        last_price = tick.get("last_price")
        close = ohlc.get("close")
        change_percent = ((float(last_price) - float(close)) / float(close) * 100) if last_price is not None and close not in (None, 0) else None
        result[symbol] = {
            "instrument_token": token,
            "last_price": last_price,
            "open": ohlc.get("open"),
            "high": ohlc.get("high"),
            "low": ohlc.get("low"),
            "close": close,
            "day_change_percentage": change_percent,
            "received_at": tick.get("received_at"),
        }
    return {
        "source": "kite_websocket_cache",
        "connected": live_market_data.status().get("connected", False),
        "data": result,
    }


@router.get("/kite/market-data/runtime", tags=["kite"])
def market_data_runtime() -> dict:
    """Read-only diagnostics for subscribed instruments and in-memory quote/trade caches."""
    from app.services import trade_terminal
    market = live_market_data.status()
    quotes = live_quotes.snapshot()
    trades = trade_terminal.list_trades()
    active = [trade for trade in trades if str(trade.get("status", "OPEN")).upper() not in {"CLOSED", "CANCELLED", "REJECTED", "EXPIRED"}]
    subscriptions = []
    for item in market.get("subscriptions", []):
        token = int(item["instrument_token"])
        matching = [trade for trade in active if int(trade.get("instrument_token") or 0) == token]
        subscriptions.append({
            **item,
            "quote": quotes.get(token),
            "open_trade_count": len(matching),
            "trade_symbols": [trade.get("instrument_symbol") for trade in matching],
        })
    return {
        "market_data": market,
        "cache": {
            "quote_count": len(quotes),
            "cached_quote_tokens": sorted(quotes),
            "open_trade_count": len(active),
            "trade_count": len(trades),
        },
        "subscriptions": subscriptions,
        "quotes": [{"instrument_token": token, **quote} for token, quote in sorted(quotes.items())],
    }


@router.post("/kite/market-data/start", tags=["kite"])
def start_market_data() -> dict:
    try:
        return live_market_data.start()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not start Kite WebSocket") from exc


@router.post("/kite/market-data/subscribe", tags=["kite"])
def subscribe_market_data(payload: SubscriptionRequest) -> dict:
    try:
        return live_market_data.subscribe(payload.instrument_tokens, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not subscribe to Kite instruments") from exc


@router.post("/kite/market-data/unsubscribe", tags=["kite"])
def unsubscribe_market_data(payload: UnsubscribeRequest) -> dict:
    return live_market_data.unsubscribe(payload.instrument_tokens)


@router.get("/kite/quote/{instrument_token}", tags=["kite"])
def get_quote(instrument_token: int) -> dict:
    cached = live_quotes.get(instrument_token)
    if cached:
        return cached
    provider = get_kite_provider()
    try:
        return provider.quote(instrument_token)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/kite/quotes", tags=["kite"])
def get_quotes(
    instrument_tokens: list[int] = Query(..., min_length=1, max_length=200),  # noqa: B008
) -> dict:
    provider = get_kite_provider()
    try:
        result = provider.quotes(instrument_tokens)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"quotes": result}



@router.post("/kite/instruments/import", tags=["kite"])
def import_kite_instruments() -> dict:
    """Download and persist Kite's complete instrument master."""
    try:
        return import_instrument_master()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kite/market-data/subscribe-portfolio", tags=["kite"])
def subscribe_portfolio_market_data() -> dict:
    """Subscribe all current holdings and non-flat net positions to live WebSocket ticks."""
    provider = get_kite_provider()
    try:
        holdings = provider.holdings()
        positions = provider.positions()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    portfolio: dict[tuple[str, str], dict[str, Any]] = {}
    for row in holdings:
        symbol = str(row.get("tradingsymbol") or "").strip()
        exchange = str(row.get("exchange") or "NSE").strip().upper()
        if symbol and int(row.get("quantity") or 0) != 0:
            portfolio[(exchange, symbol)] = {
                "exchange": exchange, "tradingsymbol": symbol, "source": "Holding",
                "quantity": int(row.get("quantity") or 0),
            }

    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    for row in net_positions:
        symbol = str(row.get("tradingsymbol") or "").strip()
        exchange = str(row.get("exchange") or "").strip().upper()
        quantity = int(row.get("quantity") or 0)
        if symbol and exchange and quantity != 0:
            key = (exchange, symbol)
            if key in portfolio:
                portfolio[key]["source"] = "Holding + Position"
            else:
                portfolio[key] = {
                    "exchange": exchange, "tradingsymbol": symbol,
                    "source": "Open Position", "quantity": quantity,
                }

    if not portfolio:
        return {"subscribed": [], "missing_instrument_master": [], "subscription": live_market_data.status()}

    clauses = []
    params: dict[str, Any] = {}
    for index, (exchange, symbol) in enumerate(portfolio):
        clauses.append(f"(exchange = :exchange_{index} AND tradingsymbol = :symbol_{index})")
        params[f"exchange_{index}"] = exchange
        params[f"symbol_{index}"] = symbol
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT instrument_token, exchange, tradingsymbol, name, instrument_type "
                    "FROM instruments WHERE is_active = TRUE AND (" + " OR ".join(clauses) + ")"
                ),
                params,
            ).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Instrument database unavailable") from None

    found = {(str(row["exchange"]).upper(), str(row["tradingsymbol"]).upper()): row for row in rows}
    subscribed = []
    missing = []
    tokens = []
    for key, item in portfolio.items():
        row = found.get((key[0].upper(), key[1].upper()))
        if row is None:
            missing.append(item)
            continue
        tokens.append(int(row["instrument_token"]))
        subscribed.append({
            **item,
            "instrument_token": int(row["instrument_token"]),
            "name": row["name"],
            "instrument_type": row["instrument_type"],
        })

    try:
        subscription = live_market_data.subscribe(tokens, mode="quote") if tokens else live_market_data.status()
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail="Could not subscribe portfolio instruments") from exc
    return {
        "subscribed": subscribed,
        "missing_instrument_master": missing,
        "subscription": subscription,
    }


@router.get("/kite/orders", tags=["kite"])
def kite_orders() -> dict:
    try:
        return {"orders": get_kite_provider().orders()}
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/kite/positions", tags=["kite"])
def kite_positions() -> dict:
    try:
        return get_kite_provider().positions()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/kite/holdings", tags=["kite"])
def kite_holdings() -> dict:
    try:
        provider = get_kite_provider()
        holdings = provider.holdings()
        # Kite holdings provide close/day-change values, but not today's OHLC.
        # Enrich each holding from the live quote endpoint so the UI can show
        # Open, High and Low alongside the existing portfolio values.
        quote_keys = [
            f"{(holding.get('exchange') or 'NSE')}:{holding.get('tradingsymbol')}"
            for holding in holdings
            if holding.get("tradingsymbol")
        ]
        quotes = {}
        if quote_keys:
            try:
                quotes = provider.quotes(quote_keys)
            except KiteProviderError:
                # Keep holdings available if live quote enrichment temporarily fails.
                quotes = {}
        for holding in holdings:
            key = f"{(holding.get('exchange') or 'NSE')}:{holding.get('tradingsymbol')}"
            quote = quotes.get(key) or {}
            ohlc = quote.get("ohlc") or {}
            holding["open"] = ohlc.get("open")
            holding["high"] = ohlc.get("high")
            holding["low"] = ohlc.get("low")
            holding["close"] = holding.get("close_price", ohlc.get("close"))
            holding["day_change_percentage"] = holding.get(
                "day_change_percentage", quote.get("change")
            )
        return {"holdings": holdings}
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/kite/funds", tags=["kite"])
def kite_funds() -> dict:
    try:
        return get_kite_provider().margins()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/instruments", tags=["instruments"])
def list_instruments(
    search: str | None = Query(default=None, min_length=1, max_length=100),
    symbol_prefix: str | None = Query(default=None, min_length=1, max_length=1, pattern="^[A-Z]$"),
    expiry_filter: str | None = Query(default=None, pattern="^(WITH_EXPIRY|NO_EXPIRY)$"),
    exchange: str | None = Query(default=None, max_length=30),
    segment: str | None = Query(default=None, max_length=40),
    instrument_type: str | None = Query(default=None, max_length=30),
    option_type: str | None = Query(default=None, pattern="^(CE|PE)$"),
    category: str | None = Query(default=None, pattern="^(EQUITY|INDICES|OPTIONS|FUTURES)$"),
    expiry: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Search the active instrument master, including expiry/option filtering."""
    clauses = ["is_active = TRUE"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if search:
        clauses.append("(tradingsymbol LIKE :search OR name LIKE :search)")
        params["search"] = f"%{search.strip()}%"
    if symbol_prefix:
        clauses.append("UPPER(tradingsymbol) LIKE :symbol_prefix")
        params["symbol_prefix"] = f"{symbol_prefix.upper()}%"
    if expiry_filter == "WITH_EXPIRY":
        clauses.append("expiry IS NOT NULL")
    elif expiry_filter == "NO_EXPIRY":
        clauses.append("expiry IS NULL")
    if exchange:
        clauses.append("exchange = :exchange")
        params["exchange"] = exchange.upper()
    if segment:
        clauses.append("segment = :segment")
        params["segment"] = segment.upper()
    if instrument_type:
        clauses.append("UPPER(instrument_type) = :instrument_type")
        params["instrument_type"] = instrument_type.upper()
    if option_type:
        clauses.append("UPPER(instrument_type) = :option_type")
        params["option_type"] = option_type.upper()
    if category:
        # Categorize from Kite's canonical segment and instrument type fields.
        # Use parentheses so the category predicate composes safely with other filters.
        category_predicates = {
            "EQUITY": "(UPPER(COALESCE(segment, '')) IN ('NSE', 'BSE', 'NSE-CM', 'BSE-CM') AND UPPER(COALESCE(instrument_type, '')) IN ('EQ', 'EQUITY'))",
            "INDICES": "(UPPER(COALESCE(segment, '')) = 'INDICES' OR UPPER(COALESCE(instrument_type, '')) = 'INDEX')",
            "OPTIONS": "(UPPER(COALESCE(segment, '')) LIKE '%OPT%' OR UPPER(COALESCE(instrument_type, '')) IN ('CE', 'PE'))",
            "FUTURES": "(UPPER(COALESCE(segment, '')) LIKE '%FUT%' OR UPPER(COALESCE(instrument_type, '')) = 'FUT')",
        }
        clauses.append(category_predicates[category])
    if expiry:
        try:
            from datetime import date

            params["expiry"] = date.fromisoformat(expiry)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="expiry must use YYYY-MM-DD format") from exc
        clauses.append("expiry = :expiry")

    where_sql = " AND ".join(clauses)
    try:
        with get_engine().connect() as connection:
            total = connection.execute(
                text(f"SELECT COUNT(*) FROM instruments WHERE {where_sql}"), params
            ).scalar_one()
            rows = connection.execute(
                text(
                    "SELECT instrument_token, exchange, tradingsymbol, name, instrument_type, "
                    "segment, expiry, strike, lot_size, tick_size, instrument_version, updated_at "
                    f"FROM instruments WHERE {where_sql} "
                    "ORDER BY exchange, tradingsymbol LIMIT :limit OFFSET :offset"
                ),
                params,
            ).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Instrument database unavailable") from None

    instruments = [
        {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()}
        for row in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "instruments": instruments}


@router.get("/kite/option-chain", tags=["kite"])
def kite_option_chain(
    underlying: str = Query(default="NIFTY", pattern="^(NIFTY|BANKNIFTY)$"),
    expiry: str | None = Query(default=None, max_length=10),
    strike_count: int = Query(default=10, ge=1, le=30),
) -> dict:
    """Return a live-updating option-chain slice and subscribe its option tokens."""
    try:
        return get_option_chain(underlying, expiry, strike_count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Instrument database unavailable") from None


@router.get("/health/db", tags=["health"])
def database_health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None

    return {"status": "ok", "database": "connected"}
