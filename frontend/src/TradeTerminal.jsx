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
const calculateTradePnl = (trade) => {
  const terminal = isTerminalStatus(trade.status);
  const entryRaw = trade.average_entry_price ?? trade.entry_price;
  const markRaw = terminal
    ? (trade.average_exit_price ?? trade.exit_price)
    : trade.last_price;
  const quantityRaw = trade.quantity;
  const entry = Number(entryRaw);
  const mark = Number(markRaw);
  const quantity = Number(quantityRaw);
  if (
    entryRaw === null || entryRaw === undefined || entryRaw === '' ||
    markRaw === null || markRaw === undefined || markRaw === '' ||
    !Number.isFinite(entry) || !Number.isFinite(mark) ||
    !Number.isFinite(quantity) || quantity < 0
  ) return null;
  const side = String(trade.direction || 'BUY').toUpperCase();
  if (side !== 'BUY' && side !== 'SELL') return null;
  return (mark - entry) * quantity * (side === 'SELL' ? -1 : 1);
};
const isTerminalStatus = (value) => ['CLOSED', 'CANCELLED', 'REJECTED', 'EXPIRED'].includes(String(value || '').toUpperCase());
const quoteFreshness = (trade) => {
  const raw = trade.last_market_data_at || trade.quote_received_at || trade.quote_timestamp || trade.last_quote_at;
  if (!raw) return { label: trade.quote_state === 'LIVE' ? 'Timestamp unavailable' : (trade.quote_state || 'No quote'), stale: true };
  const timestamp = new Date(raw).getTime();
  if (!Number.isFinite(timestamp)) return { label: 'Quote time unavailable', stale: true };
  const age = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  return { label: age > 30 ? `STALE · ${age}s` : `LIVE · ${age}s`, stale: age > 30 };
};
const csvCell = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;

export default function TradeTerminal() {
  const [trades, setTrades] = useState([]);
  const [terminal, setTerminal] = useState({});
  const [mode, setMode] = useState('ALL');
  const [status, setStatus] = useState('OPEN');
  const [sourceFilter, setSourceFilter] = useState('ALL');
  const [underlyingFilter, setUnderlyingFilter] = useState('INDICES');
  const [symbolLetter, setSymbolLetter] = useState('ALL');
  const [tradeDate, setTradeDate] = useState('');
  const [dateFilter, setDateFilter] = useState('TODAY');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [closeTrade, setCloseTrade] = useState(null);
  const [detailsTrade, setDetailsTrade] = useState(null);
  const [sortConfig, setSortConfig] = useState({ key: 'updated_at', direction: 'desc' });
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
  const symbolLetters = [...new Set(trades.flatMap((trade) => [trade.instrument_symbol, trade.underlying].map((value) => String(value || '').trim().charAt(0).toUpperCase())).filter((letter) => /^[A-Z]$/.test(letter)))].sort();
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
      (underlyingFilter === 'OTHER'
        ? !['NIFTY','BANKNIFTY','FINNIFTY','SENSEX'].includes(String(trade.underlying || '').toUpperCase())
        : String(trade.underlying || '') === underlyingFilter);
    const dateMatches = dateFilter === 'ALL' ||
      (dateFilter === 'TODAY' && created >= startOfToday) ||
      (dateFilter === 'WEEK' && created >= startOfWeek) ||
      (dateFilter === 'MONTH' && created >= startOfMonth) ||
      (dateFilter === 'CUSTOM' && !!tradeDate && String(trade.created_at || '').slice(0, 10) === tradeDate);
    return (mode === 'ALL' || trade.execution_mode === mode) && statusMatches && sourceMatches &&
      underlyingMatches && (symbolLetter === 'ALL' || String(trade.instrument_symbol || '').trim().toUpperCase().startsWith(symbolLetter) || String(trade.underlying || '').trim().toUpperCase().startsWith(symbolLetter)) && dateMatches;
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
    // The close dialog initially receives a snapshot of the row. Prefer the
    // latest trade state so WebSocket/cache-sync quote updates are not lost
    // while the dialog is open.
    const trade = trades.find((item) => item.trade_id === closeTrade.trade_id) || closeTrade;
    const cachedLtp = trade.last_price === null || trade.last_price === undefined || trade.last_price === ''
      ? NaN
      : Number(trade.last_price);
    // If no quote is available, let the user manually enter the simulated
    // exit price instead of trapping the paper trade behind the missing LTP.
    const useManualFallback = closeOrderType === 'MARKET' && (!Number.isFinite(cachedLtp) || cachedLtp <= 0);
    const exitPrice = closeOrderType === 'MARKET' && !useManualFallback
      ? cachedLtp
      : Number(closeLimitPrice);
    if (!Number.isFinite(exitPrice) || exitPrice <= 0) {
      setError(closeOrderType === 'MARKET'
        ? 'Enter a manual exit price below because no quote is currently available.'
        : 'Enter a valid positive limit price.');
      return;
    }
    const rawEntryPrice = trade.average_entry_price ?? trade.entry_price;
    const entryPrice = rawEntryPrice === null || rawEntryPrice === undefined || rawEntryPrice === '' ? NaN : Number(rawEntryPrice);
    const qty = Number(trade.quantity ?? 0);
    const directionFactor = String(trade.direction).toUpperCase() === 'SELL' ? -1 : 1;
    const changes = {
      status: 'CLOSED', exit_price: exitPrice, average_exit_price: exitPrice,
      quantity: qty,
      exit_reason: 'Manual paper close',
      exit_method: closeOrderType === 'MARKET' && Number.isFinite(cachedLtp) && cachedLtp > 0
        ? 'PAPER_MARKET_SIMULATION'
        : closeOrderType === 'LIMIT' ? 'PAPER_LIMIT_SIMULATION' : 'PAPER_MANUAL_PRICE_SIMULATION',
    };
    try {
      await request(`/trades/${encodeURIComponent(trade.trade_id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
      setCloseTrade(null);
      await refresh();
    } catch (e) { setError(e.message || 'Trade update failed.'); }
  };

  const displayedTrades = visibleTrades.map((trade) => ({ ...trade, display_pnl: calculateTradePnl(trade) })).sort((a, b) => {
    const left = a[sortConfig.key];
    const right = b[sortConfig.key];
    let comparison;
    if (['quantity', 'entry_price', 'average_entry_price', 'last_price', 'stop_loss', 'target', 'average_exit_price', 'display_pnl'].includes(sortConfig.key)) {
      const leftNumber = Number(left ?? 0);
      const rightNumber = Number(right ?? 0);
      comparison = leftNumber - rightNumber;
    } else {
      comparison = String(left ?? '').localeCompare(String(right ?? ''), undefined, { numeric: true, sensitivity: 'base' });
    }
    return sortConfig.direction === 'asc' ? comparison : -comparison;
  });
  const toggleSort = (key) => setSortConfig((current) => ({ key, direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc' }));
  const exportVisibleTrades = () => {
    const columns = [
      ['Trade ID', 'trade_id'], ['Mode', 'execution_mode'], ['Underlying', 'underlying'], ['Instrument', 'instrument_symbol'],
      ['Exchange', 'exchange'], ['Side', 'direction'], ['Qty', 'quantity'],
      ['Last Price', 'last_price'], ['Entry Price', 'average_entry_price'], ['Stop Loss', 'stop_loss'], ['Target', 'target'],
      ['Calculated P&L', 'display_pnl'], ['Exit Price', 'average_exit_price'],
      ['Exit Reason', 'exit_reason'], ['Exit Method', 'exit_method'], ['Status', 'status'], ['Source', 'source'],
      ['Strategy', 'strategy'], ['Trailing Strategy', 'trailing_strategy'], ['Notes', 'notes'], ['Created At', 'created_at'], ['Updated At', 'updated_at'],
    ];
    const csv = [columns.map(([label]) => csvCell(label)).join(','), ...displayedTrades.map((trade) => columns.map(([, key]) => {
      const value = key === 'display_pnl' ? trade.display_pnl : trade[key];
      return csvCell(value ?? '');
    }).join(','))].join('\r\n');
    const blob = new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `trade-terminal-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const pnlSummary = visibleTrades.reduce((summary, trade) => {
    const total = calculateTradePnl(trade);
    if (total === null) return summary;
    if (total > 0) summary.profit += total;
    if (total < 0) summary.loss += Math.abs(total);
    summary.net += total;
    return summary;
  }, { profit: 0, loss: 0, net: 0 });

  return <section className="trade-terminal-page">
    <div className="page-heading trade-terminal-heading"><div className="trade-terminal-actions"><button className="button secondary" onClick={exportVisibleTrades} disabled={!displayedTrades.length}>Export CSV ({displayedTrades.length})</button><button className="button secondary" onClick={reconcileLive} disabled={loading}>{loading ? 'Syncing…' : 'Sync Live Positions'}</button><button className="button primary" onClick={() => { setShowCreate(true); setSymbolQuery(''); setSymbolExchange('ALL'); setSymbolType('ALL'); }}>+ New Paper Trade</button></div></div>
    {error && <div className="error-banner"><strong>Trade terminal issue</strong><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
    <div className="trade-terminal-metrics" style={{ gap: 8 }}>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Filtered trades</span><strong style={{ fontSize: "1rem" }}>{fmt(visibleTrades.length)}</strong></div>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Open in filter</span><strong style={{ fontSize: "1rem" }}>{fmt(visibleTrades.filter((trade) => !isTerminalStatus(trade.status)).length)}</strong></div>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Pending persistence</span><strong style={{ fontSize: "1rem" }} className={terminal.persistence_degraded ? 'negative' : 'positive'}>{fmt(terminal.dirty_trades ?? 0)}</strong></div>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Total Profit</span><strong style={{ fontSize: "1rem" }} className="holding-pnl-positive">{fmt(pnlSummary.profit)}</strong></div>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Total Loss</span><strong style={{ fontSize: "1rem" }} className="holding-pnl-negative">{fmt(pnlSummary.loss)}</strong></div>
      <div className="metric" style={{ padding: "8px 10px", minHeight: 0 }}><span style={{ fontSize: "0.75rem" }}>Net P&amp;L</span><strong style={{ fontSize: "1rem" }} className={pnlSummary.net >= 0 ? 'holding-pnl-positive' : 'holding-pnl-negative'}>{fmt(pnlSummary.net)}</strong></div>
    </div>
    {terminal.persistence_degraded && <div className="trade-persistence-warning">Database persistence is degraded. Do not assume cache-only changes are durable. {terminal.persistence_error || ''}</div>}
    {detailsTrade && <div className="trade-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailsTrade(null); }}>
      <div className="panel trade-create-form trade-create-modal trade-details-modal" role="dialog" aria-modal="true" aria-labelledby="trade-details-title">
        <div className="trade-modal-heading"><div><h2 id="trade-details-title">Trade details</h2><p>{detailsTrade.exchange || '—'}:{detailsTrade.instrument_symbol} · {detailsTrade.execution_mode} · {detailsTrade.status}</p></div><button className="button secondary" type="button" onClick={() => setDetailsTrade(null)}>✕</button></div>
        <div className="trade-details-grid">
          {[
            ['Direction', detailsTrade.direction], ['Qty', detailsTrade.quantity],
            ['Entry price', detailsTrade.average_entry_price ?? detailsTrade.entry_price], ['Last price', detailsTrade.last_price],
            ['Stop loss', detailsTrade.stop_loss], ['Target', detailsTrade.target], ['Calculated P&L', calculateTradePnl(detailsTrade)], ['Exit price', detailsTrade.average_exit_price ?? detailsTrade.exit_price],
            ['Exit reason', detailsTrade.exit_reason], ['Exit method', detailsTrade.exit_method], ['Source', detailsTrade.source],
            ['Strategy', detailsTrade.strategy], ['Trailing Strategy', detailsTrade.trailing_strategy], ['Quote status', quoteFreshness(detailsTrade).label],
            ['Created', detailsTrade.created_at ? new Date(detailsTrade.created_at).toLocaleString() : '—'],
            ['Updated', detailsTrade.updated_at ? new Date(detailsTrade.updated_at).toLocaleString() : '—'],
          ].map(([label, value]) => <div className="trade-detail-item" key={label}><span>{label}</span><strong>{fmt(value)}</strong></div>)}
        </div>
        {detailsTrade.notes && <div className="trade-details-notes"><span>Notes</span><p>{detailsTrade.notes}</p></div>}
        {detailsTrade.execution_mode === 'PAPER' && <p className="trade-paper-notice">Paper trade only. Closing a trade records a simulated exit; no broker order is placed.</p>}
      </div>
    </div>}
    {closeTrade && <div className="trade-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCloseTrade(null); }}>
      <div className="panel trade-create-form trade-create-modal" role="dialog" aria-modal="true" aria-labelledby="close-trade-title">
        <div className="trade-modal-heading"><div><h2 id="close-trade-title">Close paper trade</h2><p>{closeTrade.instrument_symbol} · {fmt(closeTrade.quantity)} units</p></div><button className="button secondary" type="button" onClick={() => setCloseTrade(null)}>✕</button></div>
        <div className="trade-close-order-choices">
          <span className="trade-filter-label">Exit order type</span>
          <div className="trade-filter-buttons">
            <button className={closeOrderType === 'MARKET' ? 'trade-filter-button active' : 'trade-filter-button'} type="button" onClick={() => setCloseOrderType('MARKET')}>Market Price</button>
            <button className={closeOrderType === 'LIMIT' ? 'trade-filter-button active' : 'trade-filter-button'} type="button" onClick={() => setCloseOrderType('LIMIT')}>Limit Price</button>
          </div>
        </div>
        {closeOrderType === 'MARKET' && Number.isFinite(Number(trades.find((item) => item.trade_id === closeTrade.trade_id)?.last_price ?? closeTrade.last_price)) && Number(trades.find((item) => item.trade_id === closeTrade.trade_id)?.last_price ?? closeTrade.last_price) > 0
          ? <div className="trade-selected-instrument">Simulated exit price from latest cached quote: <strong>{fmt(trades.find((item) => item.trade_id === closeTrade.trade_id)?.last_price ?? closeTrade.last_price)}</strong><p className="trade-paper-notice">This records a simulated exit; no broker order is placed.</p></div>
          : <label className="settings-control"><span>{closeOrderType === 'MARKET' ? 'Manual exit price (quote unavailable) *' : 'Limit price *'}</span><input autoFocus type="number" min="0.01" step="any" required value={closeLimitPrice} onChange={(e) => setCloseLimitPrice(e.target.value)} placeholder="Enter exit price" />{closeOrderType === 'MARKET' && <p className="trade-paper-notice">No cached LTP is available. Enter the price at which you want to record this paper-trade exit.</p>}</label>}
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
      <div className="trade-filter-group"><span className="trade-filter-label">Sort by</span><select className="trade-sort-select" value={`${sortConfig.key}:${sortConfig.direction}`} onChange={(event) => { const [key, direction] = event.target.value.split(':'); setSortConfig({ key, direction }); }}><option value="updated_at:desc">Recently updated</option><option value="created_at:desc">Newest created</option><option value="created_at:asc">Oldest created</option><option value="last_price:desc">Last price ↓</option><option value="display_pnl:desc">P&amp;L ↓</option><option value="instrument_symbol:asc">Symbol A–Z</option><option value="instrument_symbol:desc">Symbol Z–A</option></select></div>
      <div className="trade-filter-group"><span className="trade-filter-label">Trade date</span><div className="trade-filter-buttons">{[['ALL','All'],['TODAY','Today'],['WEEK','This Week'],['MONTH','This Month'],['CUSTOM','Custom']].map(([value,label]) => <button key={value} type="button" className={(dateFilter === value ? 'trade-filter-button active' : 'trade-filter-button')} onClick={() => setDateFilter(value)}>{label}</button>)}</div>{dateFilter === 'CUSTOM' && <input className="trade-date-custom" type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />}</div>
      <span className="trade-terminal-count">{visibleTrades.length} of {trades.length} trades · live updates</span>
    </div>
    <section className="panel trade-terminal-table-panel"><div className="table-scroll trade-terminal-table-scroll"><table className="kite-data-table trade-terminal-table"><thead><tr>{[['execution_mode','Mode'],['underlying','Underlying'],['instrument_symbol','Instrument'],['direction','Side'],['quantity','Qty'],['last_price','Last price'],['average_entry_price','Entry'],['stop_loss','SL'],['target','Target'],['display_pnl','Calculated P&L'],['average_exit_price','Exit price'],['status','Status'],['source','Source'],['strategy','Strategy'],['trailing_strategy','Trailing Strategy'],['updated_at','Updated']].map(([key,label]) => <th key={key}><button type="button" className="trade-sort-header" onClick={() => toggleSort(key)}>{label}{sortConfig.key === key ? (sortConfig.direction === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>)}<th>Action</th></tr></thead><tbody>
      {displayedTrades.map((trade) => { return <tr key={trade.trade_id}><td><span className={'trade-mode-pill ' + String(trade.execution_mode).toLowerCase()}>{trade.execution_mode}</span></td><td>{fmt(trade.underlying)}</td><td><button type="button" className="trade-symbol-link" onClick={() => setDetailsTrade(trade)}>{fmt(trade.instrument_symbol)}</button></td><td>{fmt(trade.direction)}</td><td>{fmt(trade.quantity)}</td><td>{fmt(trade.average_entry_price ?? trade.entry_price)}</td><td>{fmt(trade.last_price)}</td><td>{fmt(trade.stop_loss)}</td><td>{fmt(trade.target)}</td><td className={trade.display_pnl === null ? 'holding-pnl-zero' : trade.display_pnl > 0 ? 'holding-pnl-positive' : trade.display_pnl < 0 ? 'holding-pnl-negative' : 'holding-pnl-zero'}>{fmt(trade.display_pnl)}</td><td>{fmt(trade.average_exit_price ?? trade.exit_price)}</td><td>{fmt(trade.status)}</td><td>{fmt(trade.source)}</td><td>{fmt(trade.strategy)}</td><td>{fmt(trade.trailing_strategy)}</td><td>{trade.updated_at ? new Date(trade.updated_at).toLocaleString() : '—'}{trade.persistence_state === 'DEGRADED' && <div className="negative">Persistence issue</div>}</td><td>{trade.execution_mode === 'PAPER' && !isTerminalStatus(trade.status) && <button className="button secondary trade-close-button" onClick={() => openCloseDialog(trade)}>Close</button>}</td></tr> })}
      {!displayedTrades.length && <tr><td colSpan="18" className="empty-cell">{loading ? 'Loading canonical trades…' : 'No trades match these filters. Create a paper trade or sync live positions.'}</td></tr>}
    </tbody></table></div><div className="panel-footer">Durable lifecycle events are saved immediately. Quote and unrealized P&amp;L updates are cache-only. Live broker reconciliation is read-only and remains a separate integration step.</div></section>
  </section>;
}
