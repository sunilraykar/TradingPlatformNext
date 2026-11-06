import React, { useCallback, useEffect, useMemo, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const SECTIONS = [
  ['overview', 'Overview'],
  ['option-chain', 'Option Chain'],
  ['market-direction', 'Market Direction'],
  ['rs-status', 'RS Status'],
  ['resistance', 'Resistance'],
  ['support', 'Support'],
  ['shifting', 'Shifting'],
  ['raw', 'Raw JSON'],
];

async function request(path) {
  const response = await fetch(`${API}/api/v1${path}`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.detail === 'string' ? payload.detail : `Request failed (${response.status})`);
  }
  return payload;
}

function display(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString('en-IN', { maximumFractionDigits: 2 })
    : String(value);
}

function DataTable({ rows, emptyText = 'No records in this section.' }) {
  const columns = useMemo(() => {
    const keys = new Set();
    rows.slice(0, 10).forEach((row) => {
      if (row && typeof row === 'object' && !Array.isArray(row)) {
        Object.keys(row).forEach((key) => keys.add(key));
      }
    });
    return [...keys];
  }, [rows]);

  if (!rows.length) return <div className="apiplatform-empty">{emptyText}</div>;
  return <div className="apiplatform-table-wrap"><table className="apiplatform-data-table">
    <thead><tr>{columns.map((key) => <th key={key}>{key}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={row?._id || index}>
      {columns.map((key) => <td key={key}>{display(row?.[key])}</td>)}
    </tr>)}</tbody>
  </table></div>;
}

export default function ApiplatformOptionChain() {
  const [symbol, setSymbol] = useState('NIFTY');
  const [expiry, setExpiry] = useState('09-11-2026');
  const [lotSize, setLotSize] = useState('65');
  const [snapshot, setSnapshot] = useState(null);
  const [section, setSection] = useState('overview');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cacheLoading, setCacheLoading] = useState(true);

  const loadCache = useCallback(async () => {
    setCacheLoading(true);
    try {
      const cached = await request('/apiplatform/option-chain/cache');
      if (cached.cached) {
        setSnapshot(cached);
        setSymbol(cached.symbol || 'NIFTY');
        setExpiry(cached.expiry || '09-11-2026');
        setLotSize(String(cached.lot_size || 65));
      } else {
        setSnapshot(null);
      }
      setError('');
    } catch (loadError) {
      setError(loadError.message || 'Unable to read APIPLATFORM cache.');
    } finally {
      setCacheLoading(false);
    }
  }, []);

  const fetchLatest = async () => {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams({
        symbol: symbol.trim().toUpperCase(),
        expiry: expiry.trim(),
        lot_size: lotSize,
      });
      const result = await request(`/apiplatform/option-chain/fetch?${query.toString()}`);
      setSnapshot(result);
      setSection('overview');
    } catch (fetchError) {
      setError(fetchError.message || 'Unable to fetch APIPLATFORM option-chain data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadCache(); }, [loadCache]);

  const payload = snapshot?.payload || {};
  const direction = payload.symbolMarketDirection || {};
  const reversal = direction.reversalModel || {};
  const rsStatus = Array.isArray(payload.RS_Status) ? payload.RS_Status : [];
  const optionRows = Array.isArray(payload.OptionChain) ? [...payload.OptionChain] : [];
  optionRows.sort((left, right) => Number(left?.strike || 0) - Number(right?.strike || 0));
  const resistanceRows = Array.isArray(direction.resistanceData) ? direction.resistanceData : [];
  const supportRows = Array.isArray(direction.supportData) ? direction.supportData : [];
  const shiftingRows = [
    ...(Array.isArray(payload.rshifting) ? payload.rshifting.map((row) => ({ ...row, _section: 'Resistance' })) : []),
    ...(Array.isArray(payload.sshifting) ? payload.sshifting.map((row) => ({ ...row, _section: 'Support' })) : []),
  ];
  const fetchedAt = snapshot?.fetch_time || direction.actualFetchTime || payload.FetchTime;

  return <main className="apiplatform-page">
    <section className="apiplatform-heading">
      <div><div className="eyebrow">MARKET INTELLIGENCE / APIPLATFORM</div><h1>APIPLATFORM Option Chain</h1>
        <p>Complete provider response with the latest in-memory cache snapshot.</p></div>
      <div className="apiplatform-cache-state"><span className={snapshot ? 'apiplatform-dot live' : 'apiplatform-dot'} />
        {snapshot ? 'Cached response available' : cacheLoading ? 'Checking cache…' : 'Cache empty'}</div>
    </section>

    <section className="panel apiplatform-controls">
      <label>Underlying<select value={symbol} onChange={(event) => setSymbol(event.target.value)}>
        {['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'MIDCPNIFTY'].map((item) => <option key={item}>{item}</option>)}
      </select></label>
      <label>Expiry<input value={expiry} onChange={(event) => setExpiry(event.target.value)} placeholder="DD-MM-YYYY" /></label>
      <label>Lot size<input type="number" min="1" value={lotSize} onChange={(event) => setLotSize(event.target.value)} /></label>
      <button type="button" className="button primary" onClick={fetchLatest} disabled={loading || !expiry.trim() || Number(lotSize) <= 0}>
        {loading ? 'Fetching live response…' : 'Fetch latest & update cache'}
      </button>
      <button type="button" className="button secondary" onClick={loadCache} disabled={cacheLoading || loading}>Read cache</button>
    </section>

    {error && <div className="error-banner"><strong>APIPLATFORM request issue</strong><span>{error}</span></div>}
    {snapshot && <section className="apiplatform-cache-meta">
      <div><span>Symbol / expiry</span><strong>{snapshot.symbol} · {snapshot.expiry}</strong></div>
      <div><span>Provider fetch time</span><strong>{fetchedAt || '—'}</strong></div>
      <div><span>Cached at</span><strong>{snapshot.cached_at ? new Date(snapshot.cached_at).toLocaleString('en-IN') : '—'}</strong></div>
      <div><span>Option-chain rows</span><strong>{(payload.OptionChain || []).length.toLocaleString('en-IN')}</strong></div>
      <div><span>Spot price</span><strong>{formatNumber(payload.Price_S)}</strong></div>
      <div><span>Market direction</span><strong>{direction.marketDirection || '—'}</strong></div>
    </section>}

    {!snapshot && !cacheLoading && !loading && <section className="panel apiplatform-empty-state">
      <h2>No APIPLATFORM response is cached yet</h2><p>Fetch the latest response to save the complete JSON in the backend cache and display it here.</p>
    </section>}

    {snapshot && <>
      <nav className="apiplatform-sections" aria-label="APIPLATFORM response sections">
        {SECTIONS.map(([key, label]) => <button type="button" key={key} className={section === key ? 'active' : ''} onClick={() => setSection(key)}>{label}</button>)}
      </nav>

      {section === 'overview' && <div className="apiplatform-overview">
        <section className="panel apiplatform-market-card"><div className="panel-header"><div><div className="eyebrow">SYMBOL MARKET DIRECTION</div><h2>{direction.marketDirection || 'Market direction unavailable'}</h2></div><strong>{formatNumber(payload.Price_S)}</strong></div>
          <div className="apiplatform-reversal-grid">{Object.entries(reversal).map(([key, value]) => <div key={key}><span>{key}</span><strong>{display(value)}</strong></div>)}</div>
        </section>
        <section className="apiplatform-count-grid">
          {[
            ['OptionChain', optionRows.length],
            ['Resistance data', resistanceRows.length],
            ['Support data', supportRows.length],
            ['rshifting', Array.isArray(payload.rshifting) ? payload.rshifting.length : 0],
            ['sshifting', Array.isArray(payload.sshifting) ? payload.sshifting.length : 0],
            ['RS_Status', rsStatus.length],
          ].map(([label, value]) => <article className="panel" key={label}><span>{label}</span><strong>{value.toLocaleString('en-IN')}</strong></article>)}
        </section>
        <section className="panel apiplatform-overview-table"><div className="panel-header"><h2>Current RS Status</h2></div><DataTable rows={rsStatus} /></section>
      </div>}

      {section === 'option-chain' && <section className="panel apiplatform-data-panel">
        <div className="panel-header"><div><h2>Option Chain</h2><p>{optionRows.length} strike records · Calls and puts</p></div></div>
        <div className="apiplatform-table-wrap"><table className="apiplatform-option-table">
          <thead><tr><th colSpan="6">CALLS (CE)</th><th>STRIKE</th><th colSpan="6">PUTS (PE)</th></tr>
            <tr><th>OI</th><th>OI Chg</th><th>Volume</th><th>LTP</th><th>IV</th><th>Delta</th><th>Price</th><th>Delta</th><th>IV</th><th>LTP</th><th>Volume</th><th>OI Chg</th><th>OI</th></tr></thead>
          <tbody>{optionRows.map((row, index) => <tr key={row?._id || row?.strike || index}>
            <td>{formatNumber(row.callOI)}</td><td>{formatNumber(row.callOIchg)}</td><td>{formatNumber(row.callVol)}</td><td>{formatNumber(row.callltp)}</td><td>{formatNumber(row.civ)}</td><td>{formatNumber(row.cdelta)}</td>
            <th>{formatNumber(row.strike)}</th>
            <td>{formatNumber(row.pdelta)}</td><td>{formatNumber(row.putIv)}</td><td>{formatNumber(row.putLTP)}</td><td>{formatNumber(row.putVol)}</td><td>{formatNumber(row.putOIchg)}</td><td>{formatNumber(row.putOI)}</td>
          </tr>)}</tbody>
        </table></div>
      </section>}

      {section === 'market-direction' && <div className="apiplatform-data-stack">
        <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>Resistance data ({resistanceRows.length})</h2></div><DataTable rows={resistanceRows} /></section>
        <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>Support data ({supportRows.length})</h2></div><DataTable rows={supportRows} /></section>
      </div>}

      {section === 'rs-status' && <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>RS Status ({rsStatus.length})</h2></div><DataTable rows={rsStatus} /></section>}
      {section === 'resistance' && <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>Resistance ({resistanceRows.length})</h2></div><DataTable rows={resistanceRows} /></section>}
      {section === 'support' && <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>Support ({supportRows.length})</h2></div><DataTable rows={supportRows} /></section>}
      {section === 'shifting' && <section className="panel apiplatform-data-panel"><div className="panel-header"><h2>Resistance and support shifting ({shiftingRows.length})</h2></div><DataTable rows={shiftingRows} /></section>}
      {section === 'raw' && <section className="panel apiplatform-raw-panel"><div className="panel-header"><h2>Complete raw JSON</h2><button type="button" className="button secondary" onClick={() => navigator.clipboard?.writeText(JSON.stringify(payload, null, 2))}>Copy JSON</button></div><pre>{JSON.stringify(payload, null, 2)}</pre></section>}
    </>}
  </main>;
}
