"""Temporary browser screen for testing Kite WebSocket subscriptions.

This page is intentionally isolated from the trading-account UI and can be removed
once live symbol subscriptions have been verified.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_SUBSCRIPTION_TEST_UI = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Temporary · Kite Subscription Test</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2c;--line:#29354c;--muted:#9aabc4;--text:#edf3ff;--green:#4ade80;--red:#f87171;--blue:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,Segoe UI,sans-serif}
header{padding:22px 4vw;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:22px;margin:0}main{max-width:1300px;margin:auto;padding:24px 4vw}.muted,small{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.controls{display:flex;gap:10px;align-items:end;flex-wrap:wrap}label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px}
input,select,button{font:inherit;color:var(--text);background:#0b1222;border:1px solid #35435e;border-radius:7px;padding:10px 12px}
input{min-width:260px}button{cursor:pointer;background:#164e63;border-color:#0e7490;font-weight:650}button:disabled{opacity:.45;cursor:not-allowed}
button.remove{background:#451a24;border-color:#7f1d1d;padding:6px 10px}.status{border:1px solid var(--line);border-radius:99px;padding:7px 12px;white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#fbbf24;margin-right:7px}.dot.live{background:var(--green)}
.suggestions{position:absolute;z-index:5;top:100%;left:0;right:0;background:#111b2d;border:1px solid #35435e;border-radius:8px;max-height:280px;overflow:auto}
.suggestion{padding:10px 12px;cursor:pointer;border-bottom:1px solid #29354c}.suggestion:hover{background:#1d2b43}.searchbox{position:relative}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:760px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #202b40;white-space:nowrap}th{background:#18243a;color:var(--muted);font-size:12px;text-transform:uppercase}
.price{font-weight:750;font-size:16px}.positive{color:var(--green)}.negative{color:var(--red)}.empty{text-align:center;color:var(--muted);padding:30px}.error{color:#fca5a5;margin-top:10px;white-space:pre-wrap}
.metrics{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}.metric{background:var(--panel);border:1px solid var(--line);padding:12px 16px;border-radius:10px;min-width:150px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;font-size:18px;margin-top:3px}
@media(max-width:600px){input{min-width:0;width:100%}.controls>*{flex:1}}
</style></head><body>
<header><div><h1>Kite <span style="color:var(--blue)">/ Subscription Test</span></h1><small>TEMPORARY DIAGNOSTIC SCREEN · REMOVE AFTER VALIDATION</small></div>
<div class="status"><span id="dot" class="dot"></span><span id="connection">Checking WebSocket…</span></div></header>
<main>
<div class="panel"><h2 style="margin-top:0">Add a symbol</h2><div class="controls">
<div class="searchbox"><label for="search">Search instrument symbol or name</label><input id="search" autocomplete="off" placeholder="e.g. RELIANCE, NIFTY 50, INFY"><div id="suggestions" class="suggestions" hidden></div></div>
<label>Subscription mode<select id="mode"><option value="ltp">LTP (lightweight)</option><option value="quote" selected>Quote</option><option value="full">Full market data</option></select></label>
<button id="add" disabled>Add &amp; Subscribe</button></div><div id="error" class="error" hidden></div>
<p class="muted">Choose an exact instrument from the instrument master. Add subscribes its instrument token; Remove unsubscribes it. This screen does not place orders.</p></div>
<div class="metrics"><div class="metric"><span>Symbols on this screen</span><strong id="count">0</strong></div><div class="metric"><span>WebSocket state</span><strong id="state">—</strong></div><div class="metric"><span>Backend subscriptions</span><strong id="subcount">0</strong></div><div class="metric"><span>Last refresh</span><strong id="updated">—</strong></div></div>
<div class="tablewrap"><table><thead><tr><th>Exchange</th><th>Symbol</th><th>Instrument token</th><th>Last price</th><th>Change</th><th>Last tick received</th><th>Action</th></tr></thead><tbody id="rows"><tr><td colspan="7" class="empty">Search for a symbol above and click Add &amp; Subscribe.</td></tr></tbody></table></div>
<p class="muted">Prices refresh from the backend quote cache. If no WebSocket tick has arrived, the price remains a dash. Quote timestamps are shown in your browser's local time.</p>
</main>
<script>
const $=id=>document.getElementById(id);let selected=null,items=new Map(),searchTimer=null,refreshBusy=false;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function showError(msg){$("error").hidden=!msg;$("error").textContent=msg||""}
function fmt(v){return v===null||v===undefined||v===""?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:2})}
function localTime(v){if(!v)return "—";const d=new Date(v);return Number.isNaN(d.getTime())?"—":d.toLocaleTimeString()}
async function api(url,opts={}){const r=await fetch(url,{cache:"no-store",...opts,headers:{"Content-Type":"application/json",...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||("Request failed ("+r.status+")"));return d}
$("search").addEventListener("input",()=>{clearTimeout(searchTimer);selected=null;$("add").disabled=true;const q=$("search").value.trim();if(q.length<2){$("suggestions").hidden=true;return}searchTimer=setTimeout(async()=>{try{const d=await api("/api/v1/instruments?search="+encodeURIComponent(q)+"&limit=20");const box=$("suggestions");box.innerHTML="";for(const x of d.instruments||[]){const div=document.createElement("div");div.className="suggestion";div.textContent=x.exchange+":"+x.tradingsymbol+" · token "+x.instrument_token+(x.name?" · "+x.name:"");div.addEventListener("click",()=>{selected=x;$("search").value=x.exchange+":"+x.tradingsymbol;box.hidden=true;$("add").disabled=false});box.appendChild(div)}if(!box.children.length){box.innerHTML="<div class='suggestion'>No matching active instruments. Import the Kite instrument master first.</div>"}box.hidden=false}catch(e){showError(e.message)}},250)});
document.addEventListener("click",e=>{if(!e.target.closest(".searchbox"))$("suggestions").hidden=true});
$("add").addEventListener("click",async()=>{if(!selected)return;showError("");const x=selected;try{await api("/api/v1/kite/market-data/subscribe",{method:"POST",body:JSON.stringify({instrument_tokens:[Number(x.instrument_token)],mode:$("mode").value})});items.set(Number(x.instrument_token),{...x,mode:$("mode").value});selected=null;$("search").value="";$("add").disabled=true;render();await refresh()}catch(e){showError(e.message)}});
async function removeToken(token){showError("");try{await api("/api/v1/kite/market-data/unsubscribe",{method:"POST",body:JSON.stringify({instrument_tokens:[Number(token)]})});items.delete(Number(token));render();await refresh()}catch(e){showError(e.message)}}
function render(quotes={}){$("count").textContent=items.size;const body=$("rows");body.innerHTML="";if(!items.size){body.innerHTML="<tr><td colspan='7' class='empty'>No symbols subscribed. Add one above.</td></tr>";return}
for(const [token,x] of items){const q=quotes[token]||{};const price=q.last_price;const change=q.change;const cls=Number(change)>0?"positive":Number(change)<0?"negative":"";const tr=document.createElement("tr");tr.innerHTML="<td>"+esc(x.exchange)+"</td><td><b>"+esc(x.tradingsymbol)+"</b><br><small>"+esc(x.name||x.instrument_type||"")+"</small></td><td>"+token+"</td><td class='price "+cls+"'>"+fmt(price)+"</td><td class='"+cls+"'>"+(change===undefined?"—":fmt(change)+"%")+"</td><td>"+esc(localTime(q.received_at))+"</td><td><button class='remove' data-token='"+token+"'>Unsubscribe &amp; Remove</button></td>";tr.querySelector("button").addEventListener("click",()=>removeToken(token));body.appendChild(tr)}}
async function refresh(){if(refreshBusy)return;refreshBusy=true;try{const s=await api("/api/v1/kite/market-data/status");$("dot").classList.toggle("live",s.connected===true);$("connection").textContent=s.connected?"WebSocket connected":"WebSocket: "+s.state;$("state").textContent=s.state;$("subcount").textContent=s.subscription_count??(s.subscribed_tokens||[]).length;
const entries=[...items.entries()];const result=await Promise.all(entries.map(async([token])=>{try{return [token,await api("/api/v1/kite/quote/"+token)]}catch(_){return [token,{}]}}));const quotes=Object.fromEntries(result);render(quotes);$("updated").textContent=new Date().toLocaleTimeString()}catch(e){showError(e.message)}finally{refreshBusy=false}}
render();refresh();setInterval(refresh,1000);
</script></body></html>"""


@router.get("/kite/subscription-test", tags=["kite"], response_class=HTMLResponse)
def kite_subscription_test_ui() -> HTMLResponse:
    """Temporary screen for adding/removing WebSocket subscriptions and viewing prices."""
    return HTMLResponse(_SUBSCRIPTION_TEST_UI)
