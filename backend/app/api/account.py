from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from app.providers.kite.client import KiteProviderError
from app.providers.kite.registry import get_kite_provider

router = APIRouter()


class LiveOrderRequest(BaseModel):
    exchange: str = Field(min_length=2, max_length=10, pattern=r"^[A-Z]+$")
    tradingsymbol: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z0-9&._-]+$")
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=1_000_000)
    product: Literal["CNC", "MIS", "NRML"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"]
    price: float | None = Field(default=None, ge=0)
    trigger_price: float | None = Field(default=None, ge=0)
    validity: Literal["DAY", "IOC"] = "DAY"
    disclosed_quantity: int = Field(default=0, ge=0)
    tag: str | None = Field(default=None, max_length=20)
    confirm_live_action: bool = False
    confirmation_text: str = ""

    @model_validator(mode="after")
    def validate_order_prices(self):
        if self.order_type in {"LIMIT", "SL"} and (self.price is None or self.price <= 0):
            raise ValueError("A positive price is required for LIMIT and SL orders")
        if self.order_type in {"SL", "SL-M"} and (
            self.trigger_price is None or self.trigger_price <= 0
        ):
            raise ValueError("A positive trigger_price is required for SL and SL-M orders")
        if self.order_type in {"MARKET", "SL-M"} and self.price is not None:
            raise ValueError("price must be omitted for MARKET and SL-M orders")
        if self.disclosed_quantity > self.quantity:
            raise ValueError("disclosed_quantity cannot exceed quantity")
        return self


class OrderModifyRequest(BaseModel):
    quantity: int | None = Field(default=None, gt=0, le=1_000_000)
    price: float | None = Field(default=None, gt=0)
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] | None = None
    trigger_price: float | None = Field(default=None, gt=0)
    validity: Literal["DAY", "IOC"] | None = None
    disclosed_quantity: int | None = Field(default=None, ge=0)
    confirm_live_action: bool = False
    confirmation_text: str = ""

    @model_validator(mode="after")
    def require_changes(self):
        fields = ("quantity", "price", "order_type", "trigger_price", "validity", "disclosed_quantity")
        if not any(getattr(self, name) is not None for name in fields):
            raise ValueError("Provide at least one order field to modify")
        return self


class LiveActionConfirmation(BaseModel):
    confirm_live_action: bool = False
    confirmation_text: str = ""


def _provider():
    try:
        return get_kite_provider()
    except (ValueError, KiteProviderError) as exc:
        raise HTTPException(status_code=503, detail="Kite session is not configured") from exc


def _read(method: str, *args, **kwargs):
    provider = _provider()
    try:
        return getattr(provider, method)(*args, **kwargs)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _require_confirmation(confirmed: bool, text: str) -> None:
    if not confirmed or text != "CONFIRM LIVE ORDER":
        raise HTTPException(
            status_code=400,
            detail="Live broker action requires explicit confirmation text: CONFIRM LIVE ORDER",
        )


@router.get("/trading-account", tags=["trading account"], response_class=HTMLResponse)
def trading_account_ui() -> HTMLResponse:
    return HTMLResponse(_ACCOUNT_UI)


@router.get("/kite/account/summary", tags=["trading account"])
def account_summary() -> dict[str, Any]:
    provider = _provider()
    try:
        profile = provider.profile()
        margins = provider.margins()
        positions = provider.positions()
        holdings = provider.holdings()
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    day_positions = positions.get("day", []) if isinstance(positions, dict) else []
    holdings_value = sum(
        float(row.get("last_price") or 0) * int(row.get("quantity") or 0)
        for row in holdings
    )
    unrealised = sum(float(row.get("unrealised") or 0) for row in net_positions)
    realised = sum(float(row.get("realised") or 0) for row in net_positions)
    equity = margins.get("equity", {}) if isinstance(margins, dict) else {}
    available = equity.get("available", {}) if isinstance(equity, dict) else {}
    utilised = equity.get("utilised", {}) if isinstance(equity, dict) else {}
    return {
        "profile": {
            "user_id": profile.get("user_id"),
            "user_name": profile.get("user_name"),
            "email": profile.get("email"),
            "broker": "ZERODHA KITE",
        },
        "holdings_count": len(holdings),
        "net_positions_count": len(net_positions),
        "day_positions_count": len(day_positions),
        "holdings_value": round(holdings_value, 2),
        "unrealised_pnl": round(unrealised, 2),
        "realised_pnl": round(realised, 2),
        "available_cash": available.get("cash"),
        "available_margin": available.get("live_balance"),
        "utilised_margin": utilised.get("debits"),
        "margins": margins,
    }


@router.get("/kite/positions", tags=["trading account"])
def get_positions() -> dict[str, Any]:
    return {"positions": _read("positions")}


@router.get("/kite/orders", tags=["trading account"])
def get_orders() -> dict[str, Any]:
    return {"orders": _read("orders")}


@router.get("/kite/holdings", tags=["trading account"])
def get_holdings() -> dict[str, Any]:
    return {"holdings": _read("holdings")}


@router.get("/kite/margins", tags=["trading account"])
def get_margins(segment: str | None = Query(default=None, pattern="^(equity|commodity)$")) -> dict[str, Any]:
    if segment:
        return {"margins": _read("margins", segment)}
    return {"margins": _read("margins")}


@router.get("/kite/trades", tags=["trading account"])
def get_trades() -> dict[str, Any]:
    return {"trades": _read("trades")}


@router.post("/kite/orders", tags=["trading account"])
def place_live_order(payload: LiveOrderRequest) -> dict[str, Any]:
    _require_confirmation(payload.confirm_live_action, payload.confirmation_text)
    provider = _provider()
    params = payload.model_dump(exclude={
        "confirm_live_action", "confirmation_text", "tag"
    }, exclude_none=True)
    if payload.tag:
        params["tag"] = payload.tag
    try:
        order_id = provider.place_order(**params)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "submitted", "order_id": order_id, "message": "Kite accepted the order request; verify its final status in the order book."}


@router.put("/kite/orders/{variety}/{order_id}", tags=["trading account"])
def modify_live_order(
    variety: Literal["regular", "amo", "co", "iceberg", "auction"],
    order_id: str = Path(min_length=1, max_length=64),
    payload: OrderModifyRequest = ...,
) -> dict[str, Any]:
    _require_confirmation(payload.confirm_live_action, payload.confirmation_text)
    provider = _provider()
    params = payload.model_dump(exclude={"confirm_live_action", "confirmation_text"}, exclude_none=True)
    try:
        result = provider.modify_order(order_id=order_id, variety=variety.strip(), **params)
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "modified", "order_id": result, "message": "Modification request accepted; verify the updated order book."}


@router.delete("/kite/orders/{variety}/{order_id}", tags=["trading account"])
def cancel_live_order(
    variety: Literal["regular", " amo", "co", "iceberg", "auction"],
    order_id: str = Path(min_length=1, max_length=64),
    payload: LiveActionConfirmation = ...,
) -> dict[str, Any]:
    _require_confirmation(payload.confirm_live_action, payload.confirmation_text)
    provider = _provider()
    try:
        result = provider.cancel_order(order_id=order_id, variety=variety.strip())
    except KiteProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "cancel_requested", "order_id": result, "message": "Cancellation request accepted; verify final status in the order book."}


_ACCOUNT_UI = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingPlatformNext · Trading Account</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2c;--line:#29354c;--muted:#9aabc4;--text:#edf3ff;--green:#4ade80;--red:#f87171;--blue:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif}header{padding:20px 4vw;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}h1{margin:0;font-size:22px}main{max-width:1600px;margin:auto;padding:22px 4vw}.muted,small{color:var(--muted)}button,input,select{background:#0b1222;color:var(--text);border:1px solid #35435e;border-radius:7px;padding:9px 11px;font:inherit}button{cursor:pointer;background:#164e63;border-color:#0e7490;font-weight:650}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs button{background:var(--panel);border-color:var(--line)}.tabs button.active{background:#164e63;border-color:#0e7490}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:15px}.card span{display:block;color:var(--muted);font-size:12px}.card strong{display:block;font-size:21px;margin-top:6px;overflow-wrap:anywhere}.panel{margin-top:14px;overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:10px;border-bottom:1px solid #202b40}th{background:#18243a;color:#c8d7ee}td:first-child,th:first-child{text-align:left}.positive{color:var(--green)}.negative{color:var(--red)}.error{color:#fca5a5;padding:10px}.formgrid{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px}.formgrid label{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.danger{background:#7f1d1d;border-color:#b91c1c}.hidden{display:none!important}@media(max-width:800px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.formgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.cards{grid-template-columns:1fr}.formgrid{grid-template-columns:1fr}}
</style></head><body><header><div><h1>TradingPlatformNext <span style="color:var(--blue)">/ Trading Account</span></h1><small id="profile">Connecting to your Kite account…</small></div><button id="refresh">Refresh account</button></header><main>
<div id="error" class="error" hidden></div><div class="cards" id="summary"></div>
<div class="tabs" id="tabs"><button data-tab="positions" class="active">Positions</button><button data-tab="orders">Orders</button><button data-tab="holdings">Holdings</button><button data-tab="trades">Trade book</button><button data-tab="margins">Funds &amp; margins</button><button data-tab="place">Place order</button></div>
<section id="tablepanel" class="panel"><div class="muted" id="tablehint">Loading…</div><div id="table"></div></section>
<section id="placepanel" class="panel hidden"><h2>Place live Kite order</h2><p class="muted">This submits a real broker order. Check symbol, quantity, product and order type carefully. A separate confirmation is required before submission.</p>
<div class="formgrid"><label>Exchange<select id="exchange"><option>NSE</option><option>BSE</option><option>NFO</option><option>BFO</option><option>CDS</option><option>MCX</option></select></label><label>Trading symbol<input id="symbol" maxlength="100" placeholder="e.g. INFY" autocomplete="off"></label><label>Side<select id="side"><option>BUY</option><option>SELL</option></select></label><label>Quantity<input id="quantity" type="number" min="1" max="1000000" step="1" value="1"></label><label>Product<select id="product"><option>MIS</option><option>CNC</option><option>NRML</option></select></label><label>Order type<select id="ordertype"><option>MARKET</option><option>LIMIT</option><option>SL</option><option>SL-M</option></select></label><label id="pricewrap">Price<input id="price" type="number" min="0.05" step="0.05"></label><label id="triggerwrap">Trigger price<input id="trigger" type="number" min="0.05" step="0.05"></label><label>Validity<select id="validity"><option>DAY</option><option>IOC</option></select></label><label>Disclosed quantity<input id="disclosed" type="number" min="0" step="1" value="0"></label></div>
<div class="actions"><button id="submitorder" class="danger">Review live order</button></div><p class="muted">Order submission does not guarantee execution. Verify the final status in Orders. Exchange, instrument, market hours, funds, and broker rules apply.</p></section>
</main><script>
const $=id=>document.getElementById(id);let tab="positions",cache={};const endpoints={positions:"/api/v1/kite/positions",orders:"/api/v1/kite/orders",holdings:"/api/v1/kite/holdings",trades:"/api/v1/kite/trades",margins:"/api/v1/kite/margins"};
const num=v=>v===null||v===undefined||v===""?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:2});const pnl=v=>'<span class="'+(Number(v)>=0?"positive":"negative")+'">'+num(v)+'</span>';
async function api(url,options={}){const r=await fetch(url,{cache:"no-store",...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.detail||"Request failed ("+r.status+")");return d}
async function refreshSummary(){const d=await api("/api/v1/kite/account/summary");$("profile").textContent=(d.profile.user_name||d.profile.user_id||"Kite account")+" · "+d.profile.broker;$("summary").innerHTML=[["Holdings value",d.holdings_value],["Unrealised P&L",d.unrealised_pnl],["Realised P&L",d.realised_pnl],["Available margin",d.available_margin??d.available_cash]].map(([k,v])=>'<div class="card"><span>'+k+'</span><strong>'+num(v)+'</strong></div>').join("")}
function table(headers,rows){if(!rows.length)return '<p class="muted">No records returned by Kite for this section.</p>';return '<table><thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join("")+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(v=>'<td>'+v+'</td>').join("")+'</tr>').join("")+'</tbody></table>'}
async function loadTab(){if(tab==="place"){$("tablepanel").classList.add("hidden");$("placepanel").classList.remove("hidden");return}$("tablepanel").classList.remove("hidden");$("placepanel").classList.add("hidden");$("tablehint").textContent="Loading "+tab+"…";$("table").innerHTML="";try{const d=cache[tab]||await api(endpoints[tab]);cache[tab]=d;let rows=[],heads=[];
if(tab==="positions"){const p=d.positions||{};heads=["Symbol","Exchange","Product","Quantity","Average","LTP","P&L","Day P&L"];rows=(p.net||[]).map(x=>[x.tradingsymbol,x.exchange,x.product,num(x.quantity),num(x.average_price),num(x.last_price),pnl(x.pnl),pnl(x.day_pnl)])}
if(tab==="orders"){heads=["Order ID","Time","Symbol","Side","Type","Qty","Filled","Price","Status","Action"];rows=(d.orders||[]).map(x=>[x.order_id,x.order_timestamp||x.exchange_timestamp||"—",x.tradingsymbol,x.transaction_type,x.order_type,num(x.quantity),num(x.filled_quantity),num(x.price),x.status,x.status==="OPEN"||x.status==="TRIGGER PENDING"?'<button class="modify" data-id="'+x.order_id+'" data-variety="'+(x.variety||"regular")+'">Modify</button> <button class="cancel danger" data-id="'+x.order_id+'" data-variety="'+(x.variety||"regular")+'">Cancel</button>':"—"])}
if(tab==="holdings"){heads=["Symbol","Exchange","Quantity","T1 Qty","Average","LTP","Current value","P&L","Day P&L"];rows=(d.holdings||[]).map(x=>[x.tradingsymbol,x.exchange,num(x.quantity),num(x.t1_quantity),num(x.average_price),num(x.last_price),num((x.last_price||0)*(x.quantity||0)),pnl(x.pnl),pnl(x.day_change)])}
if(tab==="trades"){heads=["Trade ID","Time","Symbol","Side","Exchange","Product","Quantity","Average price","Order ID"];rows=(d.trades||[]).map(x=>[x.trade_id,x.fill_timestamp||x.exchange_timestamp||"—",x.tradingsymbol,x.transaction_type,x.exchange,x.product,num(x.quantity),num(x.average_price),x.order_id])}
if(tab==="margins"){const m=d.margins||{};heads=["Segment","Field","Value"];rows=Object.entries(m).flatMap(([segment,values])=>Object.entries(values||{}).map(([group,fields])=>typeof fields==="object"&&fields!==null?Object.entries(fields).map(([k,v])=>[segment+" / "+group,k,num(v)]):[[segment,group,num(fields)]]).flat());}
$("table").innerHTML=table(heads,rows);$("tablehint").textContent=tab==="orders"?"Current-day order book from Kite.":"Live account data from Kite.";
document.querySelectorAll(".cancel").forEach(b=>b.addEventListener("click",()=>cancelOrder(b.dataset.id,b.dataset.variety)));document.querySelectorAll(".modify").forEach(b=>b.addEventListener("click",()=>modifyOrder(b.dataset.id,b.dataset.variety)));
}catch(e){$("tablehint").textContent="";$("error").hidden=false;$("error").textContent=e.message}}
async function refresh(){ $("error").hidden=true;cache={};try{await refreshSummary();await loadTab()}catch(e){$("error").hidden=false;$("error").textContent=e.message}}
async function modifyOrder(id,variety){const q=prompt("New quantity (leave blank to keep unchanged):");if(q===null)return;const p=prompt("New price (leave blank to keep unchanged):");if(p===null)return;const t=prompt("New trigger price (leave blank to keep unchanged):");if(t===null)return;const body={};if(q.trim()!=="")body.quantity=Number(q);if(p.trim()!=="")body.price=Number(p);if(t.trim()!=="")body.trigger_price=Number(t);if(!Object.keys(body).length){alert("No changes entered.");return}if(body.quantity!==undefined&&(!Number.isInteger(body.quantity)||body.quantity<1)){alert("Quantity must be a positive whole number.");return}if(body.price!==undefined&&!(body.price>0)){alert("Price must be positive.");return}if(body.trigger_price!==undefined&&!(body.trigger_price>0)){alert("Trigger price must be positive.");return}if(!confirm("Modify LIVE Kite order "+id+"?\n\nChanges: "+JSON.stringify(body)+"\n\nThis sends a real broker request."))return;const phrase=prompt('Type CONFIRM LIVE ORDER to submit modification for '+id);if(phrase!=="CONFIRM LIVE ORDER")return;body.confirm_live_action=true;body.confirmation_text=phrase;try{await api("/api/v1/kite/orders/"+encodeURIComponent(variety)+"/"+encodeURIComponent(id),{method:"PUT",body:JSON.stringify(body)});cache={};await loadTab();alert("Modification request sent. Verify the updated order status.")}catch(e){alert(e.message)}}\nasync function cancelOrder(id,variety){if(!confirm("Cancel LIVE Kite order "+id+"? This sends a real cancellation request."))return;const phrase=prompt('Type CONFIRM LIVE ORDER to submit cancellation for '+id);if(phrase!=="CONFIRM LIVE ORDER")return;try{await api("/api/v1/kite/orders/"+encodeURIComponent(variety)+"/"+encodeURIComponent(id),{method:"DELETE",body:JSON.stringify({confirm_live_action:true,confirmation_text:phrase})});cache={};await loadTab();alert("Cancellation request sent. Verify the final order status.")}catch(e){alert(e.message)}}
$("tabs").addEventListener("click",e=>{const b=e.target.closest("button[data-tab]");if(!b)return;tab=b.dataset.tab;document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("active",x===b));loadTab()});$("refresh").addEventListener("click",refresh);
function updatePriceFields(){const t=$("ordertype").value;$("pricewrap").classList.toggle("hidden",t==="MARKET"||t==="SL-M");$("triggerwrap").classList.toggle("hidden",t==="MARKET"||t==="LIMIT")}
$("ordertype").addEventListener("change",updatePriceFields);updatePriceFields();
$("submitorder").addEventListener("click",async()=>{const exchange=$("exchange").value,symbol=$("symbol").value.trim().toUpperCase(),side=$("side").value,quantity=Number($("quantity").value),product=$("product").value,order_type=$("ordertype").value,price=$("price").value===""?null:Number($("price").value),trigger_price=$("trigger").value===""?null:Number($("trigger").value),disclosed_quantity=Number($("disclosed").value),validity=$("validity").value;
if(!/^[A-Z0-9&._-]{1,100}$/.test(symbol)){alert("Enter a valid trading symbol.");return}if(!Number.isInteger(quantity)||quantity<1||quantity>1000000){alert("Quantity must be a positive whole number.");return}if(disclosed_quantity<0||!Number.isInteger(disclosed_quantity)||disclosed_quantity>quantity){alert("Disclosed quantity must be a whole number between zero and quantity.");return}if(["LIMIT","SL"].includes(order_type)&&!(price>0)){alert("Enter a positive limit price.");return}if(["SL","SL-M"].includes(order_type)&&!(trigger_price>0)){alert("Enter a positive trigger price.");return}
const draft={exchange,tradingsymbol:symbol,transaction_type:side,quantity,product,order_type,validity,disclosed_quantity};if(price!==null)draft.price=price;if(trigger_price!==null)draft.trigger_price=trigger_price;
const summary=side+" "+quantity+" "+symbol+" ("+exchange+") · "+product+" · "+order_type+(price!==null?" · price "+price:"")+(trigger_price!==null?" · trigger "+trigger_price:"");if(!confirm("REAL BROKER ORDER\n\n"+summary+"\n\nReview every field. Continue to final confirmation?"))return;const phrase=prompt('Type CONFIRM LIVE ORDER to send this real order:\n'+summary);if(phrase!=="CONFIRM LIVE ORDER")return;draft.confirm_live_action=true;draft.confirmation_text=phrase;try{const result=await api("/api/v1/kite/orders",{method:"POST",body:JSON.stringify(draft)});alert("Order request accepted. Order ID: "+result.order_id+"\nCheck Orders for its final status.");tab="orders";document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("active",x.dataset.tab===tab));cache={};await loadTab()}catch(e){alert("Order was not confirmed as successful: "+e.message)}});
refresh();setInterval(()=>{if(tab!=="place"){cache={};refreshSummary().catch(()=>{});loadTab()}},10000);
</script></body></html>
"""
