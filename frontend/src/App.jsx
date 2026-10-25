import React, { useCallback, useEffect, useMemo, useState } from 'react';
import TradeTerminal from './TradeTerminal.jsx';

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

function buildIntradayRows(rows, settings = {}) {
  const strengthThreshold = Math.abs(numericValue(settings.callPutStrengthDiff) ?? 10);
  const bufferThreshold = Math.abs(numericValue(settings.vwapPriceDifferenceBuffer) ?? 0.3);
  const callPcrThreshold = numericValue(settings.callPcr) ?? 1;
  const putPcrThreshold = numericValue(settings.putPcr) ?? 1;

  return rows.map((row, index) => {
    const call = numericValue(row.call);
    const put = numericValue(row.put);
    const total = call !== null && put !== null ? call + put : null;
    const callStrength = total !== null && total > 0 ? (call / total) * 100 : null;
    const putStrength = total !== null && total > 0 ? (put / total) * 100 : null;
    // Positive difference means call strength is higher. Both directions use
    // the absolute configured threshold; VWAP/Option signals distinguish direction.
    const strengthDiff = callStrength !== null && putStrength !== null ? callStrength - putStrength : null;
    const price = numericValue(row.price ?? row.close ?? row.ltp);
    const vwap = numericValue(row.vWap ?? row.vwap);
    const buffer = price !== null && vwap !== null && vwap !== 0 ? ((price - vwap) / vwap) * 100 : null;
    const optionSignal = signalValue(row.optionSignal);
    const vwapSignal = signalValue(row.vWapSignal ?? row.vwapSignal);
    const pcr = numericValue(row.pcr);

    const strengthPass = strengthDiff !== null && strengthDiff > strengthThreshold;
    // The configured buffer is a maximum absolute Price-VWAP percentage gap.
    const bufferPass = buffer !== null && Math.abs(buffer) <= bufferThreshold;
    const bullish = strengthPass && bufferPass && vwapSignal === 'BUY' && optionSignal === 'BUY' && pcr !== null && pcr > callPcrThreshold;
    const bearish = strengthPass && bufferPass && vwapSignal === 'SELL' && optionSignal === 'SELL' && pcr !== null && pcr < putPcrThreshold;
    const enter = bullish ? 'BUY' : bearish ? 'SELL' : 'WAIT';

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
  const [hoveredRow, setHoveredRow] = useState(null);
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
    .map((row) => ({ time: row.time, minutes: parseMinutes(row.time), price: row.price, vwap: row.vwap, pcr: row.pcr, buffer: row.buffer, enter: row.enter, optionSignal: row.optionSignal, vwapSignal: row.vwapSignal }))
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
  const formatHoverNumber = (value, digits = 2) => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: digits });
  const hoveredBuffer = hoveredRow?.price !== null && hoveredRow?.price !== undefined && hoveredRow?.vwap !== null && hoveredRow?.vwap !== undefined && hoveredRow.vwap !== 0 ? ((hoveredRow.price - hoveredRow.vwap) / hoveredRow.vwap) * 100 : null;
  const hoveredGap = hoveredRow?.price !== null && hoveredRow?.price !== undefined && hoveredRow?.vwap !== null && hoveredRow?.vwap !== undefined ? hoveredRow.price - hoveredRow.vwap : null;
  return <>
    <div className="legend price-vwap-legend"><span><i className="legend-price" /> Price</span><span><i className="legend-vwap" /> VWAP</span><span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "#22c55e" }} /> BUY</span><span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "#f87171" }} /> SELL</span></div>
    <div className="chart-wrap price-vwap-wrap" onMouseLeave={() => setHoveredRow(null)}>
      <div className="chart-axis"><span>{number(max)}</span><span>{number((max + min) / 2)}</span><span>{number(min)}</span></div>
      <svg className="oi-chart price-vwap-chart" viewBox="0 0 720 220" role="img" aria-label="Intraday price and VWAP chart from 09:30 to 15:30" preserveAspectRatio="none">
        <line x1="12" x2="708" y1="34" y2="34" className="zero-line" />
        <line x1="12" x2="708" y1="118" y2="118" className="zero-line" />
        <line x1="12" x2="708" y1="202" y2="202" className="zero-line" />
        <polyline points={pathFor('price')} className="price-line" />
        <polyline points={pathFor('vwap')} className="vwap-line" />
        {usable.map((row, i) => <g key={`${row.time}-${i}`} className="price-vwap-point" onMouseEnter={() => setHoveredRow(row)} onFocus={() => setHoveredRow(row)} tabIndex="0">
          <rect x={x(row.minutes) - 7} y="20" width="14" height="185" className="chart-hover-target" aria-label={`Show values for ${row.time}`} />
          {row.price !== null && <circle cx={x(row.minutes)} cy={y(row.price)} r="3.5" className="price-hover-dot" />}
          {row.vwap !== null && <circle cx={x(row.minutes)} cy={y(row.vwap)} r="3.5" className="vwap-hover-dot" />}
          {(row.enter === 'BUY' || row.enter === 'SELL') && (row.price !== null || row.vwap !== null) && <circle cx={x(row.minutes)} cy={y(row.price !== null ? row.price : row.vwap)} r="5" fill={row.enter === 'BUY' ? '#22c55e' : '#f87171'} stroke="#0b1020" strokeWidth="1.5">
            <title>{`${row.time} · ${row.enter} · VWAP ${row.vwapSignal || '—'} · Option ${row.optionSignal || '—'} · Price ${row.price ?? '—'}`}</title>
          </circle>}
        </g>)}
        {ticks.map((minutes) => <line key={minutes} x1={x(minutes)} x2={x(minutes)} y1="202" y2="207" className="time-tick" />)}
      </svg>
      {hoveredRow && <div className="price-vwap-tooltip" style={{ left: `${Math.max(16, Math.min(84, ((x(hoveredRow.minutes) - 12) / 696) * 100))}%` }}>
        <div className="price-vwap-tooltip-heading">{hoveredRow.time || 'Intraday values'}</div>
        <div><span>Price</span><strong>{formatHoverNumber(hoveredRow.price, 2)}</strong></div>
        <div><span>VWAP</span><strong>{formatHoverNumber(hoveredRow.vwap, 2)}</strong></div>
        <div><span>PCR</span><strong>{formatHoverNumber(hoveredRow.pcr, 2)}</strong></div>
        <div><span>Entry Signal</span><strong style={{ color: hoveredRow.enter === 'BUY' ? '#22c55e' : hoveredRow.enter === 'SELL' ? '#f87171' : '#fbbf24' }}>{hoveredRow.enter || '—'}</strong></div>
        <div><span>Price − VWAP</span><strong className={hoveredGap === null ? '' : hoveredGap >= 0 ? 'positive' : 'negative'}>{formatHoverNumber(hoveredGap, 2)}</strong></div>
        <div><span>Buffer</span><strong className={hoveredBuffer === null ? '' : hoveredBuffer >= 0 ? 'positive' : 'negative'}>{hoveredBuffer === null ? '—' : `${formatHoverNumber(hoveredBuffer, 3)}%`}</strong></div>
      </div>}
      <div className="chart-times price-vwap-times">{ticks.map((minutes) => <span key={minutes}>{`${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`}</span>)}</div>
    </div>
    <div className="series-summary price-vwap-summary">
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


const SETTINGS_TABS = [
  ['trading-mode', 'Trading Mode'],
  ['accounts', 'Trading Accounts'],
  ['market-intelligence', 'Market Intelligence'],
  ['option-chain', 'Option Chain'],
  ['symbol-whitelist', 'Symbol Whitelist'],
  ['autotrend', 'AutoTrender'],
  ['websocket', 'WebSocket'],
  ['active-trades', 'Active Trades'],
  ['market-session', 'Market Session'],
  ['cache-viewer', 'Cache Viewer'],
  ['instruments', 'Instruments'],
  ['live-data-cache', 'Live Data & Cache'],
];

const SETTINGS_FIELDS = {
  'market-intelligence': [
    ['targetFallbackPercent', 'Target Fallback %', 'number', '1.00', 'Used when an option-chain target is unavailable.', '0.01'],
    ['swingRefreshSeconds', 'Swing Refresh Interval (seconds)', 'number', '5', 'Refresh interval for Swing Trades.', '1'],
  ],
  'option-chain': [
    ['indicesRefreshSeconds', 'Indices Refresh Interval (seconds)', 'number', '3', 'Refresh interval for indices.', '1'],
    ['optionChainWorkers', 'Option Chain Workers', 'number', '2', 'Number of concurrent option-chain workers.', '1'],
    ['optionChainPostBatchWaitSeconds', 'Option Chain Post-Batch Wait (seconds)', 'number', '10', 'Pause between batches of option-chain requests.', '0'],
    ['optionChainSymbols', 'Option Chain Symbols (comma-separated)', 'text', '', 'Symbols to include in the option-chain universe.', ''],
  ],
  autotrend: [
    ['autoTrenderRefreshSeconds', 'AutoTrender Refresh Interval (seconds)', 'number', '5', 'Refresh interval for AutoTrender data.', '1'],
    ['vwapPriceDifferenceBuffer', 'VWAP-Price Difference Buffer (%)', 'number', '0.3', 'Maximum absolute VWAP/price buffer used by the entry filter.', '0'],
    ['putPcr', 'Put PCR Threshold', 'number', '1', 'Put PCR threshold used by the signal filter.', '0'],
    ['callPcr', 'Call PCR Threshold', 'number', '1', 'Call PCR threshold used by the signal filter.', '0'],
    ['callPutStrengthDiff', 'Call-Put Strength Difference', 'number', '10', 'Minimum call/put strength difference for a signal.', '0'],
    ['autoTrenderEntryStartTime', 'AutoTrender Entry Start Time (IST)', 'time', '10:00', 'New AutoTrender entries are blocked before this time. The start time is inclusive.'],
    ['autoTrenderEntryEndTime', 'AutoTrender Entry End Time (IST)', 'time', '14:00', 'New AutoTrender entries are blocked after this time. The end time is inclusive.'],
  ],
  'active-trades': [
    ['autoExitProfitableTrades', 'Auto Exit Profitable Trades at Market Close', 'boolean', false, 'Enable AutoExitProfitableTradesWorker. When disabled, the worker must not auto-exit profitable trades.'],
    ['targetStopLossStrategy', 'Target & Stop Loss Strategy', 'select', 'M-TTTS', '', ['L-FT-FSL|Fixed Target + Fixed SL', 'M-FT-TSL|Fixed Target + Trailing SL', 'M-TTTS|Target-Triggered Trailing Stop', 'H-PT-TSL|Progressive Target + Trailing SL']],
    ['defaultTargetPercent', 'Default Target', 'number', '10', 'Default target for new manual option trades.', '0.01'],
    ['defaultTargetType', 'Default Target Unit', 'select', 'PERCENTAGE', '', ['POINTS|Points', 'PERCENTAGE|Percentage (%)']],
    ['trailingDistance', 'Trailing SL Distance', 'number', '5', 'Trailing stop-loss distance.', '0.01'],
    ['trailingDistanceType', 'Trailing SL Unit', 'select', 'POINTS', '', ['POINTS|Points', 'PERCENTAGE|Percentage (%)']],
    ['trailingTarget', 'Trailing Target Distance', 'number', '10', 'Distance used to advance the target.', '0.01'],
    ['trailingTargetType', 'Trailing Target Unit', 'select', 'POINTS', '', ['POINTS|Points', 'PERCENTAGE|Percentage (%)']],
    ['intelligenceExitEnabled', 'Intelligence-Based Exit', 'boolean', false, 'Allow market intelligence to trigger an exit.'],
    ['manualExitEnabled', 'Manual Exit', 'boolean', true, 'Allow manual exit of an active trade.'],
    ['manualExitEnabled', 'Manual Exit', 'boolean', true, 'Allow a user to manually exit an active trade.'],
  ],
  'market-session': [
    ['marketStartTime', 'Market Start Time (IST)', 'time', '09:15', 'Start of the market session.'],
    ['marketEndTime', 'Market End Time (IST)', 'time', '15:30', 'End of the market session.'],
  ],
};


function maskSensitive(value, key = '') {
  if (/token|secret|password|authorization|api[_-]?key|cookie|session/i.test(key)) return '[REDACTED]';
  if (Array.isArray(value)) return value.map((item) => maskSensitive(item));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([childKey, childValue]) => [childKey, maskSensitive(childValue, childKey)]));
  return value;
}

function countRecords(payload) {
  if (Array.isArray(payload)) return payload.length;
  if (!payload || typeof payload !== 'object') return payload == null ? 0 : 1;
  for (const key of ['data', 'records', 'rows', 'items', 'intraday', 'result']) if (Array.isArray(payload[key])) return payload[key].length;
  return 1;
}

function CacheViewer() {
  const [snapshot, setSnapshot] = useState(null);
  const [marketEntries, setMarketEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState({});
  const load = useCallback(async () => {
    setLoading(true); setError('');
    const fetchedAt = new Date().toISOString();
    const indexSources = DEFAULT_INDICES.flatMap((symbol) => [
      { key: 'autotrend:' + symbol.toLowerCase() + ':intraday', index: symbol, kind: 'Intraday', path: '/autotrender/indices/' + symbol + '/intraday?mins=5' },
      { key: 'autotrend:' + symbol.toLowerCase() + ':oi-graph', index: symbol, kind: 'Open-interest graph', path: '/autotrender/indices/' + symbol + '/oi-graph' },
    ]);
    const results = await Promise.allSettled([api('/autotrender/cache/content'), ...indexSources.map((source) => api(source.path))]);
    if (results[0].status === 'fulfilled') { setSnapshot(results[0].value); setError(''); }
    else setError(results[0].reason?.message || 'Unable to read backend cache contents.');
    setMarketEntries(indexSources.map((source, i) => {
      const result = results[i + 1];
      const data = result.status === 'fulfilled' ? maskSensitive(result.value) : null;
      return { key: source.key, source: 'AutoTrender API response · ' + source.kind, index: source.index, fetched_at: result.status === 'fulfilled' ? fetchedAt : null, record_count: result.status === 'fulfilled' ? countRecords(data) : 0, data, error: result.status === 'rejected' ? (result.reason?.message || 'Request failed') : '' };
    }));
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);
  const entries = [...((snapshot?.entries || []).map((entry) => ({ ...entry, data: maskSensitive(entry.data), group: 'Backend memory cache' }))), ...marketEntries];
  const rowsFor = (data) => {
    if (Array.isArray(data)) return data;
    if (Array.isArray(data?.data)) return data.data;
    if (Array.isArray(data?.records)) return data.records;
    if (Array.isArray(data?.rows)) return data.rows;
    if (data && typeof data === 'object') return Object.entries(data).map(([key, value]) => ({ key, value }));
    return [];
  };
  return <div className="cache-viewer">
    <div className="cache-viewer-toolbar"><div><h2>Cache Viewer</h2><p className="settings-intro">Read-only backend cache plus live AutoTrender API snapshots for all four indices. API snapshots are inspected here but are not represented as persistent cache entries. Sensitive fields are masked.</p></div><button className="button secondary" type="button" onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh cache & snapshots'}</button></div>
    {error && <div className="error-banner"><strong>Backend cache unavailable</strong><span>{error}</span></div>}
    <div className="settings-grid"><div className="panel"><span>Entries shown</span><h3>{entries.length}</h3></div><div className="panel"><span>Records reported</span><h3>{entries.reduce((sum, entry) => sum + (entry.record_count || 0), 0).toLocaleString('en-IN')}</h3></div><div className="panel"><span>Backend cache</span><h3>{snapshot?.cached ? 'Populated' : 'Empty / unavailable'}</h3></div></div>
    {entries.map((entry) => <details className="cache-entry" key={entry.key} open={Boolean(expanded[entry.key])} onToggle={(event) => { const isOpen = event.currentTarget.open; setExpanded((current) => ({ ...current, [entry.key]: isOpen })); }}>
      <summary><strong>{entry.key}</strong><span>{entry.error ? 'Unavailable' : String(entry.record_count ?? 0) + ' records · fetched ' + (entry.fetched_at || 'timestamp unavailable') + (entry.age_seconds != null ? ' · ' + entry.age_seconds + 's old' : '') + (entry.ttl_seconds != null ? ' · TTL ' + entry.ttl_seconds + 's' : '')}</span></summary>
      <p className="settings-intro">Source: {entry.source || entry.group} · Index: {entry.index || '—'} · Fetched: {entry.fetched_at || 'Never / not reported'}</p>
      {entry.error ? <div className="error-banner"><strong>Source unavailable</strong><span>{entry.error}</span></div> : <><div className="cache-json-wrap"><pre>{JSON.stringify(maskSensitive(entry.data), null, 2)}</pre></div>{rowsFor(entry.data).length > 0 && (() => { const rows = rowsFor(entry.data); const columns = [...new Set(rows.flatMap((row) => row && typeof row === 'object' ? Object.keys(row) : ['value']))].slice(0, 30); return <div className="table-scroll"><table className="kite-data-table"><thead><tr>{columns.map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{rows.slice(0, 500).map((row, rowIndex) => { const object = row && typeof row === 'object' ? row : { value: row }; return <tr key={rowIndex}>{columns.map((key) => { const value = maskSensitive(object[key], key); return <td key={key}>{value && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—')}</td>; })}</tr>; })}</tbody></table>{rows.length > 500 && <p className="settings-intro">Table limited to first 500 records; JSON view contains the full response.</p>}</div>; })()}</>}
    </details>)}
    {snapshot?.last_refresh_error && <div className="error-banner"><strong>Last cache refresh error</strong><span>{snapshot.last_refresh_error}</span></div>}
  </div>;
}
function LiveDataCacheViewer() {
  const [runtime, setRuntime] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [masterStatus, setMasterStatus] = useState(null);
  const [masterError, setMasterError] = useState('');
  const [masterLoading, setMasterLoading] = useState(false);
  const [masterMessage, setMasterMessage] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    try { setRuntime(await api('/kite/market-data/runtime')); setError(''); }
    catch (e) { setError(e.message || 'Unable to load live cache.'); }
    finally { setLoading(false); }
  }, []);
  const loadMasterStatus = useCallback(async () => {
    try { setMasterStatus(await api('/kite/instruments/refresh-status')); setMasterError(''); }
    catch (e) { setMasterError(e.message || 'Unable to load instrument-master status.'); }
  }, []);
  const refreshMaster = useCallback(async () => {
    setMasterLoading(true);
    setMasterMessage('');
    setMasterError('');
    try {
      const result = await api('/kite/instruments/refresh', { method: 'POST' });
      setMasterStatus(result);
      setMasterMessage(`Refresh successful: ${Number(result.stored || 0).toLocaleString('en-IN')} instruments stored.`);
    } catch (e) {
      setMasterMessage('');
      setMasterError(e.message || 'Instrument-master refresh failed. The previous master should remain available.');
      loadMasterStatus();
    } finally {
      setMasterLoading(false);
      loadMasterStatus();
    }
  }, [loadMasterStatus]);
  useEffect(() => {
    load();
    loadMasterStatus();
    const timer = window.setInterval(load, 2000);
    const masterTimer = window.setInterval(loadMasterStatus, 30000);
    return () => { window.clearInterval(timer); window.clearInterval(masterTimer); };
  }, [load, loadMasterStatus]);
  const market = runtime?.market_data || {};
  const cache = runtime?.cache || {};
  const master = masterStatus || {};
  const displayTime = (value) => value ? new Date(value).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST' : 'Not yet recorded';
  return <div className="cache-viewer">
    <div className="cache-viewer-toolbar"><div><h2>Live Market Data &amp; Cache</h2><p className="settings-intro">Subscribed Kite instruments and latest in-memory quote values. This diagnostics view refreshes every 2 seconds while open; quote ticks are not written to MySQL.</p></div><button type="button" className="button secondary" onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh live data'}</button></div>
    <div className="panel cache-entry">
      <div className="cache-viewer-toolbar"><div><h2>Kite Instrument Master</h2><p className="settings-intro">Daily download from Kite and transactional replacement of only the KITE rows in MySQL. Scheduled for 08:00 Asia/Kolkata; failed attempts retry every five minutes.</p></div><button type="button" className="button primary" onClick={refreshMaster} disabled={masterLoading}>{masterLoading ? 'Refreshing master…' : 'Refresh instrument master'}</button></div>
      {masterMessage && <div className="success-banner"><strong>{master.state || 'Refresh'}</strong><span>{masterMessage}</span></div>}
      {masterError && <div className="error-banner"><strong>Instrument-master status / refresh error</strong><span>{masterError}</span></div>}
      <div className="settings-grid">
        <div className="panel"><span>Refresh status</span><h3>{master.state || 'Loading…'}</h3></div>
        <div className="panel"><span>Instruments downloaded</span><h3>{master.downloaded == null ? '—' : Number(master.downloaded).toLocaleString('en-IN')}</h3></div>
        <div className="panel"><span>Instruments stored</span><h3>{master.stored == null ? '—' : Number(master.stored).toLocaleString('en-IN')}</h3></div>
        <div className="panel"><span>Instrument version</span><h3>{master.instrument_version ?? '—'}</h3></div>
      </div>
      <div className="settings-grid">
        <div className="panel"><span>Last attempt</span><p>{displayTime(master.last_attempt_at)}</p></div>
        <div className="panel"><span>Last successful refresh</span><p>{displayTime(master.last_success_at)}</p></div>
        <div className="panel"><span>Next scheduled refresh</span><p>{displayTime(master.next_scheduled_at)}</p></div>
        <div className="panel"><span>Schedule</span><p>{master.schedule_time || '08:00'} · {master.schedule_timezone || 'Asia/Kolkata'}</p></div>
      </div>
      {master.last_error && <div className="error-banner"><strong>Last refresh failure</strong><span>{master.last_error}</span></div>}
      <p className="settings-ui-only-note">A failed download or validation does not replace the previously committed KITE master. Refresh status is runtime-only and resets when the backend restarts.</p>
    </div>
    {error && <div className="error-banner"><strong>Live cache unavailable</strong><span>{error}</span></div>}
    <div className="settings-grid">
      <div className="panel"><span>WebSocket</span><h3>{market.state || 'Unknown'}</h3></div>
      <div className="panel"><span>Subscribed</span><h3>{market.subscription_count ?? 0}</h3></div>
      <div className="panel"><span>Cached quotes</span><h3>{cache.quote_count ?? 0}</h3></div>
      <div className="panel"><span>Open / total trades</span><h3>{cache.open_trade_count ?? 0} / {cache.trade_count ?? 0}</h3></div>
    </div>
    <div className="panel cache-entry"><h3>Subscriptions</h3>
      {(runtime?.subscriptions || []).length ? <div className="table-scroll"><table><thead><tr><th>Token</th><th>Mode</th><th>Trade symbols</th><th>LTP</th><th>Received at</th><th>Details</th></tr></thead><tbody>
        {runtime.subscriptions.map((item) => <tr key={item.instrument_token}><td>{item.instrument_token}</td><td>{item.mode}</td><td>{(item.trade_symbols || []).join(', ') || '—'}</td><td>{number(item.quote?.last_price ?? item.quote?.ltp ?? item.quote?.price)}</td><td>{item.quote?.received_at || 'Waiting for quote'}</td><td><details><summary>Inspect</summary><pre>{JSON.stringify(item.quote || {}, null, 2)}</pre></details></td></tr>)}
      </tbody></table></div> : <p>No instruments are currently subscribed.</p>}
    </div>
    <div className="panel cache-entry"><h3>Quote cache records</h3>
      {(runtime?.quotes || []).length ? <div className="table-scroll"><table><thead><tr><th>Token</th><th>LTP</th><th>Received at</th><th>Full record</th></tr></thead><tbody>
        {runtime.quotes.map((quote) => <tr key={quote.instrument_token}><td>{quote.instrument_token}</td><td>{number(quote.last_price ?? quote.ltp ?? quote.price)}</td><td>{quote.received_at || '—'}</td><td><details><summary>Inspect</summary><pre>{JSON.stringify(quote, null, 2)}</pre></details></td></tr>)}
      </tbody></table></div> : <p>Quote cache is empty until subscribed instruments receive market ticks.</p>}
    </div>
    <div className="panel cache-entry"><h3>Runtime summary</h3><pre>{JSON.stringify({ state: market.state, connected: market.connected, subscriptions: market.subscriptions, quote_count: cache.quote_count, cached_quote_tokens: cache.cached_quote_tokens, open_trade_count: cache.open_trade_count, trade_count: cache.trade_count }, null, 2)}</pre></div>
  </div>;
}


function InstrumentsViewer() {
  const [category, setCategory] = useState('ALL');
  const [letter, setLetter] = useState('ALL');
  const [exchange, setExchange] = useState('ALL');
  const [expiryFilter, setExpiryFilter] = useState('ALL');
  const [optionType, setOptionType] = useState('ALL');
  const [symbolSearch, setSymbolSearch] = useState('');
  const [page, setPage] = useState(0);
  const [payload, setPayload] = useState({ instruments: [], total: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pageSize = 100;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: String(pageSize), offset: String(page * pageSize) });
      if (category !== 'ALL') params.set('category', category);
      if (letter !== 'ALL') params.set('symbol_prefix', letter);
      if (exchange !== 'ALL') params.set('exchange', exchange);
      if (expiryFilter === 'WITH_EXPIRY') params.set('expiry_filter', 'WITH_EXPIRY');
      if (expiryFilter === 'NO_EXPIRY') params.set('expiry_filter', 'NO_EXPIRY');
      if (optionType !== 'ALL') params.set('option_type', optionType);
      if (symbolSearch.trim()) params.set('search', symbolSearch.trim());
      setPayload(await api('/instruments?' + params.toString()));
    } catch (e) {
      setError(e.message || 'Unable to load instruments from MySQL.');
      setPayload({ instruments: [], total: 0 });
    } finally {
      setLoading(false);
    }
  }, [category, letter, exchange, expiryFilter, optionType, symbolSearch, page]);

  useEffect(() => { setPage(0); }, [category, letter, exchange, expiryFilter, optionType, symbolSearch]);
  useEffect(() => { load(); }, [load]);

  const rows = payload.instruments || [];
  const format = (value) => value === null || value === undefined || value === '' ? '—' : typeof value === 'number' ? value.toLocaleString('en-IN', { maximumFractionDigits: 4 }) : String(value);
  const formatStrike = (value) => value === null || value === undefined || value === '' ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const formatTickSize = (value) => value === null || value === undefined || value === '' ? '—' : Number(value).toFixed(2);
  const first = payload.total ? page * pageSize + 1 : 0;
  const last = Math.min((page + 1) * pageSize, payload.total || 0);
  return <div className="instrument-viewer">
    <div className="instrument-viewer-heading">
      <h2>Kite Instrument Master</h2>
    </div>
    <div className="instrument-controls-layout">
    <div className="instrument-category-filters" role="group" aria-label="Instrument category">
      {[
        ['ALL', 'All instruments'], ['EQUITY', 'Equity'], ['INDICES', 'Indices'],
        ['OPTIONS', 'Option contracts'], ['FUTURES', 'Futures'],
      ].map(([value, label]) => <button key={value} type="button" className={'trade-filter-button' + (category === value ? ' active' : '')} onClick={() => setCategory(value)}>{label}</button>)}
    </div>
    <div className="instrument-filter-section">
      <div className="instrument-filter-group">
        <div className="instrument-filter-label">Symbol starts with</div>
        <div className="instrument-letter-buttons" role="group" aria-label="Filter symbols by first letter">
        {['ALL', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'].map((value) => <button key={value} type="button" className={'trade-filter-button' + (letter === value ? ' active' : '')} onClick={() => setLetter(value)}>{value === 'ALL' ? 'All letters' : value}</button>)}
        </div>
      </div>
      <div className="instrument-filter-group">
        <div className="instrument-filter-label">Exchange</div>
        <div className="instrument-exchange-buttons" role="group" aria-label="Filter by exchange">
        {['ALL', 'NSE', 'BSE', 'NFO', 'BFO', 'CDS', 'MCX'].map((value) => <button key={value} type="button" className={'trade-filter-button' + (exchange === value ? ' active' : '')} onClick={() => setExchange(value)}>{value === 'ALL' ? 'All exchanges' : value}</button>)}
        </div>
      </div>
      <div className="instrument-filter-group">
        <div className="instrument-filter-label">Expiry</div>
        <div className="instrument-expiry-buttons" role="group" aria-label="Filter by expiry">
        {[['ALL', 'All instruments'], ['WITH_EXPIRY', 'Has expiry'], ['NO_EXPIRY', 'No expiry']].map(([value, label]) => <button key={value} type="button" className={'trade-filter-button' + (expiryFilter === value ? ' active' : '')} onClick={() => setExpiryFilter(value)}>{label}</button>)}
        </div>
      </div>
      {category === 'OPTIONS' && <div className="instrument-filter-group">
        <div className="instrument-filter-label">Option type</div>
        <div className="instrument-option-type-buttons" role="group" aria-label="Filter by option type">
          {[[ 'ALL', 'All types' ], [ 'CE', 'CE' ], [ 'PE', 'PE' ]].map(([value, label]) => <button key={value} type="button" className={'trade-filter-button' + (optionType === value ? ' active' : '')} onClick={() => setOptionType(value)}>{label}</button>)}
        </div>
      </div>}
    </div>
    </div>
    <div className="instrument-symbol-search">
      <label htmlFor="instrument-symbol-search-input">Search symbol / name</label>
      <input id="instrument-symbol-search-input" type="search" value={symbolSearch} onChange={(event) => setSymbolSearch(event.target.value)} placeholder="Type a symbol or instrument name…" autoComplete="off" />
      {symbolSearch.trim() && <button type="button" className="trade-filter-button" onClick={() => setSymbolSearch('')}>Clear</button>}
    </div>
    {error && <div className="error-banner"><strong>Instrument query failed</strong><span>{error}</span></div>}
    <div className="instrument-results-summary"><strong>{Number(payload.total || 0).toLocaleString('en-IN')} instruments</strong><span>{payload.total ? 'Showing ' + first.toLocaleString('en-IN') + '–' + last.toLocaleString('en-IN') : 'No matching records'}{loading ? ' · Loading…' : ''}</span></div>
    <div className="table-scroll instrument-table-scroll"><table className="kite-data-table instrument-table"><thead><tr><th>Exchange</th><th>Trading Symbol</th><th>Name</th><th>Type</th><th>Segment</th><th>Expiry</th><th>Strike</th><th>Lot Size</th><th>Tick Size</th><th>Token</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.instrument_token}><td>{format(row.exchange)}</td><td><strong>{format(row.tradingsymbol)}</strong></td><td>{format(row.name)}</td><td>{format(row.instrument_type)}</td><td>{format(row.segment)}</td><td>{format(row.expiry)}</td><td>{formatStrike(row.strike)}</td><td>{format(row.lot_size)}</td><td>{formatTickSize(row.tick_size)}</td><td>{row.instrument_token === null || row.instrument_token === undefined || row.instrument_token === '' ? '—' : String(row.instrument_token)}</td></tr>)}
      {!rows.length && <tr><td colSpan="10" className="empty-cell">{loading ? 'Loading instruments…' : 'No instruments match these filters. Check that the Kite instrument master has been refreshed.'}</td></tr>}</tbody>
    </table></div>
    <div className="instrument-pagination"><span>Page {page + 1} of {Math.max(1, Math.ceil((payload.total || 0) / pageSize))}</span><div><button type="button" className="button secondary" disabled={page === 0 || loading} onClick={() => setPage((current) => Math.max(0, current - 1))}>Previous</button><button type="button" className="button secondary" disabled={loading || (page + 1) * pageSize >= (payload.total || 0)} onClick={() => setPage((current) => current + 1)}>Next</button></div></div>
  </div>;
}

function SettingsPanel() {
  const [tab, setTab] = useState('market-intelligence');
  const [settings, setSettings] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const defaults = useMemo(() => {
    const values = {};
    for (const fields of Object.values(SETTINGS_FIELDS)) {
      for (const field of fields) values[field[0]] = field[3];
    }
    Object.assign(values, {
      executionMode: 'PAPER',
      autoTrenderDashboardIndices: { Nifty: true, BankNifty: true, Sensex: true, FinNifty: true },
      optionChainSymbolWhitelist: [],
      optionChainSymbolBlacklist: [],
      showTradeDetailsByDefault: false,
      kiteUnderlying: 'NIFTY',
      kiteStrikeCount: 10,
      kiteAutoLoginEnabled: true,
      kiteSessionResetEnabled: true,
      kiteSessionResetTime: '23:00',
      kiteSessionResetTimezone: 'Asia/Kolkata',
      tradingAccounts: [],
      portfolios: [],
    });
    return values;
  }, []);

  useEffect(() => {
    let active = true;
    api('/settings').then((result) => {
      if (active) setSettings({ ...defaults, ...(result.settings || {}) });
    }).catch((e) => {
      if (active) { setSettings(defaults); setError('Could not load saved settings from MySQL: ' + e.message); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [defaults]);

  const value = (key) => settings[key] === undefined ? defaults[key] : settings[key];
  const change = (key, next) => setSettings((current) => ({ ...current, [key]: next }));
  const addTradingAccount = () => {
    const name = String(value('accountName') || '').trim();
    const brokerAccountId = String(value('brokerAccountId') || '').trim();
    if (!name || !brokerAccountId) { setError('Trading Account requires both Account Name and Broker Account ID.'); return; }
    if (!value('enableApiPlatform') && !value('enableAutoTrender')) { setError('Enable at least one intelligence provider: APIPLATFORM or AutoTrender.'); return; }
    const account = { accountId: 'acct-' + Date.now(), name, broker: value('broker') || 'KITE', brokerAccountId, status: value('accountStatus') || 'ACTIVE', intelligenceProviders: [value('enableApiPlatform') ? 'APIPLATFORM' : null, value('enableAutoTrender') ? 'AutoTrender' : null].filter(Boolean) };
    change('tradingAccounts', [...(value('tradingAccounts') || []), account]);
    setMessage('Trading Account added to pending settings. Click Save settings to persist.');
    setError('');
  };
  const addPortfolio = () => {
    const name = String(value('portfolioName') || '').trim();
    if (!name) { setError('Portfolio Name is required.'); return; }
    change('portfolios', [...(value('portfolios') || []), { portfolioId: 'portfolio-' + Date.now(), accountName: value('accountName') || 'Unassigned account', name, description: value('portfolioDescription') || '', baseCurrency: value('portfolioBaseCurrency') || 'INR', status: value('portfolioStatus') || 'ACTIVE' }]);
    setMessage('Portfolio added to pending settings. Click Save settings to persist.');
    setError('');
  };
  const removeAccount = (accountId) => change('tradingAccounts', (value('tradingAccounts') || []).filter((item) => item.accountId !== accountId));
  const removePortfolio = (portfolioId) => change('portfolios', (value('portfolios') || []).filter((item) => item.portfolioId !== portfolioId));

  const save = async () => {
    setSaving(true); setMessage(''); setError('');
    const start = String(value('autoTrenderEntryStartTime') || '');
    const end = String(value('autoTrenderEntryEndTime') || '');
    const validTime = (candidate) => /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(candidate);
    if (!validTime(start) || !validTime(end)) {
      setError('AutoTrender entry times must use valid 24-hour HH:MM values.');
      setSaving(false);
      return;
    }
    if (start >= end) {
      setError('AutoTrender Entry End Time must be later than the start time.');
      setSaving(false);
      return;
    }
    try {
      const result = await api('/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings }) });
      // Save silently; do not display a success banner.
    } catch (e) { setError(e.message || 'Unable to save settings.'); }
    finally { setSaving(false); }
  };

  const fieldControl = (field) => {
    const [key, label, type, fallback, help, extra] = field;
    const current = value(key);
    if (type === 'boolean') return <label className="settings-toggle" key={key}><input type="checkbox" checked={Boolean(current)} onChange={(e) => change(key, e.target.checked)} /><span><strong>{label}</strong><small>{help}</small></span></label>;
    if (type === 'select') { const options = Array.isArray(extra) ? extra : String(extra || '').split(';').filter(Boolean); return <label className="settings-control" key={key}><span>{label}</span><select value={current} onChange={(e) => change(key, e.target.value)}>{options.map((item) => { const [v, name] = item.split('|'); return <option key={v} value={v}>{name || v}</option>; })}</select>{help && <small>{help}</small>}</label>; }
    return <label className="settings-control" key={key}><span>{label}</span><input type={type} min={type === 'number' ? extra : undefined} step={type === 'number' ? 'any' : undefined} value={current ?? fallback} onChange={(e) => change(key, type === 'number' ? e.target.value : e.target.value)} />{help && <small>{help}</small>}</label>;
  };

  return <section className="settings-page">
    <nav className="settings-tabs" aria-label="Settings sections">{SETTINGS_TABS.map(([id, label]) => <button type="button" key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</nav>
    {error && <div className="error-banner"><strong>Settings issue</strong><span>{error}</span></div>}
    {message && <div className="settings-success">{message}</div>}
    {loading ? <section className="panel settings-section">Loading saved settings…</section> : <section className="panel settings-section">
      {tab === 'trading-mode' && <><h2>Trading Mode</h2><label className="settings-control"><span>Execution Mode</span><select value={value('executionMode')} onChange={(e) => change('executionMode', e.target.value)}><option value="PAPER">PAPER</option><option value="REAL">REAL</option></select><small>PAPER mode is the safe default. This setting does not itself enable live order placement.</small></label><label className="settings-toggle"><input type="checkbox" checked={Boolean(value('showTradeDetailsByDefault'))} onChange={(e) => change('showTradeDetailsByDefault', e.target.checked)} /><span><strong>Show Trade Details by default</strong><small>Controls the default visibility of trade notes and conditions.</small></span></label></>}
      {tab === 'accounts' && <><h2>Trading Accounts</h2><p className="settings-intro">Each account represents one broker account. Configure one broker and enable at least one intelligence provider. Credentials and runtime tokens are not stored in this business record.</p><div className="settings-grid">
        {fieldControl(['accountName','Account Name','text','','Required display name.'])}
        {fieldControl(['broker','Broker','select','KITE','Exactly one broker per account.',['KITE|Kite']])}
        {fieldControl(['brokerAccountId','Broker Account ID','text','','Broker-side account/login identifier.'])}
        {fieldControl(['accountStatus','Account Status','select','ACTIVE','','ACTIVE|ACTIVE;INACTIVE|INACTIVE'])}
        {fieldControl(['enableApiPlatform','Enable APIPLATFORM','boolean',true,'Enable Market Intelligence provider access.'])}
        {fieldControl(['enableAutoTrender','Enable AutoTrender','boolean',true,'Enable AutoTrender provider access.'])}
      </div><p className="settings-intro">The account form is configuration-only in this first UI pass; persistent account CRUD and account listing require the dedicated Trading Account API/model phase.</p><h3>Provider Configuration</h3><div className="provider-config-list"><div><strong>Kite Connect</strong><span>API key, API secret, redirect URL, token-encryption key and access-token handling</span><code>KITE_API_KEY · KITE_API_SECRET · KITE_REDIRECT_URL · KITE_TOKEN_ENCRYPTION_KEY</code></div><div><strong>APIPLATFORM</strong><span>Provider username/password and shared runtime session</span><code>APIPLATFORM_USERNAME · APIPLATFORM_PASSWORD</code></div><div><strong>AutoTrender</strong><span>Provider username/password and authentication status</span><code>AUTOTRENDER_USERNAME · AUTOTRENDER_PASSWORD</code></div></div><p className="settings-intro">Secret values are intentionally not editable or returned by this UI. Configure them in backend/.env; runtime sessions remain owned by the provider/session layer.</p><div className="settings-actions"><button type="button" className="button secondary" onClick={addTradingAccount}>Add Trading Account</button></div><div className="settings-record-list"><h3>Configured Trading Accounts ({(value('tradingAccounts') || []).length})</h3>{(value('tradingAccounts') || []).map((account) => <div key={account.accountId}><span><strong>{account.name}</strong><small>{account.broker} · {account.brokerAccountId} · {account.intelligenceProviders.join(' + ')}</small></span><button type="button" className="button secondary" onClick={() => removeAccount(account.accountId)}>Remove</button></div>)}{!(value('tradingAccounts') || []).length && <p className="settings-intro">No Trading Accounts added yet.</p>}</div><h3>Portfolios</h3><div className="settings-grid">
        {fieldControl(['portfolioName','Portfolio Name','text','Swing Trading','Logical trading mandate within the selected account.'])}
        {fieldControl(['portfolioStatus','Portfolio Status','select','ACTIVE','','ACTIVE|ACTIVE;INACTIVE|INACTIVE'])}
        {fieldControl(['portfolioBaseCurrency','Base Currency','select','INR','','INR|INR'])}
        {fieldControl(['portfolioDescription','Portfolio Description','text','','Optional description.'])}
      </div><div className="settings-actions"><button type="button" className="button secondary" onClick={addPortfolio}>Add Portfolio</button></div><div className="settings-record-list"><h3>Configured Portfolios ({(value('portfolios') || []).length})</h3>{(value('portfolios') || []).map((portfolio) => <div key={portfolio.portfolioId}><span><strong>{portfolio.name}</strong><small>{portfolio.accountName} · {portfolio.baseCurrency} · {portfolio.status}</small></span><button type="button" className="button secondary" onClick={() => removePortfolio(portfolio.portfolioId)}>Remove</button></div>)}{!(value('portfolios') || []).length && <p className="settings-intro">No Portfolios added yet.</p>}</div></>}
      {tab === 'market-intelligence' && <><h2>Market Intelligence</h2><div className="settings-grid">{SETTINGS_FIELDS['market-intelligence'].map(fieldControl)}</div></>}
      {tab === 'option-chain' && <><h2>Option Chain</h2><div className="settings-grid">{SETTINGS_FIELDS['option-chain'].map(fieldControl)}</div><div className="settings-grid">
        {fieldControl(['kiteUnderlying','Default Option Chain Underlying','select','NIFTY','','NIFTY|NIFTY;BANKNIFTY|BANKNIFTY;FINNIFTY|FINNIFTY;SENSEX|SENSEX'])}
        {fieldControl(['kiteStrikeCount','Strikes Each Side','select',10,'Number of strikes on either side.',['5|5','10|10','15|15','20|20','30|30']])}
      </div></>}
      {tab === 'symbol-whitelist' && <><h2>Option Chain Symbol Whitelist / Blacklist</h2><p className="settings-intro">Maintain symbols explicitly allowed or excluded from the option-chain universe. Separate symbols with commas.</p>{fieldControl(['optionChainSymbolWhitelistText','Whitelist Symbols','text',(value('optionChainSymbolWhitelist') || []).join(', '),'Comma-separated allow-list.'])}{fieldControl(['optionChainSymbolBlacklistText','Blacklist Symbols','text',(value('optionChainSymbolBlacklist') || []).join(', '),'Comma-separated block-list.'])}<div className="settings-grid">{fieldControl(['optionChainSymbolLetter','Filter by Initial Letter','select','ALL','','ALL|All letters;A|A;B|B;C|C;D|D;E|E;F|F;G|G;H|H;I|I;J|J;K|K;L|L;M|M;N|N;O|O;P|P;Q|Q;R|R;S|S;T|T;U|U;V|V;W|W;X|X;Y|Y;Z|Z'])}{fieldControl(['optionChainSymbolStatus','Symbol Status Filter','select','ALL','','ALL|All;ACTIVE|Active;INACTIVE|Inactive'])}</div></>}
      {tab === 'autotrend' && <><h2>AutoTrender</h2><div className="settings-grid">{SETTINGS_FIELDS.autotrend.map(fieldControl)}</div><div className="settings-check-grid">{Object.entries(value('autoTrenderDashboardIndices') || {}).map(([name, enabled]) => <label className="settings-toggle" key={name}><input type="checkbox" checked={Boolean(enabled)} onChange={(e) => change('autoTrenderDashboardIndices', { ...(value('autoTrenderDashboardIndices') || {}), [name]: e.target.checked })} /><span><strong>{name}</strong><small>Show on AutoTrender dashboard</small></span></label>)}</div></>}
      {tab === 'websocket' && <><h2>WebSocket</h2><p className="settings-intro">Connection status is runtime information. Subscription configuration is shown separately from secret credentials.</p><div className="settings-grid">{fieldControl(['kiteWebsocketEnabled','Enable Kite WebSocket startup','boolean',true,'Start the live quote feed when a valid Kite session is restored.'])}{fieldControl(['websocketReconnectSeconds','Reconnect Backoff (seconds)','number','5','Configured reconnect delay.','1'])}{fieldControl(['websocketMode','Subscription Mode','select','quote','','ltp|LTP;quote|Quote;full|Full'])}</div><div className="settings-ui-only-note">Live status is displayed in the top header. API keys, API secrets and access tokens must remain in backend environment configuration and encrypted session storage.</div></>}
      {tab === 'active-trades' && <><h2>Active Trades</h2><div className="settings-grid">{SETTINGS_FIELDS['active-trades'].map(fieldControl)}</div><div className="settings-ui-only-note">Global defaults apply to new trades. Existing trades should retain their entry-time strategy snapshot.</div></>}
      {tab === 'market-session' && <><h2>Market Session</h2><div className="settings-grid">{SETTINGS_FIELDS['market-session'].map(fieldControl)}{fieldControl(['kiteAutoLoginEnabled','Automatic Kite Login','boolean',true,'Open the broker login flow when no valid session is available.'])}{fieldControl(['kiteSessionResetEnabled','Daily Kite Session Reset','boolean',true,'Expire the stored session at the configured time.'])}{fieldControl(['kiteSessionResetTime','Session Reset Time','time','23:00','Time uses the configured timezone.'])}{fieldControl(['kiteSessionResetTimezone','Session Reset Timezone','select','Asia/Kolkata','','Asia/Kolkata|Asia/Kolkata;UTC|UTC'])}</div></>}
      {tab === 'cache-viewer' && <CacheViewer />}
      {tab === 'instruments' && <InstrumentsViewer />}
      {tab === 'live-data-cache' && <LiveDataCacheViewer />}
      <div className="settings-actions"><button className="button primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</button></div>
    </section>}
  </section>;
}

function KiteDataTable({ rows, emptyText = 'No records returned.', hiddenColumns = [], holdingsMode = false }) {
  if (!Array.isArray(rows) || rows.length === 0) return <div className="kite-data-empty">{emptyText}</div>;
  const hidden = new Set(hiddenColumns.map((key) => key.toLowerCase()));
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row || {})))].filter((key) => !hidden.has(key.toLowerCase())).slice(0, 18);
  const formatCell = (value) => { if (value === null || value === undefined || value === '') return '—'; if (typeof value === 'object') return JSON.stringify(value); if (typeof value === 'number') return value.toLocaleString('en-IN', { maximumFractionDigits: 2 }); return String(value); };
  return <div className="table-scroll kite-data-scroll"><table className={`kite-data-table ${holdingsMode ? 'holdings-data-table' : ''}`}><thead><tr>{columns.map((key) => <th key={key}>{key.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row, i) => { const pnl = Number(row.pnl ?? row.profit_and_loss ?? row.profit_loss ?? 0); return <tr className={holdingsMode ? (pnl > 0 ? 'holding-gain' : pnl < 0 ? 'holding-loss' : 'holding-neutral') : ''} key={row.order_id || row.tradingsymbol || i}>{columns.map((key) => <td key={key} className={holdingsMode && ['pnl','profit_and_loss','profit_loss'].includes(key.toLowerCase()) ? (pnl > 0 ? 'holding-pnl-positive' : pnl < 0 ? 'holding-pnl-negative' : 'holding-pnl-zero') : ''}>{formatCell(row[key])}</td>)}</tr>; })}</tbody></table></div>;
}
export default function App() {
  const [applicationTab, setApplicationTab] = useState('autotrender');
  const [kiteTab, setKiteTab] = useState('Dashboard');
  const [kiteData, setKiteData] = useState(null);
  const [kiteLoading, setKiteLoading] = useState(false);
  const [kiteError, setKiteError] = useState('');
  const [holdingsLetter, setHoldingsLetter] = useState('ALL');
  const [holdingsProfitFilter, setHoldingsProfitFilter] = useState('ALL');
  const [websocketStatus, setWebsocketStatus] = useState(null);
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
  const [selectedContract, setSelectedContract] = useState(null);
  const [contractLoading, setContractLoading] = useState(false);
  const [contractError, setContractError] = useState('');
  const [autotrendSettings, setAutotrendSettings] = useState({ callPutStrengthDiff: 10, callPcr: 1, putPcr: 1, vwapPriceDifferenceBuffer: 0.3 });

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      api(`/autotrender/indices/${index}/intraday?mins=${mins}`),
      api(`/autotrender/indices/${index}/oi-graph`),
      api('/autotrender/auth/status'),
      api('/autotrender/cache/status'),
      api('/autotrender/index-price'),
      // Reload saved thresholds on every market-data refresh so newly arrived
      // timestamp rows are evaluated against the latest Settings values.
      api('/settings'),
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
    if (results[5].status === 'fulfilled') {
      const saved = results[5].value.settings || {};
      setAutotrendSettings({
        callPutStrengthDiff: saved.callPutStrengthDiff ?? 10,
        callPcr: saved.callPcr ?? 1,
        putPcr: saved.putPcr ?? 1,
        vwapPriceDifferenceBuffer: saved.vwapPriceDifferenceBuffer ?? 0.3,
      });
    } else {
      setError((prev) => prev || 'Unable to refresh AutoTrender thresholds from Settings.');
    }
    setLastUpdated(new Date());
    setLoading(false);
  }, [index, mins]);

  useEffect(() => { refresh(); }, [refresh]);

  const refreshWebsocketStatus = useCallback(async () => {
    try { setWebsocketStatus(await api('/kite/market-data/status')); }
    catch (e) { setWebsocketStatus({ connected: false, state: 'unavailable', error: e.message }); }
  }, []);
  useEffect(() => { refreshWebsocketStatus(); const timer = window.setInterval(refreshWebsocketStatus, 3000); return () => window.clearInterval(timer); }, [refreshWebsocketStatus]);

  const loadKiteTab = useCallback(async () => {
    if (applicationTab !== 'kite') return;
    setKiteLoading(true); setKiteError('');
    try {
      let result;
      if (kiteTab === 'Option Chain') {
        result = await api('/kite/option-chain?underlying=NIFTY&strike_count=10');
        const rows = (result.strikes || []).map((s) => ({ call_symbol: s.CE?.tradingsymbol, call_ltp: s.CE?.last_price, call_volume: s.CE?.volume, call_oi: s.CE?.oi, strike: s.strike, put_oi: s.PE?.oi, put_volume: s.PE?.volume, put_ltp: s.PE?.last_price, put_symbol: s.PE?.tradingsymbol }));
        setKiteData({ title: 'Option Chain', subtitle: (result.underlying || 'NIFTY') + ' · Expiry ' + (result.expiry || '—') + ' · Spot ' + number(result.spot_price), rows });
      } else if (kiteTab === 'Orders') { result = await api('/kite/orders'); setKiteData({ title: 'Orders', subtitle: (result.orders || []).length + ' orders', rows: result.orders || [] });
      } else if (kiteTab === 'Positions') { result = await api('/kite/positions'); setKiteData({ title: 'Positions', subtitle: 'Net and day positions', rows: [...(result.net || []).map((r) => ({ book: 'Net', ...r })), ...(result.day || []).map((r) => ({ book: 'Day', ...r }))] });
      } else if (kiteTab === 'Holdings') { result = await api('/kite/holdings'); setKiteData({ title: 'Holdings', subtitle: (result.holdings || []).length + ' holdings', rows: result.holdings || [] });
      } else if (kiteTab === 'Funds') { result = await api('/kite/funds'); const rows = Object.entries(result || {}).flatMap(([segment, vals]) => vals && typeof vals === 'object' ? Object.entries(vals).map(([metric, value]) => ({ segment, metric, value: typeof value === 'object' ? JSON.stringify(value) : value })) : [{ segment: 'Account', metric: segment, value: vals }]); setKiteData({ title: 'Funds & Margins', subtitle: 'Available balance and margin details', rows });
      } else {
        const all = await Promise.all([api('/kite/orders'), api('/kite/positions'), api('/kite/holdings'), api('/kite/funds')]);
        setKiteData({ title: 'Kite Dashboard', subtitle: 'Live account snapshot', rows: [{ metric: 'Orders today', value: (all[0].orders || []).length }, { metric: 'Net positions', value: (all[1].net || []).length }, { metric: 'Day positions', value: (all[1].day || []).length }, { metric: 'Holdings', value: (all[2].holdings || []).length }, ...Object.entries(all[3] || {}).flatMap(([segment, vals]) => vals && typeof vals === 'object' ? Object.entries(vals).map(([metric, value]) => ({ metric: segment + ' · ' + metric, value: typeof value === 'object' ? JSON.stringify(value) : value })) : [{ metric: segment, value: vals }])] });
      }
    } catch (e) { setKiteData(null); setKiteError(e.message || 'Unable to load Kite data. Check Kite login and backend.'); }
    finally { setKiteLoading(false); }
  }, [applicationTab, kiteTab]);
  useEffect(() => { loadKiteTab(); }, [loadKiteTab]);

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
  const calculatedRows = useMemo(() => buildIntradayRows(intradayRows, autotrendSettings), [intradayRows, autotrendSettings]);
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

  // Contract direction must come from the final configured Intraday Price & VWAP entry decision,
  // not the upstream Option Signal alone. WAIT must never select a directional contract.
  const contractSignal = calculatedRowsDescending[0]?.enter === 'BUY' || calculatedRowsDescending[0]?.enter === 'SELL'
    ? calculatedRowsDescending[0].enter
    : '';

  useEffect(() => {
    let active = true;
    if (!contractSignal) {
      setSelectedContract(null);
      setContractError('Waiting for a BUY or SELL option signal.');
      setContractLoading(false);
      return () => { active = false; };
    }
    setContractLoading(true);
    api('/kite/instruments/select-atm?index=' + encodeURIComponent(index) + '&signal=' + encodeURIComponent(contractSignal))
      .then((result) => { if (active) { setSelectedContract(result); setContractError(''); } })
      .catch((e) => { if (active) { setSelectedContract(null); setContractError(e.message || 'Unable to select an option contract.'); } })
      .finally(() => { if (active) setContractLoading(false); });
    return () => { active = false; };
  }, [index, contractSignal, lastUpdated]);
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
      <nav className="application-tabs" aria-label="Application section">
        <button type="button" className={applicationTab === 'autotrender' ? 'active' : ''} onClick={() => setApplicationTab('autotrender')}>AutoTrender</button>
        <button type="button" className={applicationTab === 'kite' ? 'active' : ''} onClick={() => setApplicationTab('kite')}>Kite</button>
        <button type="button" className={applicationTab === 'trades' ? 'active' : ''} onClick={() => setApplicationTab('trades')}>Trade Terminal</button>
        <button type="button" className={applicationTab === 'settings' ? 'active' : ''} onClick={() => setApplicationTab('settings')}>Settings</button>
      </nav>
        <div className="topbar-right">
          <span className={`status-pill ${auth?.authenticated ? 'online' : 'offline'}`}><i />{auth?.authenticated ? 'AutoTrender connected' : 'AutoTrender not connected'}</span>
          <span className={`status-pill ${websocketStatus?.connected ? 'online' : 'offline'}`} title={'Kite WebSocket: ' + (websocketStatus?.state || 'checking')}><i />WS {websocketStatus?.connected ? 'Connected' : (websocketStatus?.state || 'Checking')}</span>
          <button className="button secondary" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : '↻ Refresh'}</button>
        </div>
      </header>

      {applicationTab === 'settings' ? <SettingsPanel /> : applicationTab === 'trades' ? <TradeTerminal /> : applicationTab === 'autotrender' ? <>
      

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

      <section className="selected-contract-panel panel" aria-live="polite">
        <div className="selected-contract-heading">
          <div><div className="eyebrow">ATM CONTRACT SELECTION</div><h2>Selected option strike</h2></div>
          <span className={'contract-status ' + (selectedContract?.status === 'SELECTED' ? 'ready' : 'waiting')}>{contractLoading ? 'Selecting…' : selectedContract?.status === 'SELECTED' ? 'Ready' : 'Waiting'}</span>
        </div>
        {selectedContract?.status === 'SELECTED' ? <>
          <div className="selected-contract-main">
            <div className="selected-contract-symbol"><strong>{selectedContract.tradingsymbol}</strong><span>{selectedContract.index} · {selectedContract.option_type} · {selectedContract.exchange}</span></div>
            <div className="selected-contract-stat"><span>Spot</span><strong>{number(selectedContract.spot_price)}</strong></div>
            <div className="selected-contract-stat"><span>ATM strike</span><strong>{number(selectedContract.strike, 0)}</strong></div>
            <div className="selected-contract-stat"><span>Expiry</span><strong>{selectedContract.expiry}</strong></div>
            <div className="selected-contract-stat"><span>Lot size</span><strong>{number(selectedContract.lot_size, 0)}</strong></div>
          </div>
          <div className="selected-contract-footnote">{selectedContract.expiry_rule}{selectedContract.used_next_expiry ? ' · Switched to next expiry' : ''} · Signal: {selectedContract.signal}. Paper-trade selection only; no order has been placed.</div>
          {selectedContract.calendar_note && <div className="selected-contract-calendar-note">{selectedContract.calendar_note}</div>}
        </> : <div className="selected-contract-empty">{contractLoading ? 'Checking Kite spot, listed strikes and expiry…' : contractError || 'Contract selection is unavailable.'}</div>}
      </section>

      <section className="price-vwap-section">
        <article className="panel price-vwap-panel">
          <div className="price-vwap-layout">
            <div className="price-vwap-graph-column">
              <div className="panel-header"><div><div className="eyebrow">INTRADAY PRICE ACTION</div><h2>Future Price &amp; VWAP</h2></div><div className="chart-header-right"><div className="chart-enter-card"><span>Enter</span><strong className={'latest-enter ' + (calculatedRowsDescending[0]?.enter || 'wait').toLowerCase()}>{calculatedRowsDescending[0]?.enter || '—'}</strong></div><span className="data-count">{calculatedRows.filter((row) => row.price !== null || row.vwap !== null).length} points</span></div></div>
              <PriceVwapChart rows={calculatedRows} />
            </div>
            <aside className="latest-candle-card">
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
              <td><span className={'signal-tag ' + (row.vwapSignal === 'BUY' ? 'signal-buy' : row.vwapSignal === 'SELL' ? 'signal-sell' : '')}>{row.vwapSignal || '—'}</span></td><td><span className={'signal-tag ' + (row.optionSignal === 'BUY' ? 'signal-buy' : row.optionSignal === 'SELL' ? 'signal-sell' : '')}>{row.optionSignal || '—'}</span></td>
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
      </> : <>
                <nav className="kite-tabs" aria-label="Kite sections">
          {['Dashboard', 'Option Chain', 'Orders', 'Positions', 'Holdings', 'Funds'].map((tab) =>
            <button key={tab} type="button" className={kiteTab === tab ? 'active' : ''} onClick={() => setKiteTab(tab)}>{tab}</button>
          )}
        </nav>
        <section className="kite-data-panel panel">
          <div className="panel-header"><div><div className="eyebrow">KITE TRADING / {kiteTab.toUpperCase()}</div><h2>{kiteData?.title || kiteTab}</h2><div className="kite-data-subtitle">{kiteData?.subtitle || 'Fetching data from Kite API…'}</div></div><button className="button secondary" onClick={loadKiteTab} disabled={kiteLoading}>{kiteLoading ? 'Loading…' : '↻ Refresh data'}</button></div>
          {kiteError && <div className="kite-error"><strong>Unable to load Kite data</strong><span>{kiteError}</span><small>Check Kite login and import the instrument master for Option Chain.</small></div>}
          {kiteTab === 'Holdings' && Array.isArray(kiteData?.rows) && kiteData.rows.length > 0 && (() => {
            const holdings = kiteData.rows;
            const symbolOf = (row) => String(row.tradingsymbol || row.trading_symbol || row.symbol || '');
            const pnlOf = (row) => Number(row.pnl ?? row.profit_and_loss ?? row.profit_loss ?? 0);
            const availableLetters = [...new Set(holdings.map((row) => symbolOf(row).trim().charAt(0).toUpperCase()).filter((letter) => /^[A-Z]$/.test(letter)))].sort();
            const filtered = holdings.filter((row) => {
              const symbol = symbolOf(row).trim().toUpperCase();
              const pnl = pnlOf(row);
              return (holdingsLetter === 'ALL' || symbol.startsWith(holdingsLetter)) &&
                (holdingsProfitFilter === 'ALL' || (holdingsProfitFilter === 'PROFITABLE' && pnl > 0) || (holdingsProfitFilter === 'LOSS' && pnl < 0) || (holdingsProfitFilter === 'BREAKEVEN' && pnl === 0));
            });
            const quantityOf = (row) => Number(row.quantity ?? row.net_quantity ?? 0);
            const priceOf = (row) => Number(row.last_price ?? row.lastPrice ?? row.price ?? row.close_price ?? 0);
            const averageOf = (row) => Number(row.average_price ?? row.averagePrice ?? 0);
            const valueOf = (row) => Number(row.current_value ?? row.market_value ?? priceOf(row) * quantityOf(row));
            const pnlPercentOf = (row) => { const cost = averageOf(row) * quantityOf(row); return cost ? pnlOf(row) / Math.abs(cost) * 100 : 0; };
            const counts = {
              ALL: holdings.length,
              PROFITABLE: holdings.filter((row) => pnlOf(row) > 0).length,
              LOSS: holdings.filter((row) => pnlOf(row) < 0).length,
              BREAKEVEN: holdings.filter((row) => pnlOf(row) === 0).length,
            };
            return <div className="holdings-filters">
              <div className="holdings-filter-heading"><strong>Filter portfolio by symbol</strong><span>{filtered.length} of {holdings.length} holdings</span></div>
              <div className="holdings-alphabet" aria-label="Filter portfolio holdings by first letter">
                <button type="button" className={holdingsLetter === 'ALL' ? 'active' : ''} onClick={() => setHoldingsLetter('ALL')}>All</button>
                {availableLetters.map((letter) => <button key={letter} type="button" className={holdingsLetter === letter ? 'active' : ''} onClick={() => setHoldingsLetter(letter)}>{letter}</button>)}
              </div>

              <div className="holdings-profit-filters" aria-label="Filter holdings by profitability">
                {[['ALL','All holdings'],['PROFITABLE','Profitable'],['LOSS','Loss-making'],['BREAKEVEN','Break-even']].map(([key,label]) => <button type="button" key={key} className={holdingsProfitFilter === key ? 'active' : ''} onClick={() => setHoldingsProfitFilter(key)}>{label}<span>{counts[key]}</span></button>)}
              </div>
              <KiteDataTable rows={filtered} holdingsMode hiddenColumns={['instrument_token', 'isin', 'product', 't1_quantity', 'authorised_quantity', 'authorisation', 'last_quantity', 'authorised_date', 'discrepancy', 'short_quantity', 'used_quantity', 'mtf']} emptyText="No holdings match the selected filters." />
            </div>;
          })()}
          {kiteTab !== 'Holdings' && (kiteLoading && !kiteData ? <div className="kite-data-empty">Loading {kiteTab.toLowerCase()}…</div> : <KiteDataTable rows={kiteData?.rows || []} emptyText={kiteError ? 'Data unavailable until the Kite API request succeeds.' : 'No records returned by Kite.'} />)}
          {kiteTab === 'Holdings' && (!Array.isArray(kiteData?.rows) || kiteData.rows.length === 0) && (kiteLoading && !kiteData ? <div className="kite-data-empty">Loading holdings…</div> : <KiteDataTable rows={[]} emptyText={kiteError ? 'Data unavailable until the Kite API request succeeds.' : 'No holdings returned by Kite.'} />)}
          <div className="panel-footer">Read-only data view. No order placement or modification is performed by these tabs.</div>
        </section>
      </>}
    </main>
  );
}