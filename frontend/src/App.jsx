import { useCallback, useEffect, useMemo, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const INDICES = ['FINNIFTY', 'SENSEX'];

async function api(path, options = {}) {
  const response = await fetch(`${API}/api/v1${path}`, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === 'string' ? payload.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return payload;
}

function number(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString('en-IN', { maximumFractionDigits: digits }) : String(value);
}

function timeLabel(value) {
  if (value === null || value === undefined) return '';
  const raw = String(value);
  if (/^\d{3,4}$/.test(raw)) return `${raw.padStart(4, '0').slice(0, 2)}:${raw.slice(-2)}`;
  return raw;
}

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') {
    // AutoTrender may encode signals as { type, colorCode } objects.
    return value.type ?? value.label ?? value.name ?? value.value ?? '—';
  }
  return String(value);
}

function extractTrend(payload) {
  const trend = payload?.data?.trend || payload?.trend || {};
  return {
    call: trend.call ?? trend.callPercent ?? trend.call_percentage,
    put: trend.put ?? trend.putPercent ?? trend.put_percentage,
    signal: trend.signal ?? trend.optionSignal ?? trend.title,
  };
}

function linePoints(series, width = 720, height = 220) {
  const values = series.map((point) => Number(point.value)).filter(Number.isFinite);
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return series.map((point, i) => {
    const x = 12 + (i / Math.max(series.length - 1, 1)) * (width - 24);
    const y = height - 18 - ((Number(point.value) - min) / span) * (height - 36);
    return `${x},${y}`;
  }).join(' ');
}

function OiChart({ series }) {
  const usable = (Array.isArray(series) ? series : [])
    .map((p) => ({ value: Number(p.value), time: p.time }))
    .filter((p) => Number.isFinite(p.value));
  if (!usable.length || usable.every((p) => p.value === 0)) {
    return <div className="chart-empty">No usable OI series available from the upstream response.</div>;
  }
  const values = usable.map((p) => p.value);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const y = (value) => 202 - ((value - min) / (max - min || 1)) * 168;
  const points = linePoints(usable);
  return (
    <div className="chart-wrap">
      <div className="chart-axis"><span>{number(max, 0)}</span><span>0</span><span>{number(min, 0)}</span></div>
      <svg className="oi-chart" viewBox="0 0 720 220" role="img" aria-label="Change in open interest through the session" preserveAspectRatio="none">
        <line x1="12" x2="708" y1={y(0)} y2={y(0)} className="zero-line" />
        <polyline points={points} className="oi-line" />
        {usable.map((p, i) => <circle key={`${p.time}-${i}`} cx={12 + (i / Math.max(usable.length - 1, 1)) * 696} cy={y(p.value)} r="2.5" className="oi-dot" />)}
      </svg>
      <div className="chart-times"><span>{timeLabel(usable[0]?.time)}</span><span>{timeLabel(usable[Math.floor(usable.length / 2)]?.time)}</span><span>{timeLabel(usable[usable.length - 1]?.time)}</span></div>
    </div>
  );
}

function Metric({ label, value, tone }) {
  return <div className="metric"><span>{label}</span><strong className={tone || ''}>{value ?? '—'}</strong></div>;
}

export default function App() {
  const [index, setIndex] = useState('FINNIFTY');
  const [mins, setMins] = useState(5);
  const [intraday, setIntraday] = useState(null);
  const [oi, setOi] = useState(null);
  const [auth, setAuth] = useState(null);
  const [cache, setCache] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      api(`/autotrender/indices/${index}/intraday?mins=${mins}`),
      api(`/autotrender/indices/${index}/oi-graph`),
      api('/autotrender/auth/status'),
      api('/autotrender/cache/status'),
    ]);
    if (results[0].status === 'fulfilled') setIntraday(results[0].value);
    else setError((prev) => prev || `Intraday: ${results[0].reason.message}`);
    if (results[1].status === 'fulfilled') setOi(results[1].value);
    else setError((prev) => prev || `OI graph: ${results[1].reason.message}`);
    if (results[2].status === 'fulfilled') setAuth(results[2].value);
    if (results[3].status === 'fulfilled') setCache(results[3].value);
    setLastUpdated(new Date());
    setLoading(false);
  }, [index, mins]);

  useEffect(() => { refresh(); }, [refresh]);

  const login = async () => {
    setLoggingIn(true);
    setError('');
    try {
      await api('/autotrender/auth/login', { method: 'POST' });
      const status = await api('/autotrender/auth/status');
      setAuth(status);
      await refresh();
    } catch (e) {
      setError(`AutoTrender login: ${e.message}`);
    } finally {
      setLoggingIn(false);
    }
  };

  const trend = useMemo(() => extractTrend(oi), [oi]);
  const intradayRows = Array.isArray(intraday?.data) ? intraday.data : [];
  const latest = intradayRows[intradayRows.length - 1] || {};
  const graph = oi?.data?.graph || {};
  const optionSeries = Array.isArray(graph.option) ? graph.option : [];
  const futureSeries = Array.isArray(graph.future) ? graph.future : [];
  const stale = Boolean(cache?.last_refresh_error || cache?.cache?.stale);
  const hasMarketData = Boolean(intraday || oi);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">TP</div>
        <div className="brand-copy"><strong>TradingPlatformNext</strong><span>MARKET INTELLIGENCE</span></div>
        <div className="topbar-right">
          <span className={`status-pill ${auth?.authenticated ? 'online' : 'offline'}`}><i />{auth?.authenticated ? 'AutoTrender connected' : 'AutoTrender not connected'}</span>
          <button className="button secondary" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : '↻ Refresh'}</button>
        </div>
      </header>

      <section className="page-heading">
        <div><div className="eyebrow">READ-ONLY MARKET DATA</div><h1>AutoTrender Monitor</h1><p>Intraday option strength and change-in-open-interest for FINNIFTY and SENSEX.</p></div>
        <div className="heading-meta"><span className={stale ? 'stale' : 'fresh'}>{stale ? 'Check data freshness' : 'Source status'}</span><small>Updated {lastUpdated ? lastUpdated.toLocaleTimeString('en-IN') : '—'}</small></div>
      </section>

      <section className="control-panel">
        <div className="index-switch" aria-label="Select index">
          {INDICES.map((item) => <button key={item} className={index === item ? 'selected' : ''} onClick={() => setIndex(item)}>{item}</button>)}
        </div>
        <label className="interval-select">Intraday interval
          <select value={mins} onChange={(e) => setMins(Number(e.target.value))}>
            {[1, 3, 5, 10, 15, 30, 60].map((m) => <option key={m} value={m}>{m} min</option>)}
          </select>
        </label>
        <div className="control-note">Market data only · No order placement</div>
      </section>

      {error && <div className="error-banner"><strong>Data request issue</strong><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}

      {!auth?.authenticated && <section className="auth-notice">
        <div className="notice-icon">!</div><div><strong>AutoTrender authentication required</strong><p>Use the configured backend credentials to establish a session before refreshing market data.</p></div>
        <button className="button primary" onClick={login} disabled={loggingIn}>{loggingIn ? 'Connecting…' : 'Connect AutoTrender'}</button>
      </section>}

      <section className="metric-grid">
        <Metric label="INDEX" value={index} />
        <Metric label="LATEST PRICE" value={number(latest.price ?? latest.close ?? latest.ltp)} />
        <Metric label="CALL STRENGTH" value={trend.call == null ? '—' : `${number(trend.call)}%`} tone="positive" />
        <Metric label="PUT STRENGTH" value={trend.put == null ? '—' : `${number(trend.put)}%`} tone="negative" />
        <Metric label="OPTION SIGNAL" value={displayValue(trend.signal ?? latest.optionSignal)} />
      </section>

      <section className="content-grid">
        <article className="panel intraday-panel">
          <div className="panel-header"><div><div className="eyebrow">OPTION STRENGTH</div><h2>Intraday data</h2></div><span className="data-count">{intradayRows.length} points</span></div>
          <div className="table-scroll"><table><thead><tr><th>Time</th><th>Price</th><th>VWAP</th><th>PCR</th><th>Option signal</th><th>VWAP signal</th></tr></thead>
            <tbody>{intradayRows.slice(-30).reverse().map((row, i) => <tr key={`${row.time || row.mtime || i}`}><td>{timeLabel(row.time ?? row.mtime)}</td><td>{number(row.price)}</td><td>{number(row.vWap ?? row.vwap)}</td><td>{number(row.pcr?.value ?? row.pcr)}</td><td><span className="signal-tag">{displayValue(row.optionSignal)}</span></td><td>{displayValue(row.vWapSignal ?? row.vwapSignal)}</td></tr>)}
            {!intradayRows.length && <tr><td colSpan="6" className="empty-cell">{loading ? 'Loading market data…' : 'No intraday records returned.'}</td></tr>}</tbody>
          </table></div>
          <div className="panel-footer">Showing latest {Math.min(intradayRows.length, 30)} records from the API response.</div>
        </article>

        <article className="panel oi-panel">
          <div className="panel-header"><div><div className="eyebrow">CHANGE IN OPEN INTEREST</div><h2>OI graph</h2></div><span className="data-count">{optionSeries.length} points</span></div>
          <div className="legend"><span><i className="legend-option" />Option OI</span><span><i className="legend-future" />Future OI</span></div>
          <OiChart series={optionSeries} />
          <div className="series-summary"><div><span>Latest option OI change</span><strong>{number(optionSeries.at(-1)?.value, 0)}</strong></div><div><span>Latest future OI change</span><strong>{number(futureSeries.at(-1)?.value, 0)}</strong></div></div>
          {futureSeries.length > 0 && futureSeries.every((p) => Number(p.value) === 0) && <p className="subtle-warning">The upstream future series contains only zero values; it is not plotted as a meaningful trend.</p>}
        </article>
      </section>

      <footer className="footer">
        <span>{hasMarketData ? 'Data received from AutoTrender API' : 'Waiting for AutoTrender market data'}</span>
        <span>Cache {cache?.cached ? `${number(cache.cache_age_seconds, 1)}s old` : 'not populated'} · API proxy: local backend</span>
      </footer>
    </main>
  );
}