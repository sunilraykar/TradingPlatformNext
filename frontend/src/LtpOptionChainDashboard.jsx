import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './LtpOptionChainDashboard.css';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const DEFAULT_SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'BANKEX'];

async function request(path, options = {}) {
  const response = await fetch(API + '/api/v1' + path, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Request failed (' + response.status + ')');
  return payload;
}

function n(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(String(value).replace(/,/g, '').replace(/%/g, '').trim());
  return Number.isFinite(parsed) ? parsed : null;
}
function fmt(value, digits = 2) {
  const parsed = n(value);
  return parsed === null ? '—' : parsed.toLocaleString('en-IN', { maximumFractionDigits: digits });
}
function compact(value) {
  const parsed = n(value);
  return parsed === null ? '—' : parsed.toLocaleString('en-IN', { notation: 'compact', maximumFractionDigits: 2 });
}
function parseOhlc(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return null; }
}
function tone(value) {
  const status = String(value || '').toUpperCase();
  if (['WTB', 'BREAK OUT', 'BULLISH'].includes(status)) return 'positive';
  if (['WTT', 'BREAK DOWN', 'BEARISH'].includes(status)) return 'negative';
  return 'neutral';
}
function maxPain(rows) {
  let best = null;
  let lowest = Infinity;
  rows.forEach((candidate) => {
    const strike = n(candidate.strike);
    if (strike === null) return;
    let payout = 0;
    rows.forEach((item) => {
      const k = n(item.strike);
      if (k === null) return;
      payout += (n(item.callOI) || 0) * Math.max(0, strike - k);
      payout += (n(item.putOI) || 0) * Math.max(0, k - strike);
    });
    if (payout < lowest) { lowest = payout; best = strike; }
  });
  return best;
}

function CandleChart({ candles, livePrice, levels, priceMode }) {
  const rows = useMemo(() => (candles || []).map((item) => ({
    time: item.time, open: n(item.open), high: n(item.high), low: n(item.low), close: n(item.close),
  })).filter((item) => [item.open, item.high, item.low, item.close].every((v) => v !== null)), [candles]);

  const W = 1100, H = 650, L = 28, R = 118, T = 28, B = 52;
  const validLevels = levels.map((level) => ({ ...level, numericValue: n(level.value) })).filter((level) => level.numericValue !== null);
  const values = rows.flatMap((r) => [r.high, r.low]).concat(validLevels.map((l) => l.numericValue));
  if (livePrice !== null && livePrice !== undefined) values.push(livePrice);
  if (!values.length) return <div className="ltp-chart-empty"><strong>Intraday chart unavailable</strong><small>No 5-minute candles are available for this instrument and session. The chart will populate when Kite historical data is available.</small></div>;

  const lo = Math.min(...values), hi = Math.max(...values);
  const pad = Math.max((hi - lo) * 0.1, Math.abs(hi) * 0.0008, 1);
  const min = lo - pad, max = hi + pad;
  const plotW = W - L - R, plotH = H - T - B;
  const y = (v) => T + ((max - v) / (max - min)) * plotH;
  const step = plotW / Math.max(rows.length, 1);
  const body = Math.max(2, Math.min(10, step * 0.62));
  const ticks = Array.from({ length: 6 }, (_, i) => min + (max - min) * i / 5);
  const firstTime = rows[0]?.time;
  const lastTime = rows[rows.length - 1]?.time;
  const timeLabel = (value) => {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value).slice(11, 16) : date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  };
  const level = (name) => validLevels.find((item) => item.key === name)?.numericValue;
  const resistanceTop = level("resistanceMaxPain");
  const resistanceBottom = level("eor");
  const supportTop = level("eos");
  const supportBottom = level("supportMaxPain");
  const zones = [
    resistanceTop !== undefined && resistanceBottom !== undefined ? { top: Math.max(resistanceTop, resistanceBottom), bottom: Math.min(resistanceTop, resistanceBottom), cls: "resistance-zone" } : null,
    supportTop !== undefined && supportBottom !== undefined ? { top: Math.max(supportTop, supportBottom), bottom: Math.min(supportTop, supportBottom), cls: "support-zone" } : null,
  ].filter(Boolean);

  return <div className="ltp-candle-chart"><svg viewBox={'0 0 ' + W + ' ' + H} role="img" aria-label={priceMode + ' intraday candlestick chart with support and resistance levels'}>
    {zones.map((zone) => <rect key={zone.cls} x={L} y={y(zone.top)} width={plotW} height={Math.max(1, y(zone.bottom) - y(zone.top))} className={'ltp-chart-zone ' + zone.cls} />)}
    {ticks.map((v, i) => <g key={i}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="ltp-chart-grid" /><text x={W - R + 12} y={y(v) + 5} textAnchor="start" className="ltp-chart-axis">{fmt(v, 0)}</text></g>)}
    {validLevels.map((item) => <g key={item.key || item.label}><line x1={L} x2={W - R} y1={y(item.numericValue)} y2={y(item.numericValue)} className={'ltp-level-line ' + item.tone} /><rect x={W - R + 3} y={y(item.numericValue) - 13} width="112" height="25" rx="6" className={'ltp-level-tag ' + item.tone} /><text x={W - R + 59} y={y(item.numericValue) + 4} textAnchor="middle" className="ltp-level-tag-text">{item.label} {fmt(item.numericValue, 0)}</text></g>)}
    {rows.map((r, i) => {
      const x = L + i * step + step / 2, rising = r.close >= r.open;
      const top = y(Math.max(r.open, r.close)), bottom = y(Math.min(r.open, r.close));
      return <g key={r.time || i} className={rising ? 'ltp-candle up' : 'ltp-candle down'}><line x1={x} x2={x} y1={y(r.high)} y2={y(r.low)} /><rect x={x - body / 2} y={top} width={body} height={Math.max(1.5, bottom - top)} rx="1" /></g>;
    })}
    {livePrice !== null && livePrice !== undefined && <g className="ltp-live-price"><line x1={L} x2={W - R} y1={y(livePrice)} y2={y(livePrice)} /><rect x={W - R + 3} y={y(livePrice) - 13} width="112" height="25" rx="6" /><text x={W - R + 59} y={y(livePrice) + 4} textAnchor="middle">SPOT {fmt(livePrice, 2)}</text></g>}
    {rows.length > 0 && Array.from(new Set([0, Math.floor((rows.length - 1) / 4), Math.floor((rows.length - 1) / 2), Math.floor(3 * (rows.length - 1) / 4), rows.length - 1])).map((index) => <text key={index} x={L + index * step + step / 2} y={H - 18} textAnchor="middle" className="ltp-chart-axis">{timeLabel(rows[index].time)}</text>)}
    {rows.length === 0 && <text x={L + plotW / 2} y={H - 13} textAnchor="middle" className="ltp-chart-axis">Waiting for intraday candles</text>}
    {rows.length > 0 && <text x={L} y={H - 3} className="ltp-chart-axis">{timeLabel(firstTime)}</text>}
    {rows.length > 0 && <text x={W - R} y={H - 3} textAnchor="end" className="ltp-chart-axis">{timeLabel(lastTime)}</text>}
  </svg></div>;
}

function OptionRow({ row, spot, quotes, instruments, maxima }) {
  const strike = n(row.strike);
  const call = instruments.get(String(strike) + ':CE');
  const put = instruments.get(String(strike) + ':PE');
  const cq = call ? quotes[String(call.instrument_token)] : null;
  const pq = put ? quotes[String(put.instrument_token)] : null;
  // APIPLATFORM is the authoritative source for chain OI, volume and OI change.
  // Kite WebSocket OI/volume are in a different unit/session basis and must not
  // overwrite the provider values. Kite is used here only for live option LTP.
  const callOi = n(row.callOI);
  const putOi = n(row.putOI);
  const callVol = n(row.callVol);
  const putVol = n(row.putVol);
  const callChg = n(row.callOIchg);
  const putChg = n(row.putOIchg);
  // Every metric is normalized against the maximum absolute value in its own
  // column across the full provider response. Preserve the sign for OI change.
  const callChgPct = maxima.callOiChg ? callChg / maxima.callOiChg * 100 : null;
  const putChgPct = maxima.putOiChg ? putChg / maxima.putOiChg * 100 : null;
  const callOiShare = maxima.callOi ? callOi / maxima.callOi * 100 : null;
  const putOiShare = maxima.putOi ? putOi / maxima.putOi * 100 : null;
  const callVolShare = maxima.callVol ? callVol / maxima.callVol * 100 : null;
  const putVolShare = maxima.putVol ? putVol / maxima.putVol * 100 : null;
  const isAtm = spot !== null && strike !== null && Math.abs(spot - strike) < 0.01;
  const rankClass = (key, value, absolute = false) => {
    const ranked = maxima.top[key] || [];
    const rankValue = absolute ? Math.abs(value) : value;
    if (!rankValue || !ranked.length) return '';
    if (rankValue === ranked[0]) return ' ltp-rank-' + key + '-first';
    if (rankValue === ranked[1]) return ' ltp-rank-' + key + '-second';
    return '';
  };
  const reversal = (value, quote) => <><span className={'ltp-reversal ' + tone(value)}>{value || '—'}</span><small className="ltp-option-price">LTP {fmt(quote && quote.last_price)}</small></>;
  return <tr className={isAtm ? 'ltp-atm-row' : ''}>
    <td className={(callChg > 0 ? 'positive' : callChg < 0 ? 'negative' : '') + rankClass('callOiChg', callChg, true)}><strong>{fmt(callChg, 0)}</strong><small>{callChgPct === null ? '' : fmt(callChgPct, 2) + '%'}</small></td>
    <td className={rankClass('callOi', callOi)}><strong>{fmt(callOi, 0)}</strong><small>{callOiShare === null ? '' : fmt(callOiShare, 2) + '%'}</small></td>
    <td className={rankClass('callVol', callVol)}><strong>{fmt(callVol, 0)}</strong><small>{callVolShare === null ? '' : fmt(callVolShare, 2) + '%'}</small></td>
    <td>{reversal(row.calReversal, cq)}</td>
    <td className="ltp-strike-cell"><strong>{fmt(strike, 0)}</strong>{isAtm && <small>ATM</small>}</td>
    <td>{reversal(row.putReversal, pq)}</td>
    <td className={rankClass('putVol', putVol)}><strong>{fmt(putVol, 0)}</strong><small>{putVolShare === null ? '' : fmt(putVolShare, 2) + '%'}</small></td>
    <td className={rankClass('putOi', putOi)}><strong>{fmt(putOi, 0)}</strong><small>{putOiShare === null ? '' : fmt(putOiShare, 2) + '%'}</small></td>
    <td className={(putChg > 0 ? 'positive' : putChg < 0 ? 'negative' : '') + rankClass('putOiChg', putChg, true)}><strong>{fmt(putChg, 0)}</strong><small>{putChgPct === null ? '' : fmt(putChgPct, 2) + '%'}</small></td>
  </tr>;
}

export default function LtpOptionChainDashboard() {
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [symbol, setSymbol] = useState('NIFTY');
  const [expiries, setExpiries] = useState([]);
  const [expiry, setExpiry] = useState('09-11-2026');
  const [lotSize, setLotSize] = useState(65);
  const [priceMode, setPriceMode] = useState('SPOT');
  const [auth, setAuth] = useState({ authenticated: false });
  const [loading, setLoading] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [instruments, setInstruments] = useState([]);
  const [spotToken, setSpotToken] = useState(null);
  const [futureToken, setFutureToken] = useState(null);
  const [quotes, setQuotes] = useState({});
  const [candles, setCandles] = useState([]);
  const [feedConnected, setFeedConnected] = useState(false);
  const chainRequestId = useRef(0);
  const metadataRequestId = useRef(0);

  const data = result && result.data ? result.data : {};
  const rows = Array.isArray(data.OptionChain) ? data.OptionChain : [];
  const rs = Array.isArray(data.RS_Status) ? (data.RS_Status[0] || {}) : {};
  const model = data.symbolMarketDirection && data.symbolMarketDirection.reversalModel ? data.symbolMarketDirection.reversalModel : {};
  const spotQuote = spotToken ? quotes[String(spotToken)] : null;
  const futureQuote = futureToken ? quotes[String(futureToken)] : null;
  const spot = n(spotQuote && spotQuote.last_price) ?? n(data.Price_S);
  const underlying = priceMode === 'SPOT' ? spot : (n(futureQuote && futureQuote.last_price) ?? n(data.Price_fno));
  const pain = useMemo(() => maxPain(rows), [rows]);
  const instrumentMap = useMemo(() => new Map(instruments.map((item) => [String(Number(item.strike)) + ':' + item.side, item])), [instruments]);

  const visibleRows = useMemo(() => {
    const ordered = rows.slice().sort((a, b) => (n(a.strike) || 0) - (n(b.strike) || 0));
    if (ordered.length <= 21 || underlying === null) return ordered;
    let center = 0;
    ordered.forEach((item, index) => {
      if (Math.abs((n(item.strike) || 0) - underlying) < Math.abs((n(ordered[center].strike) || 0) - underlying)) center = index;
    });
    return ordered.slice(Math.max(0, center - 11), Math.min(ordered.length, center + 12));
  }, [rows, underlying]);

  const maxima = useMemo(() => {
    const rankValues = (field, absolute = false) => [...new Set(rows
      .map((r) => n(r[field]) || 0)
      .filter((value) => value !== 0)
      .map((value) => absolute ? Math.abs(value) : value))]
      .sort((a, b) => b - a).slice(0, 2);
    return {
      callOiChg: Math.max(0, ...rows.map((r) => Math.abs(n(r.callOIchg) || 0))),
      callOi: Math.max(0, ...rows.map((r) => n(r.callOI) || 0)),
      callVol: Math.max(0, ...rows.map((r) => n(r.callVol) || 0)),
      putVol: Math.max(0, ...rows.map((r) => n(r.putVol) || 0)),
      putOi: Math.max(0, ...rows.map((r) => n(r.putOI) || 0)),
      putOiChg: Math.max(0, ...rows.map((r) => Math.abs(n(r.putOIchg) || 0))),
      top: {
        callOiChg: rankValues('callOIchg', true), callOi: rankValues('callOI'),
        callVol: rankValues('callVol'), putVol: rankValues('putVol'),
        putOi: rankValues('putOI'), putOiChg: rankValues('putOIchg', true),
      },
    };
  }, [rows]);

  const loadMetadata = useCallback(async (selected) => {
    const requestId = ++metadataRequestId.current;
    try {
      const meta = await request('/apiplatform/option-chain/metadata?symbol=' + encodeURIComponent(selected));
      if (requestId !== metadataRequestId.current) return;
      setExpiries(meta.expiries || []);
      if (meta.expiries && meta.expiries.length) {
        const preferred = meta.expiries.find((item) => item.expiry === expiry) || meta.expiries[0];
        setExpiry(preferred.expiry);
        setLotSize(preferred.lot_size || meta.default_lot_size || 65);
      } else {
        setExpiry('');
      }
    } catch (e) {
      if (requestId !== metadataRequestId.current) return;
      setExpiries([]);
      setExpiry('');
      setError(e.message || 'Unable to load expiry information.');
    }
  }, [expiry]);

  const loadChain = useCallback(async (showLoading = true) => {
    if (!expiry) return;
    const requestId = ++chainRequestId.current;
    if (showLoading) setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams({ symbol, expiry, lotSize: String(lotSize) });
      const payload = await request('/apiplatform/option-chain/fetch?' + query.toString());
      if (requestId !== chainRequestId.current) return;
      setResult(payload);
      const mapped = payload.kite_instruments || [];
      setInstruments(mapped);
      setSpotToken(payload.kite_spot_instrument_token || null);
      setFutureToken(payload.kite_future_instrument_token || null);
      const tokens = mapped.map((item) => item.instrument_token);
      if (payload.kite_spot_instrument_token) tokens.push(payload.kite_spot_instrument_token);
      if (payload.kite_future_instrument_token) tokens.push(payload.kite_future_instrument_token);
      if (tokens.length) {
        await request('/kite/market-data/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instrument_tokens: Array.from(new Set(tokens)), mode: 'full' }),
        }).catch(() => null);
      }
    } catch (e) {
      if (requestId === chainRequestId.current) setError(e.message || 'Unable to fetch option-chain data.');
    } finally {
      if (requestId === chainRequestId.current && showLoading) setLoading(false);
    }
  }, [symbol, expiry, lotSize]);

  const refreshQuotes = useCallback(async () => {
    const tokens = instruments.map((item) => item.instrument_token);
    if (spotToken) tokens.push(spotToken);
    if (futureToken) tokens.push(futureToken);
    if (!tokens.length) return;
    const query = new URLSearchParams();
    Array.from(new Set(tokens)).forEach((token) => query.append('instrument_tokens', String(token)));
    try {
      const response = await request('/apiplatform/option-chain/live-quotes?' + query.toString());
      setQuotes(response.quotes || {});
      setFeedConnected(Boolean(response.connected));
    } catch { setFeedConnected(false); }
  }, [instruments, spotToken, futureToken]);

  const login = async () => {
    setLoggingIn(true);
    setError('');
    try {
      const response = await request('/apiplatform/auth/login', { method: 'POST' });
      setAuth({ authenticated: Boolean(response.authenticated), message: response.message });
    } catch (e) { setError(e.message || 'APIPLATFORM login failed.'); }
    finally { setLoggingIn(false); }
  };

  useEffect(() => {
    let active = true;
    Promise.all([
      request('/apiplatform/auth/status').catch(() => ({ authenticated: false })),
      request('/apiplatform/option-chain/symbols').catch(() => ({ symbols: [] })),
    ]).then(([authStatus, symbolResult]) => {
      if (!active) return;
      setAuth(authStatus);
      const list = (symbolResult.symbols || []).map((item) => item.symbol).filter(Boolean);
      if (list.length) {
        const preferred = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX'].filter((value) => list.includes(value));
        setSymbols(preferred.concat(list.filter((value) => !preferred.includes(value))));
      }
    });
    return () => { active = false; };
  }, []);

  useEffect(() => { loadMetadata(symbol); }, [symbol, loadMetadata]);
  useEffect(() => { if (auth.authenticated && expiry) loadChain(); }, [auth.authenticated, expiry, symbol, lotSize, priceMode]);
  useEffect(() => {
    if (!instruments.length && !spotToken) return undefined;
    refreshQuotes();
    const timer = window.setInterval(refreshQuotes, 1000);
    return () => window.clearInterval(timer);
  }, [refreshQuotes]);
  useEffect(() => {
    if (!auth.authenticated || !expiry) return undefined;
    const timer = window.setInterval(() => loadChain(false), 15000);
    return () => window.clearInterval(timer);
  }, [auth.authenticated, loadChain, expiry]);

  useEffect(() => {
    if (!auth.authenticated || !expiry) return undefined;
    let active = true;
    const refreshCandles = () => {
      const query = new URLSearchParams({ symbol, expiry, price_mode: priceMode });
      request('/apiplatform/option-chain/candles?' + query.toString())
        .then((chart) => { if (active) setCandles(chart.candles || []); })
        .catch(() => { if (active) setCandles([]); });
    };
    refreshCandles();
    const timer = window.setInterval(refreshCandles, 60000);
    return () => { active = false; window.clearInterval(timer); };
  }, [auth.authenticated, symbol, expiry, priceMode]);

  // Reference-chart levels are sourced from the current RS_Status record.
  // The reversalModel stop-loss values provide the upper/lower Max Pain lines.
  // Do not substitute locally calculated option-chain max pain: it is a different metric.
  const chartLevels = [
    { key: 'resistanceMaxPain', label: 'Max Pain', value: model.resistanceSL, tone: 'resistance-risk' },
    { key: 'eorPlusOne', label: 'EOR+1', value: rs.rreversalNext, tone: 'resistance' },
    { key: 'eor', label: 'EOR', value: rs.rreversal, tone: 'resistance' },
    { key: 'eos', label: 'EOS', value: rs.sreversal, tone: 'support' },
    { key: 'eosMinusOne', label: 'EOS-1', value: rs.sreversalBack, tone: 'support' },
    { key: 'supportMaxPain', label: 'Max Pain', value: model.supportSL, tone: 'support-risk' },
  ].sort((a, b) => (n(b.value) ?? -Infinity) - (n(a.value) ?? -Infinity));
  const marketDirection = data.symbolMarketDirection && data.symbolMarketDirection.marketDirection ? data.symbolMarketDirection.marketDirection : 'Market direction unavailable';
  const fetchedAt = data.FetchTime || (data.symbolMarketDirection && data.symbolMarketDirection.actualFetchTime) || '—';
  const selectedQuote = priceMode === 'SPOT' ? spotQuote : futureQuote;
  const change = selectedQuote && selectedQuote.ohlc && n(selectedQuote.ohlc.close) && n(selectedQuote.last_price)
    ? (n(selectedQuote.last_price) - n(selectedQuote.ohlc.close)) / n(selectedQuote.ohlc.close) * 100 : null;
  const total = (field) => visibleRows.reduce((sum, row) => sum + (n(row[field]) || 0), 0);

  return <section className="ltp-option-chain">
    <section className="ltp-option-toolbar">
      <div className="ltp-brand"><span className="ltp-brand-icon">↗</span><div><strong>OPTION CHAIN DATA</strong><small>MARKET INTELLIGENCE TERMINAL</small></div></div>
      <div className="ltp-controls">
        <label><span>SYMBOL</span><select value={symbol} onChange={(e) => {
          const nextSymbol = e.target.value;
          if (nextSymbol === symbol) return;
          chainRequestId.current += 1;
          setLoading(false);
          setError('');
          setResult(null);
          setInstruments([]);
          setSpotToken(null);
          setFutureToken(null);
          setQuotes({});
          setCandles([]);
          setExpiry('');
          setSymbol(nextSymbol);
        }}>{symbols.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label><span>EXPIRY</span><select value={expiry} onChange={(e) => { const selected = expiries.find((item) => item.expiry === e.target.value); setExpiry(e.target.value); if (selected && selected.lot_size) setLotSize(selected.lot_size); }}>{expiries.length ? expiries.map((item) => <option key={item.expiry} value={item.expiry}>{item.expiry}</option>) : <option value={expiry}>{expiry}</option>}</select></label>
        <label><span>PRICE</span><select value={priceMode} onChange={(e) => setPriceMode(e.target.value)}><option value="SPOT">SPOT</option><option value="FUTURE">FUTURE</option></select></label>
        <label className="ltp-lot-size"><span>LOT SIZE</span><input type="number" min="1" value={lotSize} onChange={(e) => setLotSize(Number(e.target.value) || 1)} /></label>
        <button type="button" className="ltp-refresh" onClick={() => loadChain()} disabled={loading || !auth.authenticated}>{loading ? 'Loading…' : '↻ Refresh'}</button>
      </div>
      <div className={'ltp-feed-status ' + (feedConnected ? 'connected' : '')}><i />{feedConnected ? 'KITE LIVE' : 'KITE FEED'}</div>
    </section>

    {!auth.authenticated && <section className="ltp-auth-banner"><div><strong>APIPLATFORM connection required</strong><small>Uses the credentials already configured in backend/.env. Tokens remain on the backend.</small></div><button type="button" onClick={login} disabled={loggingIn}>{loggingIn ? 'Connecting…' : 'Connect APIPLATFORM'}</button></section>}
    {error && <div className="ltp-error"><strong>Data request issue</strong><span>{error}</span></div>}

    <section className="ltp-direction-banner"><span>MARKET SCENARIO</span><strong>{marketDirection}</strong><small>{model.scenario || ''}</small></section>

    <section className="ltp-market-strip">
      <div className="ltp-market-price"><span>{priceMode} PRICE</span><strong>{fmt(underlying)}</strong><small className={change > 0 ? 'positive' : change < 0 ? 'negative' : ''}>{change === null ? 'Kite WebSocket quote' : (change > 0 ? '+' : '') + fmt(change) + '%'}</small></div>
      <div><span>RISKY RESISTANCE</span><strong>{model.riskyResistance || '—'}</strong></div>
      <div><span>MODERATE RESISTANCE</span><strong>{fmt(model.moderateResistance)}</strong></div>
      <div><span>RESISTANCE SL</span><strong>{fmt(model.resistanceSL)}</strong></div>
      <div><span>RISKY SUPPORT</span><strong>{fmt(model.riskySupport)}</strong></div>
      <div><span>MODERATE SUPPORT</span><strong>{fmt(model.moderateSupport)}</strong></div>
      <div><span>SUPPORT SL</span><strong>{fmt(model.supportSL)}</strong></div>
    </section>

    <section className="ltp-workspace">
      <article className="ltp-option-panel">
        <div className="ltp-chain-count"><span>{visibleRows.length} strikes shown · {rows.length} returned</span></div>
        <div className="ltp-table-scroll"><table className="ltp-option-table">
          <thead><tr><th>CALL OI CHG</th><th>CALL OI</th><th>CALL VOLUME</th><th>CALL REVERSAL</th><th className="ltp-strike-head">STRIKE</th><th>PUT REVERSAL</th><th>PUT VOLUME</th><th>PUT OI</th><th>PUT OI CHG</th></tr></thead>
          <tbody>{visibleRows.map((row, index) => {
            const strike = n(row.strike);
            const nextStrike = index < visibleRows.length - 1 ? n(visibleRows[index + 1].strike) : null;
            const showSpot = underlying !== null && strike !== null && (underlying >= strike && (nextStrike === null || underlying < nextStrike));
            return <React.Fragment key={row._id || row.strike}>
              <OptionRow row={row} spot={underlying} quotes={quotes} instruments={instrumentMap} maxima={maxima} />
              {showSpot && <tr className="ltp-spot-marker-row">
                <td colSpan="4"><div className="ltp-rs-indicators ltp-rs-call">
                  <span className="ltp-rs-chip"><b>{rs.R_OI_Status || '—'}</b><strong>{fmt(rs.R_OI_ShiftingStrike_per, 2)}%</strong><small>OI · {rs.R_OI_Strike || '—'}{rs.R_OI_Shifting ? ' → ' + (rs.R_OI_ShiftingStrike || '—') : ''}</small></span>
                  <span className="ltp-rs-chip"><b>{rs.R_Vol_Status || '—'}</b><strong>{fmt(rs.R_Vol_ShiftingStrike_per, 2)}%</strong><small>VOL · {rs.R_Vol_Strike || '—'}{rs.R_Vol_Shifting ? ' → ' + (rs.R_Vol_ShiftingStrike || '—') : ''}</small></span>
                </div></td>
                <td className="ltp-spot-marker-cell"><strong>SPOT</strong><b>{fmt(underlying)}</b></td>
                <td colSpan="4"><div className="ltp-rs-indicators ltp-rs-put">
                  <span className="ltp-rs-chip"><b>{rs.S_Vol_Status || '—'}</b><strong>{fmt(rs.S_Vol_ShiftingStrike_per, 2)}%</strong><small>VOL · {rs.S_Vol_Strike || '—'}{rs.S_Vol_Shifting ? ' → ' + (rs.S_Vol_ShiftingStrike || '—') : ''}</small></span>
                  <span className="ltp-rs-chip"><b>{rs.S_OI_Status || '—'}</b><strong>{fmt(rs.S_OI_ShiftingStrike_per, 2)}%</strong><small>OI · {rs.S_OI_Strike || '—'}{rs.S_OI_Shifting ? ' → ' + (rs.S_OI_ShiftingStrike || '—') : ''}</small></span>
                </div></td>
              </tr>}
            </React.Fragment>;
          })}
            {!visibleRows.length && <tr><td colSpan="9" className="ltp-empty-cell">{loading ? 'Loading option-chain data…' : 'No option-chain records returned for this expiry.'}</td></tr>}
          </tbody>
          {visibleRows.length > 0 && <tfoot>
            <tr>
              <td>CALL OI CHG<br />{fmt(total('callOIchg'), 0)}</td>
              <td>CALL OI<br />{compact(total('callOI'))}</td>
              <td>CALL VOL<br />{compact(total('callVol'))}</td>
              <td>CALL REVERSAL</td>
              <td className="ltp-strike-head">TOTAL</td>
              <td>PUT REVERSAL</td>
              <td>PUT VOL<br />{compact(total('putVol'))}</td>
              <td>PUT OI<br />{compact(total('putOI'))}</td>
              <td>PUT OI CHG<br />{fmt(total('putOIchg'), 0)}</td>
            </tr>
            <tr className="ltp-total-summary-row">
              <td colSpan="4">PE − CE OI CHANGE<br /><strong>{fmt(total('putOIchg') - total('callOIchg'), 0)}</strong></td>
              <td className="ltp-strike-head">VISIBLE STRIKES<br />{visibleRows.length}</td>
              <td colSpan="4">TOTAL OI<br /><strong>{compact(total('callOI') + total('putOI'))}</strong></td>
            </tr>
          </tfoot>}
        </table></div>
        <div className="ltp-table-legend"><span><i className="atm" /> ATM strike</span><span><i className="max" /> Highest OI / volume</span><span className="positive">● Positive</span><span className="negative">● Negative</span></div>
      </article>

      <aside className="ltp-chart-panel">
        <div className="ltp-panel-heading"><div><span>INTRADAY PRICE ACTION</span><h2>{symbol} · {priceMode}</h2></div><small>5-minute candles</small></div>
        <div className="ltp-chart-quote"><strong>{fmt(underlying)}</strong><span className={feedConnected ? 'positive' : 'neutral'}>{feedConnected ? '● Live from Kite WebSocket' : '○ Waiting for Kite ticks'}</span></div>
        <CandleChart candles={candles} livePrice={underlying} levels={chartLevels} priceMode={priceMode} />
        <div className="ltp-chart-footer"><span>Source: Kite WebSocket + today's Kite candles</span><span>Fetched {fetchedAt}</span></div>
        <section className="ltp-intelligence-groups" aria-label="Resistance and support shifting">
          <article className="ltp-intelligence-group resistance-group">
            <h3>RESISTANCE</h3>
            <div className="ltp-intelligence-status"><span>STATUS</span><strong className={tone(rs.Resistance_Status)}>{rs.Resistance_Status || '—'}</strong></div>
            <div><span>VOLUME SHIFT</span><strong>{rs.R_Vol_ShiftingStrike || '—'}</strong><small>{rs.R_Vol_Status || '—'}</small></div>
            <div><span>OI SHIFT</span><strong>{rs.R_OI_ShiftingStrike || '—'}</strong><small>{rs.R_OI_Status || '—'}</small></div>
            <div><span>RESISTANCE SHIFT</span><strong>{rs.R_ShiftingStrike || '—'}</strong></div>
          </article>
          <article className="ltp-intelligence-group support-group">
            <h3>SUPPORT</h3>
            <div className="ltp-intelligence-status"><span>STATUS</span><strong className={tone(rs.Support_Status)}>{rs.Support_Status || '—'}</strong></div>
            <div><span>VOLUME SHIFT</span><strong>{rs.S_Vol_ShiftingStrike || '—'}</strong><small>{rs.S_Vol_Status || '—'}</small></div>
            <div><span>OI SHIFT</span><strong>{rs.S_OI_ShiftingStrike || '—'}</strong><small>{rs.S_OI_Status || '—'}</small></div>
            <div><span>SUPPORT SHIFT</span><strong>{rs.S_ShiftingStrike || '—'}</strong></div>
          </article>
        </section>
      </aside>
    </section>

    <footer className="ltp-footer">Market data is provided by APIPLATFORM and Kite Connect. Verify all levels independently before placing trades.</footer>
  </section>;
}
