import React, { useCallback, useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [form, setForm] = useState({ instrument_symbol: '', underlying: 'NIFTY', direction: 'BUY', quantity: '1', entry_price: '', stop_loss: '', target: '', strategy: 'Manual Paper Trade', notes: '' });

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

  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 5000); return () => window.clearInterval(timer); }, [refresh]);

  const create = async (event) => {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const payload = { ...form, idempotency_key: requestKey, execution_mode: 'PAPER', source: 'MANUAL', quantity: Number(form.quantity) };
      for (const key of ['entry_price', 'stop_loss', 'target']) payload[key] = payload[key] === '' ? null : Number(payload[key]);
      const result = await request('/trades', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      setShowCreate(false);
      setRequestKey(crypto.randomUUID());
      setForm({ instrument_symbol: '', underlying: 'NIFTY', direction: 'BUY', quantity: '1', entry_price: '', stop_loss: '', target: '', strategy: 'Manual Paper Trade', notes: '' });
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

  const updateStatus = async (trade, nextStatus) => {
    let changes = { status: nextStatus };
    if (nextStatus === 'CLOSED') {
      const rawExit = window.prompt('Enter the paper-trade exit price to record:');
      if (rawExit === null) return;
      const exitPrice = Number(rawExit);
      if (!Number.isFinite(exitPrice) || exitPrice < 0) { setError('Enter a valid non-negative exit price.'); return; }
      const rawEntryPrice = trade.average_entry_price ?? trade.entry_price;
      const entryPrice = rawEntryPrice === null || rawEntryPrice === undefined || rawEntryPrice === '' ? NaN : Number(rawEntryPrice);
      const qty = Number(trade.open_quantity ?? trade.quantity ?? 0);
      const directionFactor = String(trade.direction).toUpperCase() === 'SELL' ? -1 : 1;
      changes = { ...changes, exit_price: exitPrice, average_exit_price: exitPrice, open_quantity: 0 };
      if (Number.isFinite(entryPrice)) changes.realized_pnl = (exitPrice - entryPrice) * qty * directionFactor;
    }
    try {
      await request(`/trades/${encodeURIComponent(trade.trade_id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
      await refresh();
    } catch (e) { setError(e.message || 'Trade update failed.'); }
  };

  return <section className="trade-terminal-page">
    <div className="page-heading"><div><div className="eyebrow">EXECUTION MONITOR</div><h1>Trade Terminal</h1><p>One canonical record per trade. Paper and live modes share this terminal; live order placement is not enabled here.</p></div><div className="trade-terminal-actions"><button className="button secondary" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : '↻ Refresh'}</button><button className="button secondary" onClick={reconcileLive} disabled={loading}>{loading ? 'Syncing…' : 'Sync Live Positions'}</button><button className="button primary" onClick={() => setShowCreate((v) => !v)}>{showCreate ? 'Cancel' : '+ New Paper Trade'}</button></div></div>
    {error && <div className="error-banner"><strong>Trade terminal issue</strong><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
    <div className="trade-terminal-metrics">
      <div className="metric"><span>Cached trades</span><strong>{fmt(terminal.cached_trades ?? 0)}</strong></div>
      <div className="metric"><span>Open trades</span><strong>{fmt(terminal.open_trades ?? 0)}</strong></div>
      <div className="metric"><span>Pending persistence</span><strong className={terminal.persistence_degraded ? 'negative' : 'positive'}>{fmt(terminal.dirty_trades ?? 0)}</strong></div>
    </div>
    {terminal.persistence_degraded && <div className="trade-persistence-warning">Database persistence is degraded. Do not assume cache-only changes are durable. {terminal.persistence_error || ''}</div>}
    {showCreate && <form className="panel trade-create-form" onSubmit={create}>
      <h2>Create paper trade</h2><div className="settings-grid">
        <label className="settings-control"><span>Instrument symbol *</span><input required value={form.instrument_symbol} onChange={(e) => setForm({ ...form, instrument_symbol: e.target.value.toUpperCase() })} placeholder="NIFTY26OCT25000CE" /></label>
        <label className="settings-control"><span>Underlying / index</span><select value={form.underlying} onChange={(e) => setForm({ ...form, underlying: e.target.value })}>{['NIFTY','BANKNIFTY','FINNIFTY','SENSEX','OTHER'].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label className="settings-control"><span>Direction</span><select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}><option>BUY</option><option>SELL</option></select></label>
        <label className="settings-control"><span>Quantity *</span><input type="number" min="1" required value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
        <label className="settings-control"><span>Entry price</span><input type="number" min="0" step="any" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })} /></label>
        <label className="settings-control"><span>Stop loss</span><input type="number" min="0" step="any" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} /></label>
        <label className="settings-control"><span>Target</span><input type="number" min="0" step="any" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} /></label>
        <label className="settings-control"><span>Strategy / source label</span><input value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })} /></label>
        <label className="settings-control trade-notes-field"><span>Notes</span><input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength="2000" /></label>
      </div><div className="settings-actions"><span className="trade-paper-label">PAPER MODE · no broker order will be placed</span><button className="button primary" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Create paper trade'}</button></div>
    </form>}
    <div className="trade-terminal-filters">
      <label className="settings-control"><span>Mode</span><select value={mode} onChange={(e) => setMode(e.target.value)}><option value="ALL">All modes</option><option value="PAPER">Paper</option><option value="LIVE">Live</option></select></label>
      <label className="settings-control"><span>Status</span><select value={status} onChange={(e) => setStatus(e.target.value)}><option value="ALL">All statuses</option>{['OPEN','PENDING','PARTIALLY_FILLED','CLOSED','CANCELLED','REJECTED','EXPIRED'].map((x) => <option key={x}>{x}</option>)}</select></label>
      <span className="trade-terminal-count">{trades.length} trades · auto-refresh every 5 seconds</span>
    </div>
    <section className="panel trade-terminal-table-panel"><div className="table-scroll trade-terminal-table-scroll"><table className="kite-data-table trade-terminal-table"><thead><tr><th>Mode</th><th>Trade ID</th><th>Underlying</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Entry</th><th>Last price</th><th>SL</th><th>Target</th><th>Realized P&amp;L</th><th>Unrealized P&amp;L</th><th>Status</th><th>Source / Strategy</th><th>Updated</th><th>Action</th></tr></thead><tbody>
      {trades.map((trade) => <tr key={trade.trade_id}><td><span className={'trade-mode-pill ' + String(trade.execution_mode).toLowerCase()}>{trade.execution_mode}</span></td><td title={trade.trade_id}>{String(trade.trade_id).slice(0, 8)}</td><td>{fmt(trade.underlying)}</td><td>{fmt(trade.instrument_symbol)}</td><td>{fmt(trade.direction)}</td><td>{fmt(trade.open_quantity ?? trade.quantity)}</td><td>{fmt(trade.average_entry_price ?? trade.entry_price)}</td><td>{fmt(trade.last_price)}</td><td>{fmt(trade.stop_loss)}</td><td>{fmt(trade.target)}</td><td className={Number(trade.realized_pnl || 0) >= 0 ? 'holding-pnl-positive' : 'holding-pnl-negative'}>{fmt(Number(trade.realized_pnl || 0))}</td><td className={Number(trade.unrealized_pnl || 0) >= 0 ? 'holding-pnl-positive' : 'holding-pnl-negative'}>{fmt(Number(trade.unrealized_pnl || 0))}</td><td>{fmt(trade.status)}</td><td>{fmt(trade.source)}{trade.strategy ? ` · ${trade.strategy}` : ''}</td><td>{trade.updated_at ? new Date(trade.updated_at).toLocaleString() : '—'}{trade.persistence_state === 'DEGRADED' && <div className="negative">Persistence issue</div>}</td><td>{trade.execution_mode === 'PAPER' && !['CLOSED','CANCELLED','REJECTED','EXPIRED'].includes(String(trade.status).toUpperCase()) && <button className="button secondary trade-close-button" onClick={() => updateStatus(trade, 'CLOSED')}>Close</button>}</td></tr>)}
      {!trades.length && <tr><td colSpan="16" className="empty-cell">{loading ? 'Loading canonical trades…' : 'No trades match these filters. Create a paper trade or connect an existing trade source.'}</td></tr>}
    </tbody></table></div><div className="panel-footer">Durable lifecycle events are saved immediately. Quote and unrealized P&amp;L updates are cache-only. Live broker reconciliation is read-only and remains a separate integration step.</div></section>
  </section>;
}
