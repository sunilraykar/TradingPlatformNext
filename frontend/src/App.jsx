import { useCallback, useEffect, useMemo, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const DEFAULT_INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX'];
const INDEX_LABELS = {
  nifty: 'NIFTY',
  banknifty: 'BANKNIFTY',
  finnifty: 'FINNIFTY',
  midcpnifty: 'MIDCPNIFTY',
  sensex: 'SENSEX',
  bankex: 'BANKEX',
};

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

function scalarValue(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'object') return value;
  for (const key of ['value', 'price', 'ltp', 'close', 'current', 'amount', 'number', 'type']) {
    const candidate = value[key];
    if (candidate !== null && candidate !== undefined && candidate !== '' && typeof candidate !== 'object') {
      return candidate;
    }
  }
  return null;
}

function number(value, digits = 2) {
  const scalar = scalarValue(value);
  if (scalar === null) return '—';
  const n = Number(scalar);
  return Number.isFinite(n) ? n.toLocaleString('en-IN', { maximumFractionDigits: digits }) : String(scalar);
}

function timeLabel(value) {
  if (value === null || value === undefined) return '';
  const raw = String(value);
  if (/^\d{3,4}$/.test(raw)) return `${raw.padStart(4, '0').slice(0, 2)}:${raw.slice(-2)}`;
  return raw;
}

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value !== 'object') return String(value);
  for (const key of ['type', 'label', 'name', 'value', 'status', 'signal', 'text', 'price', 'ltp', 'close']) {
    const candidate = value[key];
    if (candidate !== null && candidate !== undefined && candidate !== '' && typeof candidate !== 'object') {
      return String(candidate);
    }
  }
  return '—';
}

function extractTrend(payload) {
  const candidates = [payload?.data?.trend, payload?.trend, payload?.data?.summary, payload?.data?.strength, payload?.data, payload?.summary, payload];
  const source = candidates.find((item) => item && typeof item === 'object' && !Array.isArray(item) && (item.call !== undefined || item.put !== undefined || item.signal !== undefined || item.optionSignal !== undefined)) || {};
  const call = numericValue(source.call ?? source.callStrength ?? source.call_strength ?? source.callPercentage ?? source.call_percentage);
  const put = numericValue(source.put ?? source.putStrength ?? source.put_strength ?? source.putPercentage ?? source.put_percentage);
  const signal = source.signal ?? source.optionSignal ?? source.option_signal ?? source.title;
  return { call, put, signal };
}

function numericValue(value) {
  const scalar = scalarValue(value);
  if (scalar === null || scalar === '') return null;
  // AutoTrender sends Indian-formatted numeric strings such as "1,49,640"
  // and wraps fields like price, VWAP, PCR and diff in { value, colorCode }.
  // scalarValue unwraps the object; remove grouping commas before parsing.
  const normalized = String(scalar).replace(/,/g, '').replace(/%/g, '').trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function signalValue(value) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'object') return String(value).trim().toUpperCase();
  for (const key of ['type', 'value', 'signal', 'label', 'status', 'name', 'text']) {
    const candidate = value[key];
    if (candidate !== null && candidate !== undefined && typeof candidate !== 'object' && candidate !== '') return String(candidate).trim().toUpperCase();
  }
  return '';
}

function recordTime(row) {
  return timeLabel(row.time ?? row.mtime ?? row.timestamp ?? row.dateTime ?? row.datetime ?? row.createdAt);
}

function buildIntradayRows(rows) {
  return rows.map((row, index) => {
    const call = numericValue(row.call);
    const put = numericValue(row.put);
    const total = call !== null && put !== null ? call + put : null;
    const callStrength = total !== null && total > 0 ? (call / total) * 100 : null;
    const putStrength = total !== null && total > 0 ? (put / total) * 100 : null;
    // Positive difference means call strength is higher.
    const strengthDiff = callStrength !== null && putStrength !== null ? callStrength - putStrength : null;
    const price = numericValue(row.price ?? row.close ?? row.ltp);
    const vwap = numericValue(row.vWap ?? row.vwap);
    const buffer = price !== null && vwap !== null && vwap !== 0 ? ((price - vwap) / vwap) * 100 : null;
    const optionSignal = signalValue(row.optionSignal);
    const vwapSignal = signalValue(row.vWapSignal ?? row.vwapSignal);
    const pcr = numericValue(row.pcr);
    let enter = '—';
    if (optionSignal === 'BUY' && vwapSignal === 'BUY') enter = strengthDiff !== null && strengthDiff > 10 && pcr !== null && pcr > 1 && buffer !== null && Math.abs(buffer) < 0.3 ? 'BUY' : 'WAIT';
    else if (optionSignal === 'SELL' && vwapSignal === 'SELL') enter = strengthDiff !== null && strengthDiff < -10 && pcr !== null && pcr < 1 && buffer !== null && Math.abs(buffer) < 0.3 ? 'SELL' : 'WAIT';
    return { key: recordTime(row) + '-' + index, time: recordTime(row), call, put, diff: numericValue(row.diff), pcr, price, vwap, vwapSignal, optionSignal, callStrength, putStrength, strengthDiff, buffer, enter };
  });
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

function PriceVwapChart({ rows }) {
  const parseMinutes = (value) => {
    const raw = String(value || '').trim();
    const match = raw.match(/(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)/) || raw.match(/^(\d{1,2}):(\d{2})$/);
    if (!match) return null;
    return Number(match[1]) * 60 + Number(match[2]);
  };
  const sessionStart = 9 * 60 + 30;
  const sessionEnd = 15 * 60 + 30;
  // Plot chronologically from 09:30 to 15:30 regardless of API response order.
  const usable = rows
    .map((row) => ({ time: row.time, minutes: parseMinutes(row.time), price: row.price, vwap: row.vwap, pcr: row.pcr, buffer: row.buffer }))
    .filter((row) => row.minutes !== null && row.minutes >= sessionStart && row.minutes <= sessionEnd && (row.price !== null || row.vwap !== null))
    .sort((a, b) => a.minutes - b.minutes);
  const values = usable.flatMap((row) => [row.price, row.vwap]).filter((value) => value !== null && Number.isFinite(value));
  if (usable.length < 2 || !values.length) {
    return <div className="chart-empty">Price/VWAP history for 09:30–15:30 is unavailable in the current intraday API response.</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (minutes) => 12 + ((minutes - sessionStart) / (sessionEnd - sessionStart)) * 696;
  const y = (value) => 202 - ((value - min) / span) * 168;
  const pathFor = (key) => usable.map((row) => row[key] === null ? null : `${x(row.minutes)},${y(row[key])}`).filter(Boolean).join(' ');
  const latest = usable[usable.length - 1];
  const gap = latest.price !== null && latest.vwap !== null ? latest.price - latest.vwap : null;
  const buffer = latest.buffer ?? (latest.price !== null && latest.vwap !== null && latest.vwap !== 0 ? ((latest.price - latest.vwap) / latest.vwap) * 100 : null);
  const ticks = Array.from({ length: 13 }, (_, i) => sessionStart + i * 30);
  return <>
    <div className="legend price-vwap-legend"><span><i className="legend-price" /> Price</span><span><i className="legend-vwap" /> VWAP</span></div>
    <div className="chart-wrap price-vwap-wrap">
      <div className="chart-axis"><span>{number(max)}</span><span>{number((max + min) / 2)}</span><span>{number(min)}</span></div>
      <svg className="oi-chart price-vwap-chart" viewBox="0 0 720 220" role="img" aria-label="Intraday price and VWAP chart from 09:30 to 15:30" preserveAspectRatio="none">
        <line x1="12" x2="708" y1="34" y2="34" className="zero-line" />
        <line x1="12" x2="708" y1="118" y2="118" className="zero-line" />
        <line x1="12" x2="708" y1="202" y2="202" className="zero-line" />
        <polyline points={pathFor('price')} className="price-line" />
        <polyline points={pathFor('vwap')} className="vwap-line" />
        {usable.map((row, i) => {
          const tooltip = `${row.time || 'Time'}\\nPCR: ${number(row.pcr, 2)}\\nPrice: ${number(row.price)}\\nVWAP: ${number(row.vwap)}\\nVWAP-Price Buffer: ${row.price === null || row.vwap === null ? '—' : number(row.price - row.vwap)}\\nVWAP-Price Buffer (%): ${row.buffer === null ? '—' : number(row.buffer, 3) + '%'}`;
          return <g key={`${row.time}-${i}`} className="price-vwap-point">
            <rect x={x(row.minutes) - 4} y="20" width="8" height="185" className="chart-hover-target"><title>{tooltip}</title></rect>
            {row.price !== null && <circle cx={x(row.minutes)} cy={y(row.price)} r="4" className="price-hover-dot"><title>{tooltip}</title></circle>}
            {row.vwap !== null && <circle cx={x(row.minutes)} cy={y(row.vwap)} r="4" className="vwap-hover-dot"><title>{tooltip}</title></circle>}
          </g>;
        })}
        {ticks.map((minutes) => <line key={minutes} x1={x(minutes)} x2={x(minutes)} y1="202" y2="207" className="time-tick" />)}
      </svg>
      <div className="chart-times price-vwap-times">{ticks.map((minutes) => <span key={minutes}>{`${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`}</span>)}</div>
    </div>
    <div className="series-summary price-vwap-summary">
      <div><span>Latest price</span><strong>{number(latest.price)}</strong></div>
      <div><span>Latest VWAP</span><strong>{number(latest.vwap)}</strong></div>
      <div><span>Price − VWAP</span><strong className={gap === null ? '' : gap >= 0 ? 'positive' : 'negative'}>{gap === null ? '—' : number(gap)}</strong></div>
      <div><span>VWAP price buffer</span><strong className={buffer === null ? '' : buffer >= 0 ? 'positive' : 'negative'}>{buffer === null ? '—' : `${number(buffer, 3)}%`}</strong></div>
    </div>
  </>;
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
  return <div className="metric"><span>{label}</span><strong className={tone || ''}>{displayValue(value)}</strong></div>;
}

export default function App() {
  const [index, setIndex] = useState('FINNIFTY');
  const [indexPrices, setIndexPrices] = useState(null);
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
      api('/autotrender/index-price'),
    ]);
    if (results[0].status === 'fulfilled') setIntraday(results[0].value);
    else setError((prev) => prev || `Intraday: ${results[0].reason.message}`);
    if (results[1].status === 'fulfilled') setOi(results[1].value);
    else setError((prev) => prev || `OI graph: ${results[1].reason.message}`);
    if (results[2].status === 'fulfilled') setAuth(results[2].value);
    if (results[3].status === 'fulfilled') setCache(results[3].value);
    if (results[4].status === 'fulfilled') {
      // Price data is independent from the selector: upstream may omit indices
      // from this response, but all five requested indices must remain selectable.
      setIndexPrices(results[4].value);
    }
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
  const calculatedRows = useMemo(() => buildIntradayRows(intradayRows), [intradayRows]);
  const calculatedRowsDescending = useMemo(() => [...calculatedRows].sort((a, b) => {
    const sortTime = (value) => {
      const raw = String(value || '').trim();
      const clock = raw.match(/^(\d{1,2}):(\d{2})/);
      if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
      const parsed = Date.parse(raw);
      return Number.isFinite(parsed) ? parsed : raw;
    };
    const left = sortTime(a.time);
    const right = sortTime(b.time);
    if (typeof left === 'number' && typeof right === 'number') return right - left;
    return String(right).localeCompare(String(left), undefined, { numeric: true });
  }), [calculatedRows]);
  const latest = intradayRows[intradayRows.length - 1] || {};
  const indexPriceKey = Object.keys(indexPrices?.data || {}).find((key) => INDEX_LABELS[key.toLowerCase()] === index);
  const indexPriceRecord = indexPriceKey ? indexPrices.data[indexPriceKey] : null;
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
        <div><div className="eyebrow">READ-ONLY MARKET DATA</div><h1>AutoTrender Monitor</h1><p>Intraday option strength and change-in-open-interest for NIFTY, BANKNIFTY, FINNIFTY and SENSEX.</p></div>
        <div className="heading-meta"><span className={stale ? 'stale' : 'fresh'}>{stale ? 'Check data freshness' : 'Source status'}</span><small>Updated {lastUpdated ? lastUpdated.toLocaleTimeString('en-IN') : '—'}</small></div>
      </section>

      <section className="control-panel">
        <div className="index-switch" aria-label="Select index">
          {DEFAULT_INDICES.map((item) => <button key={item} className={index === item ? 'selected' : ''} onClick={() => setIndex(item)}>{item}</button>)}
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
        <Metric label="LATEST PRICE" value={number(latest.price ?? latest.close ?? latest.ltp ?? indexPriceRecord?.price ?? indexPriceRecord?.ltp ?? indexPriceRecord)} />
        <Metric label="CALL STRENGTH" value={trend.call == null ? '—' : `${number(trend.call)}%`} tone="positive" />
        <Metric label="PUT STRENGTH" value={trend.put == null ? '—' : `${number(trend.put)}%`} tone="negative" />
        <Metric label="OPTION SIGNAL" value={displayValue(trend.signal ?? latest.optionSignal)} />
      </section>

      <section className="price-vwap-section">
        <article className="panel price-vwap-panel">
          <div className="price-vwap-layout">
            <div className="price-vwap-graph-column">
              <div className="panel-header"><div><div className="eyebrow">INTRADAY PRICE ACTION</div><h2>Future Price &amp; VWAP</h2></div><span className="data-count">{calculatedRows.filter((row) => row.price !== null || row.vwap !== null).length} points</span></div>
              <PriceVwapChart rows={calculatedRows} />
            </div>
            <aside className="latest-candle-card">
              <div className="eyebrow">LATEST INTRADAY RECORD</div>
              <h2>{calculatedRowsDescending[0]?.time || 'Latest record'}</h2>
              <div className="latest-record-grid">
                <div><span>Call</span><strong>{number(calculatedRowsDescending[0]?.call)}</strong></div>
                <div><span>Put</span><strong>{number(calculatedRowsDescending[0]?.put)}</strong></div>
                <div><span>Diff</span><strong>{number(calculatedRowsDescending[0]?.diff)}</strong></div>
                <div><span>PCR</span><strong>{number(calculatedRowsDescending[0]?.pcr, 2)}</strong></div>
                <div><span>Price</span><strong>{number(calculatedRowsDescending[0]?.price)}</strong></div>
                <div><span>VWAP</span><strong>{number(calculatedRowsDescending[0]?.vwap)}</strong></div>
                <div><span>VWAP Signal</span><strong className="latest-signal">{calculatedRowsDescending[0]?.vwapSignal || '—'}</strong></div>
                <div><span>Option Signal</span><strong className="latest-signal">{calculatedRowsDescending[0]?.optionSignal || '—'}</strong></div>
                <div><span>Call Strength</span><strong>{calculatedRowsDescending[0]?.callStrength == null ? '—' : number(calculatedRowsDescending[0].callStrength) + '%'}</strong></div>
                <div><span>Put Strength</span><strong>{calculatedRowsDescending[0]?.putStrength == null ? '—' : number(calculatedRowsDescending[0].putStrength) + '%'}</strong></div>
                <div><span>Strength Diff</span><strong className={calculatedRowsDescending[0]?.strengthDiff == null ? '' : calculatedRowsDescending[0].strengthDiff > 0 ? 'positive' : 'negative'}>{calculatedRowsDescending[0]?.strengthDiff == null ? '—' : number(calculatedRowsDescending[0].strengthDiff)}</strong></div>
                <div><span>Buffer (%)</span><strong className={calculatedRowsDescending[0]?.buffer == null ? '' : calculatedRowsDescending[0].buffer > 0 ? 'positive' : 'negative'}>{calculatedRowsDescending[0]?.buffer == null ? '—' : number(calculatedRowsDescending[0].buffer, 3) + '%'}</strong></div>
                <div><span>Enter</span><strong className={'latest-enter ' + (calculatedRowsDescending[0]?.enter || 'wait').toLowerCase()}>{calculatedRowsDescending[0]?.enter || '—'}</strong></div>
              </div>
              {!calculatedRowsDescending.length && <div className="chart-empty">No intraday records returned.</div>}
            </aside>
          </div>
          <div className="panel-footer">Price and VWAP are plotted from the selected index's intraday API records. Card shows the latest record only. No database writes.</div>
        </article>
      </section>

      <section className="content-grid">
        <article className="panel intraday-panel calculated-table-panel">
          <div className="panel-header"><div><div className="eyebrow">INTRADAY SIGNALS</div><h2>Calculated intraday table</h2></div><span className="data-count">{calculatedRows.length} points</span></div>
          <div className="table-scroll calculated-table-scroll"><table className="calculated-table"><thead><tr><th>Time</th><th>Call</th><th>Put</th><th>Diff</th><th>PCR</th><th>Price</th><th>VWAP</th><th>VWAP Signal</th><th>Option Signal</th><th>Call Strength (%)</th><th>Put Strength (%)</th><th>Call-Put Strength Diff</th><th>VWAP-Price Buffer (%)</th><th>Enter</th></tr></thead>
            <tbody>{calculatedRowsDescending.slice(0, 100).map((row) => <tr key={row.key} title={`Time: ${row.time || '—'}\nPCR: ${number(row.pcr, 2)}\nPrice: ${number(row.price)}\nVWAP: ${number(row.vwap)}\nVWAP-Price Buffer: ${row.price === null || row.vwap === null ? '—' : number(row.price - row.vwap)}\nVWAP-Price Buffer (%): ${row.buffer === null ? '—' : number(row.buffer, 3) + '%'}`}>
              <td>{row.time || '—'}</td><td>{number(row.call)}</td><td>{number(row.put)}</td><td>{number(row.diff)}</td><td>{number(row.pcr, 2)}</td><td>{number(row.price)}</td><td>{number(row.vwap)}</td>
              <td><span className="signal-tag">{row.vwapSignal || '—'}</span></td><td><span className="signal-tag">{row.optionSignal || '—'}</span></td>
              <td>{row.callStrength === null ? '—' : number(row.callStrength) + '%'}</td><td>{row.putStrength === null ? '—' : number(row.putStrength) + '%'}</td>
              <td className={row.strengthDiff === null ? '' : row.strengthDiff > 0 ? 'cell-positive' : row.strengthDiff < 0 ? 'cell-negative' : ''}>{row.strengthDiff === null ? '—' : number(row.strengthDiff)}</td>
              <td className={row.buffer === null ? '' : row.buffer > 0 ? 'cell-positive' : row.buffer < 0 ? 'cell-negative' : ''}>{row.buffer === null ? '—' : number(row.buffer, 3)}</td>
              <td><span className={'signal-tag enter-tag ' + row.enter.toLowerCase()}>{row.enter}</span></td>
            </tr>)}
            {!calculatedRows.length && <tr><td colSpan="14" className="empty-cell">{loading ? 'Loading market data…' : 'No intraday records returned.'}</td></tr>}</tbody>
          </table></div>
          <div className="panel-footer">Calculated in memory from the current API response. No MySQL writes. Showing latest {Math.min(calculatedRows.length, 100)} records.</div>
        </article>


      </section>

      <footer className="footer">
        <span>{hasMarketData ? 'Data received from AutoTrender API' : 'Waiting for AutoTrender market data'}</span>
        <span>Cache {cache?.cached ? `${number(cache.cache_age_seconds, 1)}s old` : 'not populated'} · API proxy: local backend</span>
      </footer>
    </main>
  );
}