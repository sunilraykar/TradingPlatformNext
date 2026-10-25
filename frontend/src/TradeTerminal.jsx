import React, { useCallback, useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const WS_API = API ? API.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:') : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
async function request(path, options = {}) {
  const response = await fetch(`${API}/api/v1${path}`, {
    ...options, headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  return data;
}
const fmt = (value) => value === null || value === undefined || value === '' ? '—' :
  typeof value === 'number' ? value.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : String(value);

export default function TradeTerminal() {
  const [trades, setTrades] = useState([]);
  const [terminal, setTerminal] = useState({});
  const [mode, setMode] = useState('ALL');
  const [status, setStatus] = useState('ALL');
  const [sourceFilter, setSourceFilter] = useState('ALL');
  const [underlyingFilter, setUnderlyingFilter] = useState('ALL');
  const [symbolLetter, setSymbolLetter] = useState('ALL');
  const [tradeDate, setTradeDate] = useState('');
  const [dateFilter, setDateFilter] = useState('ALL');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [closeTrade, setCloseTrade] = useState(null);
  const [closeOrderType, setCloseOrderType] = useState('MARKET');
  const [closeLimitPrice, setCloseLimitPrice] = useState('');
  const [symbolQuery, setSymbolQuery] = useState('');
  const [symbolExchange, setSymbolExchange] = useState('ALL');
  const [symbolType, setSymbolType] = useState('ALL');
  const [instrumentOptions, setInstrumentOptions] = useState([]);
  const [loadingInstruments, setLoadingInstruments] = useState(false);
  const [instrumentSearchError, setInstrumentSearchError] = useState('');
  const [saving, setSaving] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [form, setForm] = useState({ instrument_symbol: '', exchange: 'NFO', underlying: 'NIFTY', direction: 'BUY', quantity: '1', entry_price: '', stop_loss: '', target: '', strategy: 'Manual Paper Trade', notes: '' });

  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params = new URLSearchParams();
      if (mode !== 'ALL') params.set('mode', mode);
      if (status !== 'ALL') params.set('status', status);
      const result = await request('/trades' + (params.size ? `?${params}` : ''));
      setTrades(result.trades || []); setTerminal(result.terminal || {});
    } catch (e) { setError(e.message || 'Unable to load canonical trade records.'); }
    finally { setLoading(false); }
  }, [mode, status]);

  useEffect(() => {
    refresh();
    // WebSocket ticks are the primary update path. This silent cache sync is a
    // fallback for ticks cached before a trade subscription or a missed push event.
    let syncInFlight = false;
    const syncCachedQuotes = async () => {
      if (syncInFlight || document.visibilityState === 'hidden') return;
      syncInFlight = true;
      try {
        const result = await request('/trades');
        setTrades(result.trades || []);
        setTerminal(result.terminal || {});
      } catch {
        // Keep the current table visible; the next cycle or WebSocket can recover.
      } finally {
        syncInFlight = false;
      }
    };
    const quoteSyncTimer = window.setInterval(syncCachedQuotes, 2000);
    let socket;
    let reconnectTimer;
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(`${WS_API}/api/v1/trades/stream`);
      socket.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type === 'snapshot') {
          setTrades(message.trades || []);
          setTerminal(message.terminal || {});
        } else if (message.type === 'trade_upsert' && message.trade) {
          if (message.terminal) setTerminal(message.terminal);
          setTrades((current) => {
            const index = current.findIndex((item) => item.trade_id === message.trade.trade_id);
            if (index < 0) return [message.trade, ...current];
            return current.map((item) => item.trade_id === message.trade.trade_id ? { ...item, ...message.trade } : item);
          });
        } else if (message.type === 'trade_quote' && message.trade) {
          setTrades((current) => current.map((item) => item.trade_id === message.trade_id ? { ...item, ...message.trade } : item));
        } else if (message.type === 'trade_status') {
          setTerminal(message.terminal || {});
        }
      };
      socket.onclose = () => {
        if (!disposed) reconnectTimer = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      window.clearInterval(quoteSyncTimer);
      if (socket) socket.close();
    };
  }, [refresh]);

  useEffect(() => {
    if (!showCreate || symbolQuery.trim().length < 2) {
      setInstrumentOptions([]);
      setInstrumentSearchError('');
      setLoadingInstruments(false);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setLoadingInstruments(true);
      setInstrumentSearchError('');
      try {
        const params = new URLSearchParams({ q: symbolQuery.trim(), limit: '100' });
        if (symbolExchange !== 'ALL') params.set('exchange', symbolExchange);
        if (symbolType !== 'ALL') params.set('instrument_type', symbolType);
        const result = await request('/trades/instruments?' + params.toString());
        if (!cancelled) setInstrumentOptions(result.instruments || []);
      } catch (e) {
        if (!cancelled) {
          setInstrumentOptions([]);
          setInstrumentSearchError(e.message || 'Unable to search instruments.');
        }
      } finally {
        if (!cancelled) setLoadingInstruments(false);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [showCreate, symbolQuery, symbolExchange, symbolType]);

  const selectInstrument = (instrument) => {
    const symbol = instrument.tradingsymbol;
    const underlying = ['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','SENSEX','BANKEX'].find((index) => symbol.toUpperCase().startsWith(index)) ||
      (instrument.instrument_type === 'EQ' ? 'OTHER' : form.underlying);
    setForm((current) => ({ ...current, instrument_symbol: symbol, exchange: instrument.exchange, underlying }));
    setSymbolQuery(symbol);
    setInstrumentOptions([]);
  };

  const sources = [...new Set(trades.map((trade) => trade.source).filter(Boolean))].sort();
  const underlyings = [...new Set(trades.map((trade) => trade.underlying).filter(Boolean))].sort();
  const symbolLetters = [...new Set(trades.map((trade) => String(trade.instrument_symbol || '').trim().charAt(0).toUpperCase()).filter((letter) => /^[A-Z]$/.test(letter)))].sort();
  const visibleTrades = trades.filter((trade) => {
    const created = new Date(trade.created_at || trade.updated_at || 0);
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfWeek = new Date(startOfToday);
    startOfWeek.setDate(startOfToday.getDate() - ((startOfToday.getDay() + 6) % 7));
    const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    const normalizedSource = String(trade.source || '').toUpperCase();
    const sourceMatches = sourceFilter === 'ALL' || normalizedSource === sourceFilter ||
      (sourceFilter === 'AUTOTRENDER' && normalizedSource.includes('AUTO'));
    const statusValue = String(trade.status || '').toUpperCase();
    const statusMatches = status === 'ALL' || (status === 'OPEN' && !['CLOSED','CANCELLED','REJECTED','EXPIRED'].includes(statusValue)) || statusValue === status;
    const underlyingMatches = underlyingFilter === 'ALL' ||
      (underlyingFilter === 'INDICES' && ['NIFTY','BANKNIFTY','FINNIFTY','SENSEX'].includes(String(trade.underlying || '').toUpperCase())) ||
      String(trade.underlying || '') === underlyingFilter;
    const dateMatches = dateFilter === 'ALL' ||
      (dateFilter === 'TODAY' && created >= startOfToday) ||
      (dateFilter === 'WEEK' && created >= startOfWeek) ||
      (dateFilter === 'MONTH' && created >= startOfMonth) ||
      (dateFilter === 'CUSTOM' && !!tradeDate && String(trade.created_at || '').slice(0, 10) === tradeDate);
    return (mode === 'ALL' || trade.execution_mode === mode) && statusMatches && sourceMatches &&
      underlyingMatches && (symbolLetter === 'ALL' || String(trade.instrument_symbol || '').trim().toUpperCase().startsWith(symbolLetter)) && dateMatches;
  });

  const create = async (event) => {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const payload = { ...form, idempotency_key: requestKey, execution_mode: 'PAPER', source: 'MANUAL', quantity: Number(form.quantity) };
      for (const key of ['entry_price', 'stop_loss', 'target']) payload[key] = payload[key] === '' ? null : Number(payload[key]);
      const result = await request('/trades', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      setShowCreate(false);
      setRequestKey(crypto.randomUUID());
      setForm({ instrument_symbol: '', exchange: 'NFO', underlying: 'NIFTY', direction: 'BUY', quantity: '1', entry_price: '', stop_loss: '', target: '', strategy: 'Manual Paper Trade', notes: '' });
      await refresh();
      if (result.trade?.persistence_state === 'DEGRADED') setError('Trade is not durably persisted; check database connectivity.');
    } catch (e) { setError(e.message || 'Trade could not be saved.'); }
    finally { setSaving(false); }
  };

  const reconcileLive = async () => {
    setLoading(true); setError('');
    try {
      const result = await request('/trades/reconcile-live', { method: 'POST' });
      await refresh();
      const r = result.reconciliation || {};
      window.alert(`Kite reconciliation complete: ${r.created || 0} created, ${r.updated || 0} updated, ${r.closed || 0} closed.`);
    } catch (e) { setError(e.message || 'Live-position reconciliation failed.'); }
    finally { setLoading(false); }
  };

  const openCloseDialog = (trade) => {
    setCloseTrade(trade);
    setCloseOrderType('MARKET');
    setCloseLimitPrice('');
    setError('');
  };

  const updateStatus = async () => {
    if (!closeTrade) return;
    const trade = closeTrade;
    const exitPrice = closeOrderType === 'MARKET'
      ? Number(trade.last_price)
      : Number(closeLimitPrice);
    if (!Number.isFinite(exitPrice) || exitPrice <= 0) {
      setError(closeOrderType === 'MARKET'
        ? 'No valid last price is available for this trade. Refresh quotes before closing.'
        : 'Enter a valid positive limit price.');
      return;
    }
    const rawEntryPrice = trade.average_entry_price ?? trade.entry_price;
    const entryPrice = rawEntryPrice === null || rawEntryPrice === undefined || rawEntryPrice === '' ? NaN : Number(rawEntryPrice);
    const qty = Number(trade.open_quantity ?? trade.quantity ?? 0);
    const directionFactor = String(trade.direction).toUpperCase() === 'SELL' ? -1 : 1;
    const changes = { status: 'CLOSED', exit_price: exitPrice, average_exit_price: exitPrice, open_quantity: 0 };
    if (Number.isFinite(entryPrice)) changes.realized_pnl = (exitPrice - entryPrice) * qty * directionFactor;
    try {
      await request(`/trades/${encodeURIComponent(trade.trade_id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
      setCloseTrade(null);
      await refresh();
    } catch (e) { setError(e.message || 'Trade update failed.'); }
  };

  return <section className="trade-terminal-page">
    <div className="page-heading trade-terminal-heading"><div className="trade-terminal-actions"><button className="button secondary" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : '↻ Refresh'}</button><button className="button secondary" onClick={reconcileLive} disabled={loading}>{loading ? 'Syncing…' : 'Sync Live Positions'}</button><button className="button primary" onClick={() => { setShowCreate(true); setSymbolQuery(''); setSymbolExchange('ALL'); setSymbolType('ALL'); }}>+ New Paper Trade</button></div></div>
    {error && <div className="error-banner"><strong>Trade terminal issue</strong><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
    <div className="trade-terminal-metrics">
      <div className="metric"><span>Total Trades</span><strong>{fmt(trades.length)}</strong></div>
      <div className="metric"><span>Open trades</span><strong>{fmt(terminal.open_trades ?? 0)}</strong></div>
      <div className="metric"><span>Pending persistence</span><strong className={terminal.persistence_degraded ? 'negative' : 'positive'}>{fmt(terminal.dirty_trades ?? 0)}</strong></div>
    </div>
    {terminal.persistence_degraded && <div className="trade-persistence-warning">Database persistence is degraded. Do not assume cache-only changes are durable. {terminal.persistence_error || ''}</div>}
    {closeTrade && <div className="trade-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCloseTrade(null); }}>
      <div className="panel trade-create-form trade-create-modal" role="dialog" aria-modal="true" aria-labelledby="close-trade-title">
        <div className="trade-modal-heading"><div><h2 id="close-trade-title">Close paper trade</h2><p>{closeTrade.instrument_symbol} · {fmt(closeTrade.open_quantity ?? closeTrade.quantity)} units</p></div><button className="button secondary" type="button" onClick={() => setCloseTrade(null)}>✕</button></div>
        <div className="trade-close-order-choices">
          <span className="trade-filter-label">Exit order type</span>
          <div className="trade-filter-buttons">
            <button className={closeOrderType === 'MARKET' ? 'trade-filter-button active' : 'trade-filter-button'} type="button" onClick={() => setCloseOrderType('MARKET')}>Market Price</button>
            <button className={closeOrderType === 'LIMIT' ? 'trade-filter-button active' : 'trade-filter-button'} type="button" onClick={() => setCloseOrderType('LIMIT')}>Limit Price</button>
          </div>
        </div>
        {closeOrderType === 'MARKET'
          ? <div className="trade-selected-instrument">Current last price: <strong>{fmt(closeTrade.last_price)}</strong></div>
          : <label className="settings-control"><span>Limit price *</span><input autoFocus type="number" min="0.01" step="any" required value={closeLimitPrice} onChange={(e) => setCloseLimitPrice(e.target.value)} placeholder="Enter limit price" /></label>}
        <div className="settings-actions"><span className="trade-paper-label">PAPER MODE · no broker order will be placed</span><button className="button secondary" type="button" onClick={() => setCloseTrade(null)}>Cancel</button><button className="button primary" type="button" onClick={updateStatus}>Confirm close</button></div>
      </div>
    </div>}
    {showCreate && <div className="trade-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setShowCreate(false); }}>
      <form className="panel trade-create-form trade-create-modal" onSubmit={create}>
      <div className="trade-modal-heading"><div><h2>New paper trade</h2><p>Search the Kite instrument master and select a symbol.</p></div><button className="button secondary" type="button" onClick={() => setShowCreate(false)} disabled={saving}>✕</button></div>
      <div className="trade-instrument-picker">
        <label className="settings-control"><span>Search symbols</span><input autoFocus value={symbolQuery} onChange={(e) => setSymbolQuery(e.target.value)} placeholder="Type a symbol or company name (e.g. RELIANCE, NIFTY)" /></label>
        <div className="trade-instrument-filters">
          <label className="settings-control"><span>Exchange</span><select value={symbolExchange} onChange={(e) => setSymbolExchange(e.target.value)}><option value="ALL">All exchanges</option><option value="NSE">NSE</option><option value="NFO">NFO</option><option value="BSE">BSE</option><option value="BFO">BFO</option><option value="CDS">CDS</option><option value="MCX">MCX</option></select></label>
          <label className="settings-control"><span>Instrument type</span><select value={symbolType} onChange={(e) => setSymbolType(e.target.value)}><option value="ALL">All types</option><option value="EQ">Equity (EQ)</option><option value="CE">Call option (CE)</option><option value="PE">Put option (PE)</option><option value="FUT">Futures (FUT)</option></select></label>
        </div>
        <div className="trade-instrument-results" aria-live="polite">
          {loadingInstruments && <div className="trade-instrument-empty">Searching Kite instruments…</div>}
          {!loadingInstruments && instrumentSearchError && <div className="trade-instrument-empty negative">{instrumentSearchError}</div>}
          {!loadingInstruments && !instrumentSearchError && symbolQuery.trim().length < 2 && <div className="trade-instrument-empty">Enter at least two characters to search.</div>}
          {!loadingInstruments && !instrumentSearchError && symbolQuery.trim().length >= 2 && !instrumentOptions.length && <div className="trade-instrument-empty">No matching instruments. Try another symbol or filter.</div>}
          {instrumentOptions.map((instrument) => <button key={instrument.instrument_token} type="button" className={'trade-instrument-option' + (form.instrument_symbol === instrument.tradingsymbol && form.exchange === instrument.exchange ? ' selected' : '')} onClick={() => selectInstrument(instrument)}>
            <span className="trade-instrument-main"><strong>{instrument.tradingsymbol}</strong><small>{instrument.name || instrument.segment || instrument.instrument_type || 'Kite instrument'}</small></span>
            <span className="trade-instrument-meta"><strong>{instrument.exchange}</strong><small>{instrument.instrument_type || instrument.segment || '—'}{instrument.expiry ? ' · ' + instrument.expiry : ''}</small></span>
          </button>)}
        </div>
        <div className="trade-selected-instrument">{form.instrument_symbol ? <>Selected: <strong>{form.exchange}:{form.instrument_symbol}</strong></> : 'Select a symbol from the search results to continue.'}</div>
      </div>
      <div className="settings-grid">
        <label className="settings-control"><span>Instrument symbol *</span><input required readOnly value={form.instrument_symbol} placeholder="Select a symbol above" /></label>
        <label className="settings-control"><span>Exchange</span><select value={form.exchange} onChange={(e) => setForm({ ...form, exchange: e.target.value })}>{[['NSE','NSE · Stocks'],['NFO','NFO · Options'],['BSE','BSE · Stocks'],['BFO','BFO · Options']].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="settings-control"><span>Underlying / index</span><select value={form.underlying} onChange={(e) => setForm({ ...form, underlying: e.target.value })}>{['NIFTY','BANKNIFTY','FINNIFTY','SENSEX','OTHER'].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label className="settings-control"><span>Direction</span><select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}><option>BUY</option><option>SELL</option></select></label>
        <label className="settings-control"><span>Quantity *</span><input type="number" min="1" required value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
        <label className="settings-control"><span>Entry price</span><input type="number" min="0" step="any" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })} /></label>
        <label className="settings-control"><span>Stop loss</span><input type="number" min="0" step="any" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} /></label>
        <label className="settings-control"><span>Target</span><input type="number" min="0" step="any" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} /></label>
        <label className="settings-control"><span>Strategy / source label</span><input value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })} /></label>
        <label className="settings-control trade-notes-field"><span>Notes</span><input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength="2000" /></label>
      </div><div className="settings-actions"><span className="trade-paper-label">PAPER MODE · no broker order will be placed</span><button className="button primary" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Create paper trade'}</button></div>
    </form></div>}
    <div className="trade-terminal-filters">
      <div className="trade-filter-group"><span className="trade-filter-label">Mode</span><div className="trade-filter-buttons">{[['ALL','All'],['PAPER','Paper'],['LIVE','Live']].map(([value,label]) => <button key={value} type="button" className={mode === value ? 'trade-filter-button active' : 'trade-filter-button'} onClick={() => setMode(value)}>{label}</button>)}</div></div>
      <div className="trade-filter-group"><span className="trade-filter-label">Status</span><div className="trade-filter-buttons">{[['ALL','All'],['OPEN','Open'],['CLOSED','Closed'],['CANCELLED','Cancelled']].map(([value,label]) => <button key={value} type="button" className={status === value ? 'trade-filter-button active' : 'trade-filter-button'} onClick={() => setStatus(value)}>{label}</button>)}</div></div>
      <div className="trade-filter-group"><span className="trade-filter-label">Source</span><div className="trade-filter-buttons">{[['ALL','All'],...['AUTOTRENDER','MANUAL','KITE'].map(value => [value, value === 'AUTOTRENDER' ? 'AutoTrender' : value.charAt(0)+value.slice(1).toLowerCase()])].map(([value,label]) => <button key={value} type="button" className={sourceFilter === value ? 'trade-filter-button active' : 'trade-filter-button'} onClick={() => setSourceFilter(value)}>{label}</button>)}</div></div>
      <div className="trade-filter-group"><span className="trade-filter-label">Underlying</span><div className="trade-filter-buttons">{[['ALL','All'],['INDICES','Indices'],...underlyings.filter(x => !['NIFTY','BANKNIFTY','FINNIFTY','SENSEX'].includes(x)).map(x => [x,x])].map(([value,label]) => <button key={value} type="button" className={underlyingFilter === value ? 'trade-filter-button active' : 'trade-filter-button'} onClick={() => setUnderlyingFilter(value)}>{label}</button>)}</div></div>
      <div className="trade-filter-group trade-symbol-letter-filter"><span className="trade-filter-label">Symbol</span><div className="trade-filter-buttons">{[['ALL','All'],...symbolLetters.map((letter) => [letter,letter])].map(([value,label]) => <button key={value} type="button" className={symbolLetter === value ? 'trade-filter-button active' : 'trade-filter-button'} onClick={() => setSymbolLetter(value)}>{label}</button>)}</div></div>
      <div className="trade-filter-group"><span className="trade-filter-label">Trade date</span><div className="trade-filter-buttons">{[['ALL','All'],['TODAY','Today'],['WEEK','This Week'],['MONTH','This Month'],['CUSTOM','Custom']].map(([value,label]) => <button key={value} type="button" className={(dateFilter === value ? 'trade-filter-button active' : 'trade-filter-button')} onClick={() => setDateFilter(value)}>{label}</button>)}</div>{dateFilter === 'CUSTOM' && <input className="trade-date-custom" type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />}</div>
      <span className="trade-terminal-count">{visibleTrades.length} of {trades.length} trades · live updates</span>
    </div>
    <section className="panel trade-terminal-table-panel"><div className="table-scroll trade-terminal-table-scroll"><table className="kite-data-table trade-terminal-table"><thead><tr><th>Mode</th><th>Underlying</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Entry</th><th>Last price</th><th>SL</th><th>Target</th><th>Unrealized P&amp;L</th><th>Status</th><th>Source / Strategy</th><th>Updated</th><th>Action</th></tr></thead><tbody>
      {visibleTrades.map((trade) => <tr key={trade.trade_id}><td><span className={'trade-mode-pill ' + String(trade.execution_mode).toLowerCase()}>{trade.execution_mode}</span></td><td>{fmt(trade.underlying)}</td><td>{fmt(trade.instrument_symbol)}</td><td>{fmt(trade.direction)}</td><td>{fmt(trade.open_quantity ?? trade.quantity)}</td><td>{fmt(trade.average_entry_price ?? trade.entry_price)}</td><td>{fmt(trade.last_price)}{trade.quote_state && trade.quote_state !== 'LIVE' && <div className="trade-quote-state">{trade.quote_state === 'WAITING_FOR_FIRST_QUOTE' ? 'Waiting for live quote' : trade.quote_state === 'INSTRUMENT_NOT_RESOLVED' ? 'Instrument not resolved' : 'Subscription pending'}</div>}</td><td>{fmt(trade.stop_loss)}</td><td>{fmt(trade.target)}</td><td className={Number(trade.unrealized_pnl || 0) >= 0 ? 'holding-pnl-positive' : 'holding-pnl-negative'}>{fmt(Number(trade.unrealized_pnl || 0))}</td><td>{fmt(trade.status)}</td><td>{fmt(trade.source)}{trade.strategy ? ` · ${trade.strategy}` : ''}</td><td>{trade.updated_at ? new Date(trade.updated_at).toLocaleString() : '—'}{trade.persistence_state === 'DEGRADED' && <div className="negative">Persistence issue</div>}</td><td>{trade.execution_mode === 'PAPER' && !['CLOSED','CANCELLED','REJECTED','EXPIRED'].includes(String(trade.status).toUpperCase()) && <button className="button secondary trade-close-button" onClick={() => openCloseDialog(trade)}>Close</button>}</td></tr>)}
      {!visibleTrades.length && <tr><td colSpan="14" className="empty-cell">{loading ? 'Loading canonical trades…' : 'No trades match these filters. Create a paper trade or sync live positions.'}</td></tr>}
    </tbody></table></div><div className="panel-footer">Durable lifecycle events are saved immediately. Quote and unrealized P&amp;L updates are cache-only. Live broker reconciliation is read-only and remains a separate integration step.</div></section>
  </section>;
}
