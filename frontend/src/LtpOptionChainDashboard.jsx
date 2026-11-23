import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './LtpOptionChainDashboard.css';
import LightweightOptionChart from './LightweightOptionChart.jsx';

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

function OptionRow({ row, spot, quotes, instruments, maxima, activeReversal, onToggleReversal, onOpenOrder, onStrikeSelect }) {
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
  // Match the reference option-chain emphasis rule: values above 75% of
  // their column maximum use 900 weight; all other values use 500.
  const percentageWeight = (percentage) => percentage !== null && percentage > 75 ? 900 : 500;
  const reversal = (side, value, quote, instrument) => {
    const rowKey = String(row._id || row.strike);
    const isOpen = activeReversal?.rowKey === rowKey && activeReversal?.side === side;
    const ltp = n(quote && quote.last_price);
    return <span className="ltp-reversal-wrap">
      <button type="button" className={'ltp-reversal ' + tone(value)} aria-label={side + ' reversal ' + (value || 'unavailable') + ', show option LTP'} aria-expanded={isOpen} onClick={() => onToggleReversal(rowKey, side)}>{value || '—'}</button>
      {isOpen && <span className="ltp-reversal-popover" role="status">
        <span>{instrument?.side === 'CE' ? 'CALL' : instrument?.side === 'PE' ? 'PUT' : side} LTP</span>
        <button type="button" className="ltp-reversal-ltp-action" disabled={!instrument} onClick={() => onOpenOrder(instrument, value, side)} aria-label={'Open order for ' + (instrument?.tradingsymbol || side)} title={ltp !== null && ltp > 0 ? 'Open order' : 'LTP unavailable; open order anyway'}>{ltp !== null && ltp > 0 ? fmt(ltp) : 'LTP unavailable'}</button>
      </span>}
    </span>;
  };
  return <tr className={isAtm ? 'ltp-atm-row' : ''}>
    <td className={(callChg > 0 ? 'positive' : callChg < 0 ? 'negative' : '') + rankClass('callOiChg', callChg, true)}><strong className={percentageWeight(callChgPct) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(callChg, 0)}</strong><small className={percentageWeight(callChgPct) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{callChgPct === null ? '' : fmt(callChgPct, 2) + '%'}</small></td>
    <td className={rankClass('callOi', callOi)}><strong className={percentageWeight(callOiShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(callOi, 0)}</strong><small className={percentageWeight(callOiShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{callOiShare === null ? '' : fmt(callOiShare, 2) + '%'}</small></td>
    <td className={rankClass('callVol', callVol)}><strong className={percentageWeight(callVolShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(callVol, 0)}</strong><small className={percentageWeight(callVolShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{callVolShare === null ? '' : fmt(callVolShare, 2) + '%'}</small></td>
    <td>{reversal('CALL', row.calReversal, pq, put)}</td>
    <td className="ltp-strike-cell"><button type="button" className="ltp-strike-order-button" onClick={() => onStrikeSelect?.(row, strike, spot, call, put)} title="Open order ticket for this strike"><strong>{fmt(strike, 0)}</strong>{isAtm && <small>ATM</small>}</button></td>
    <td>{reversal('PUT', row.putReversal, cq, call)}</td>
    <td className={rankClass('putVol', putVol)}><strong className={percentageWeight(putVolShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(putVol, 0)}</strong><small className={percentageWeight(putVolShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{putVolShare === null ? '' : fmt(putVolShare, 2) + '%'}</small></td>
    <td className={rankClass('putOi', putOi)}><strong className={percentageWeight(putOiShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(putOi, 0)}</strong><small className={percentageWeight(putOiShare) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{putOiShare === null ? '' : fmt(putOiShare, 2) + '%'}</small></td>
    <td className={(putChg > 0 ? 'positive' : putChg < 0 ? 'negative' : '') + rankClass('putOiChg', putChg, true)}><strong className={percentageWeight(putChgPct) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{fmt(putChg, 0)}</strong><small className={percentageWeight(putChgPct) === 900 ? "ltp-metric-emphasis" : "ltp-metric-normal"}>{putChgPct === null ? '' : fmt(putChgPct, 2) + '%'}</small></td>
  </tr>;
}


function SwingTradePanel({ onStockSelect }) {
  const [side, setSide] = useState('bullish');
  const [payload, setPayload] = useState({ bullish: [], bearish: [], counts: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  useEffect(() => {
    let active = true, busy = false;
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const next = await request('/apiplatform/swing/live');
        if (!active) return;
        setPayload({ bullish: Array.isArray(next.bullish) ? next.bullish : [], bearish: Array.isArray(next.bearish) ? next.bearish : [], counts: next.counts || {} });
        setLastUpdated(new Date());
        setError('');
      } catch (e) { if (active) setError(e.message || 'Unable to refresh Swing Trade data.'); }
      finally { busy = false; if (active) setLoading(false); }
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const rows = payload[side] || [];
  const number = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const parsed = Number(String(value).replace(/,/g, ''));
    return Number.isFinite(parsed) ? parsed.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : String(value);
  };
  const starField = side === 'bullish' ? 'S_OI_BullishStarRating' : 'R_OI_BearishStarRating';
  const statusField = side === 'bullish' ? 'S_OI_Status' : 'R_OI_Status';
  const oiField = side === 'bullish' ? 'S_OI' : 'R_OI';
  return <section className="ltp-swing-panel">
    <div className="ltp-swing-heading">
      <div><span>APIPLATFORM · IDADDY SWING LIVE</span><h2>Swing Trade Signals</h2></div>
      <div className="ltp-swing-live"><i /> LIVE · 5 SEC</div>
    </div>
    <div className="ltp-swing-toolbar">
      <div className="ltp-swing-tabs" role="tablist" aria-label="Swing signal direction">
        <button type="button" role="tab" aria-selected={side === 'bullish'} className={side === 'bullish' ? 'bullish active' : 'bullish'} onClick={() => setSide('bullish')}>BULLISH <b>{payload.counts.bullish ?? payload.bullish.length}</b></button>
        <button type="button" role="tab" aria-selected={side === 'bearish'} className={side === 'bearish' ? 'bearish active' : 'bearish'} onClick={() => setSide('bearish')}>BEARISH <b>{payload.counts.bearish ?? payload.bearish.length}</b></button>
      </div>
      <div className="ltp-swing-refresh-meta"><span>{lastUpdated ? 'Updated ' + lastUpdated.toLocaleTimeString('en-IN') : (loading ? 'Connecting…' : 'Waiting for data')}</span>{error && <strong title={error}>Refresh issue · retaining last data</strong>}</div>
    </div>
    <div className="ltp-swing-table-wrap">
      <table className="ltp-swing-table">
        <thead><tr><th>SYMBOL</th><th>LOT SIZE</th><th>STATUS [SHIFTING]</th><th>CMP</th><th>PUT H OI REVERSAL</th><th>CALL H OI REVERSAL</th><th>OI</th><th>STAR RATING</th></tr></thead>
        <tbody>{rows.map((row) => {
          const status = String(row[statusField] || '—').toUpperCase();
          return <tr key={row.symbol}>
            <td className="symbol"><button type="button" className="ltp-swing-symbol-link" onClick={() => onStockSelect?.(row, side)} title={`Open ${row.symbol} stock order at its reversal price`}>{row.symbol}</button></td><td>{number(row.lotSize)}</td>
            <td><span className={'ltp-swing-status ' + (status === 'STRONG' ? 'strong' : status === 'WTB' ? 'wtb' : status === 'WTT' ? 'wtt' : '')}>{status}</span></td>
            <td className="cmp">{number(row['Price-S'])}</td><td>{row.S_OI_Reversal ?? '—'}</td><td>{row.R_OI_Reversal ?? '—'}</td><td>{number(row[oiField])}</td><td><span className="ltp-swing-stars">{number(row[starField])}</span></td>
          </tr>;
        })}
        {!rows.length && <tr><td colSpan="8" className="ltp-swing-empty">{loading ? 'Loading live Swing Trade records…' : error ? 'Unable to load Swing Trade records.' : 'No records match the configured filters.'}</td></tr>}</tbody>
      </table>
    </div>
    <div className="ltp-swing-footnote">Filters: Star Rating 0–1 · Bullish status STRONG/WTT · Bearish status STRONG/WTB · Duplicate symbols retain the last qualifying API record · API order preserved.</div>
  </section>;
}

export default function LtpOptionChainDashboard({ initialSymbol = 'NIFTY' }) {
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [symbol, setSymbol] = useState(initialSymbol || 'NIFTY');
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
  const [activeView, setActiveView] = useState('option-chain');
  const chainRequestId = useRef(0);
  const chainFetchBusy = useRef(false);
  const metadataRequestId = useRef(0);
  const [orderDraft, setOrderDraft] = useState(null);
  const [instrumentMode, setInstrumentMode] = useState('CASH');
  const [futuresLoading, setFuturesLoading] = useState(false);
  const [orderDirection, setOrderDirection] = useState('BUY');
  const [orderLots, setOrderLots] = useState('1');
  const [orderSaving, setOrderSaving] = useState(false);
  const [orderError, setOrderError] = useState('');
  const [orderType, setOrderType] = useState('LIMIT');
  const [orderProduct, setOrderProduct] = useState('CNC');
  const [orderPrice, setOrderPrice] = useState('');
  const [orderTriggerPrice, setOrderTriggerPrice] = useState('');
  const [executionMode, setExecutionMode] = useState('PAPER');
  const [activeReversal, setActiveReversal] = useState(null);
  const reversalPopupTimer = useRef(null);

  const openSymbolOptionChain = (nextSymbol) => {
    if (!nextSymbol) return;
    if (nextSymbol !== symbol) {
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
    }
    setActiveView('option-chain');
  };

  useEffect(() => {
    let active = true;
    request('/settings').then((payload) => {
      if (active) setExecutionMode(String(payload.settings?.executionMode || 'PAPER').toUpperCase());
    }).catch(() => { if (active) setExecutionMode('PAPER'); });
    return () => { active = false; };
  }, []);

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

  const atmStrike = useMemo(() => {
    if (spot === null || !rows.length) return null;
    return rows.reduce((best, row) => {
      const strike = n(row.strike);
      if (strike === null) return best;
      return best === null || Math.abs(strike - spot) < Math.abs(best - spot) ? strike : best;
    }, null);
  }, [rows, spot]);
  const atmPut = atmStrike === null ? null : instrumentMap.get(String(atmStrike) + ':PE') || null;
  const atmCall = atmStrike === null ? null : instrumentMap.get(String(atmStrike) + ':CE') || null;
  const quoteForInstrument = (instrument) => instrument ? quotes[String(instrument.instrument_token)] : null;
  const openAtmOrder = (instrument, reversalPrice = null) => {
    if (!instrument || !instrument.tradingsymbol) return;
    if (reversalPopupTimer.current) window.clearTimeout(reversalPopupTimer.current);
    reversalPopupTimer.current = null;
    setActiveReversal(null);
    const preferredPrice = n(reversalPrice);
    setOrderDraft({ ...instrument, instrumentType: 'OPTION', reversalPrice: preferredPrice, underlyingSymbol: symbol, optionContract: instrument, futuresContract: null, cashAvailable: !isIndexUnderlying });
    setInstrumentMode('CASH');
    setOrderDirection('BUY');
    setOrderLots('1');
    setOrderType('LIMIT');
    setOrderProduct('MIS');
    setOrderPrice(preferredPrice !== null ? String(preferredPrice) : '');
    setOrderTriggerPrice(preferredPrice !== null ? String(preferredPrice) : '');
    setOrderError('');
  };
  const openStockTicket = (stockSymbol, reversalValue, lastPriceValue, signalSide = 'bullish', exchange = 'NSE') => {
    const tradingsymbol = String(stockSymbol || '').trim().toUpperCase();
    if (!tradingsymbol) return;
    const reversalPrice = n(reversalValue);
    const lastPrice = n(lastPriceValue);
    if (reversalPopupTimer.current) window.clearTimeout(reversalPopupTimer.current);
    reversalPopupTimer.current = null;
    setActiveReversal(null);
    setOrderDraft({ instrumentType: 'STOCK', tradingsymbol, exchange: String(exchange || 'NSE').toUpperCase(), lot_size: 1, reversalPrice, lastPrice, signalSide, underlyingSymbol: tradingsymbol, optionContract: null, futuresContract: null, cashAvailable: true });
    setInstrumentMode('CASH');
    setOrderDirection('BUY');
    setOrderLots('1');
    setOrderType('LIMIT');
    setOrderProduct('CNC');
    setOrderPrice(reversalPrice !== null ? String(reversalPrice) : '');
    setOrderTriggerPrice(reversalPrice !== null ? String(reversalPrice) : '');
    setOrderError('');
  };
  const openStockOrder = (row, signalSide) => {
    const reversalValue = signalSide === 'bullish' ? row.S_OI_Reversal : row.R_OI_Reversal;
    openStockTicket(row?.symbol, reversalValue, row?.['Price-S'] ?? row?.Price_S ?? row?.price, signalSide, row?.exchange || 'NSE');
  };
  const isIndexUnderlying = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'BANKEX', 'MIDCPNIFTY'].includes(String(symbol).toUpperCase());
  const openUnderlyingOrder = (instrument, reversalValue, side) => {
    if (!isIndexUnderlying) {
      openStockTicket(symbol, reversalValue, data.Price_S, side === 'PUT' ? 'bearish' : 'bullish', 'NSE');
      setOrderDraft((current) => current ? { ...current, optionContract: instrument } : current);
      return;
    }
    openAtmOrder(instrument, reversalValue);
  };
  const openStrikeOrder = (row, strike, liveSpot, call, put) => {
    const isPut = n(strike) !== null && n(liveSpot) !== null && Number(strike) < Number(liveSpot);
    const reversalPrice = n(isPut ? row.putReversal : row.calReversal);
    if (!isIndexUnderlying) {
      openStockTicket(symbol, reversalPrice, data.Price_S, isPut ? 'bearish' : 'bullish', 'NSE');
      setOrderDraft((current) => current ? { ...current, optionContract: isPut ? put : call } : current);
      return;
    }
    const optionSide = isPut ? 'PE' : 'CE';
    const instrument = isPut ? put : call;
    const fallbackInstrument = {
      tradingsymbol: instrument?.tradingsymbol || (symbol + ' ' + expiry + ' ' + strike + ' ' + optionSide),
      exchange: instrument?.exchange || 'NFO',
      instrument_token: instrument?.instrument_token || null,
      strike: n(strike),
      side: optionSide,
      expiry: instrument?.expiry || expiry,
      lot_size: Number(instrument?.lot_size || lotSize),
      instrumentAvailable: Boolean(instrument?.tradingsymbol),
    };
    openAtmOrder(fallbackInstrument, reversalPrice);
  };
  const toggleReversalPopup = (rowKey, side) => {
    if (reversalPopupTimer.current) window.clearTimeout(reversalPopupTimer.current);
    reversalPopupTimer.current = null;
    if (activeReversal?.rowKey === rowKey && activeReversal?.side === side) {
      setActiveReversal(null);
      return;
    }
    const nextActive = { rowKey, side };
    setActiveReversal(nextActive);
    reversalPopupTimer.current = window.setTimeout(() => {
      setActiveReversal((active) => active?.rowKey === rowKey && active?.side === side ? null : active);
      reversalPopupTimer.current = null;
    }, 5000);
  };
  useEffect(() => () => {
    if (reversalPopupTimer.current) window.clearTimeout(reversalPopupTimer.current);
  }, []);

  const submitAtmPaperOrder = async (event) => {
    event.preventDefault();
    if (!orderDraft || orderSaving) return;
    if (orderDraft.instrumentType === 'OPTION' && orderDraft.instrumentAvailable === false) {
      setOrderError('This strike popup is open, but its contract is missing from the Kite instrument master. Refresh the option chain/instrument master before placing the order.');
      return;
    }
    const lots = Number(orderLots);
    const isStock = instrumentMode === 'CASH';
    const activeInstrument = instrumentMode === 'CASH'
      ? (orderDraft.cashAvailable === false ? null : { tradingsymbol: orderDraft.underlyingSymbol || orderDraft.tradingsymbol, exchange: 'NSE', lot_size: 1 })
      : instrumentMode === 'OPTIONS' ? orderDraft.optionContract : orderDraft.futuresContract;
    if (!activeInstrument?.tradingsymbol) {
      setOrderError(instrumentMode === 'CASH' ? 'Cash equity is not available for this underlying. Select OPTIONS or FUTURES.' : instrumentMode === 'OPTIONS' ? 'The selected option contract is unavailable.' : 'Select an available futures contract before placing the order.');
      return;
    }
    const lot = isStock ? 1 : Number(activeInstrument.lot_size || orderDraft.lot_size || lotSize);
    if (!Number.isInteger(lots) || lots < 1 || !Number.isInteger(lot) || lot < 1) {
      setOrderError(isStock ? 'Enter a valid share quantity.' : 'Enter a valid lot quantity.');
      return;
    }
    const quote = isStock ? null : quoteForInstrument(activeInstrument);
    const ltp = isStock ? n(orderDraft.lastPrice) : n(quote && quote.last_price);
    const price = n(orderPrice);
    const triggerPrice = n(orderTriggerPrice);
    if (orderType === 'LIMIT' && (price === null || price <= 0)) { setOrderError('Enter a valid limit price.'); return; }
    if (orderType === 'GTT' && (price === null || price <= 0 || triggerPrice === null || triggerPrice <= 0)) { setOrderError('Enter valid GTT trigger and order prices.'); return; }
    if (orderType === 'GTT' && (ltp === null || ltp <= 0)) { setOrderError('Current market price is unavailable for GTT creation.'); return; }
    setOrderSaving(true);
    setOrderError('');
    try {
      const result = await request('/kite/portfolio/order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: activeInstrument.tradingsymbol,
          instrument_type: instrumentMode === 'CASH' ? 'EQ' : instrumentMode === 'OPTIONS' ? (activeInstrument.side || (activeInstrument.tradingsymbol.endsWith('CE') ? 'CE' : 'PE')) : 'FUT',
          exchange: activeInstrument.exchange || (isStock ? 'NSE' : 'NFO'),
          transaction_type: orderDirection,
          quantity: lots * lot,
          product: isStock ? orderProduct : 'MIS',
          order_type: orderType,
          price: ['LIMIT', 'GTT'].includes(orderType) ? price : null,
          trigger_price: orderType === 'GTT' ? triggerPrice : null,
          last_price: orderType === 'GTT' ? ltp : null,
        }),
      });
      setOrderDraft(null);
      setOrderError('');
      window.alert(result.mode === 'PAPER'
        ? `PAPER ${orderDirection} accepted for ${lots * lot} ${orderDraft.tradingsymbol}; no broker order was placed.`
        : result.status === 'GTT_CREATED'
          ? `GTT created for ${orderDraft.tradingsymbol}. Trigger ID: ${result.trigger_id || '—'}`
          : `Order submitted for ${orderDraft.tradingsymbol}. Order ID: ${result.order_id || '—'}`);
    } catch (e) {
      setOrderError(e.message || 'Unable to create paper trade.');
    } finally {
      setOrderSaving(false);
    }
  };

  // Show the 20 strikes nearest to the live spot, centered on the nearest ATM strike.
  // Keep the provider's full response in `rows` for calculations and heatmap ranking.
  const visibleRows = useMemo(() => {
    const sorted = rows.slice().sort((a, b) => (n(a.strike) || 0) - (n(b.strike) || 0));
    if (sorted.length <= 20) return sorted;
    const referencePrice = spot ?? n(data.Price_S);
    let centerIndex = Math.floor(sorted.length / 2);
    if (referencePrice !== null && referencePrice !== undefined) {
      let nearestDistance = Infinity;
      sorted.forEach((row, index) => {
        const strike = n(row.strike);
        if (strike === null) return;
        const distance = Math.abs(strike - referencePrice);
        if (distance < nearestDistance) {
          nearestDistance = distance;
          centerIndex = index;
        }
      });
    }
    const start = Math.max(0, Math.min(sorted.length - 20, centerIndex - 10));
    return sorted.slice(start, start + 20);
  }, [rows, spot, data.Price_S]);

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
    // Never overlap full-chain requests. If a request is still running at
    // the next poll, skip that tick and let the current response complete.
    if (chainFetchBusy.current) return;
    const requestId = ++chainRequestId.current;
    chainFetchBusy.current = true;
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
      // The backend fetch endpoint owns and replaces the Option Chain subscription
      // group. Do not register the same contracts again as unowned subscriptions.
    } catch (e) {
      if (requestId === chainRequestId.current) setError(e.message || 'Unable to fetch option-chain data.');
    } finally {
      chainFetchBusy.current = false;
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
  useEffect(() => {
    if (!auth.authenticated || !expiry) return undefined;
    loadChain();
    const timer = window.setInterval(() => loadChain(false), 2000);
    return () => window.clearInterval(timer);
  }, [auth.authenticated, expiry, loadChain, priceMode]);
  useEffect(() => {
    if (!instruments.length && !spotToken) return undefined;
    refreshQuotes();
    const timer = window.setInterval(refreshQuotes, 1000);
    return () => window.clearInterval(timer);
  }, [refreshQuotes]);
  useEffect(() => {
    if (!auth.authenticated || !expiry || !instruments.length) return undefined;
    let active = true;
    let busy = false;
    const refreshSubscriptionWindow = async () => {
      if (busy) return;
      busy = true;
      try {
        const query = new URLSearchParams({ symbol, expiry });
        const next = await request('/apiplatform/option-chain/subscriptions/refresh?' + query.toString());
        if (!active || !next.updated || !Array.isArray(next.kite_instruments)) return;
        setInstruments((current) => {
          const currentTokens = current.map((item) => item.instrument_token).join(',');
          const nextTokens = next.kite_instruments.map((item) => item.instrument_token).join(',');
          return currentTokens === nextTokens ? current : next.kite_instruments;
        });
        setSpotToken(next.kite_spot_instrument_token || null);
        setFutureToken(next.kite_future_instrument_token || null);
      } catch (e) {
        // Retain the last valid subscription window if a refresh fails.
        console.warn('Option Chain ATM subscription refresh failed:', e);
      } finally {
        busy = false;
      }
    };
    refreshSubscriptionWindow();
    const timer = window.setInterval(refreshSubscriptionWindow, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [auth.authenticated, symbol, expiry, instruments.length]);

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

  return <section className="ltp-option-chain" data-active-view={activeView}>
    <div className="ltp-overview-row">
    <nav className="ltp-section-tabs" aria-label="Market Intelligence view">
      <button type="button" className={activeView === "option-chain" ? "active" : ""} aria-current={activeView === "option-chain" ? "page" : undefined} onClick={() => setActiveView("option-chain")}>Option Chain</button>
      <button type="button" className={activeView === "swing-trade" ? "active" : ""} aria-current={activeView === "swing-trade" ? "page" : undefined} onClick={() => setActiveView("swing-trade")}>Swing Trade</button>
    </nav>


    {!auth.authenticated && <section className="ltp-auth-banner"><div><strong>APIPLATFORM connection required</strong><small>Uses the credentials already configured in backend/.env. Tokens remain on the backend.</small></div><button type="button" onClick={login} disabled={loggingIn}>{loggingIn ? 'Connecting…' : 'Connect APIPLATFORM'}</button></section>}
    {error && <div className="ltp-error"><strong>Data request issue</strong><span>{error}</span></div>}

    </div>

    {activeView === "option-chain" && <div className="ltp-contract-index-row" aria-label="Option chain contract shortcuts">
      <div className="ltp-atm-contracts" aria-label="ATM put contract">
        <button type="button" className="ltp-atm-contract put" onClick={() => openAtmOrder(atmPut)} disabled={!atmPut}>
          <strong>{atmPut?.tradingsymbol || '—'}</strong>
        </button>
      </div>
      <div className="ltp-index-shortcuts" role="group" aria-label="Quick select index">
        {['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX'].map((item) => (
          <button key={item} type="button" className={symbol === item ? 'active' : ''} aria-pressed={symbol === item} onClick={() => openSymbolOptionChain(item)}>{item}</button>
        ))}
      </div>
      <div className="ltp-atm-contracts" aria-label="ATM call contract">
        <button type="button" className="ltp-atm-contract call" onClick={() => openAtmOrder(atmCall)} disabled={!atmCall}>
          <strong>{atmCall?.tradingsymbol || '—'}</strong>
        </button>
      </div>
      <div className="ltp-controls ltp-contract-controls">
        <label className="ltp-symbol-field"><span>SYMBOL</span><select value={symbol} onChange={(e) => openSymbolOptionChain(e.target.value)}>{symbols.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label className="ltp-expiry-field"><span>EXPIRY</span><select value={expiry} onChange={(e) => { const selected = expiries.find((item) => item.expiry === e.target.value); setExpiry(e.target.value); if (selected && selected.lot_size) setLotSize(selected.lot_size); }}>{expiries.length ? expiries.map((item) => <option key={item.expiry} value={item.expiry}>{item.expiry}</option>) : <option value={expiry}>{expiry}</option>}</select></label>
        <label className="ltp-price-field"><span>PRICE</span><select value={priceMode} onChange={(e) => setPriceMode(e.target.value)}><option value="SPOT">SPOT</option><option value="FUTURE">FUTURE</option></select></label>
        <label className="ltp-lot-size"><span>LOT SIZE</span><input type="number" min="1" value={lotSize} onChange={(e) => setLotSize(Number(e.target.value) || 1)} /></label>
      </div>
    </div>}

    {activeView === "swing-trade" ? <SwingTradePanel onStockSelect={openStockOrder} /> : <>
    <section className="ltp-workspace">
      <article className="ltp-option-panel">
        <div className="ltp-table-scroll"><table className="ltp-option-table">
          <thead><tr><th>CALL OI CHG</th><th>CALL OI</th><th>CALL VOLUME</th><th>CALL REVERSAL</th><th className="ltp-strike-head">STRIKE</th><th>PUT REVERSAL</th><th>PUT VOLUME</th><th>PUT OI</th><th>PUT OI CHG</th></tr></thead>
          <tbody>{visibleRows.map((row, index) => {
            const strike = n(row.strike);
            const nextStrike = index < visibleRows.length - 1 ? n(visibleRows[index + 1].strike) : null;
            const showSpot = underlying !== null && strike !== null && (underlying >= strike && (nextStrike === null || underlying < nextStrike));
            return <React.Fragment key={row._id || row.strike}>
              <OptionRow row={row} spot={spot} quotes={quotes} instruments={instrumentMap} maxima={maxima} activeReversal={activeReversal} onToggleReversal={toggleReversalPopup} onOpenOrder={openUnderlyingOrder} onStrikeSelect={openStrikeOrder} />
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
        <LightweightOptionChart key={symbol + ':' + priceMode} candles={candles} livePrice={underlying} levels={[
          // Keep target numbering in ascending target order: T1 is the nearer target,
          // T2 is the farther/higher target. Supports retain the reference mapping.
          { title: 'T2', value: rs.rreversalNext, color: '#ff6474' },
          { title: 'T1', value: rs.rreversal, color: '#ffad42' },
          { title: 'S1', value: rs.sreversal, color: '#35dc8a' },
          { title: 'S2', value: rs.sreversalBack, color: '#43c8e8' },
          // Max Pain boundaries are separate APIPLATFORM reversalModel values.
          { title: 'Max Pain', value: model.resistanceSL, color: '#ff9b45', lineStyle: 2 },
          { title: 'Max Pain', value: model.supportSL, color: '#ff9b45', lineStyle: 2 },
        ]} />
        <section className="ltp-direction-banner ltp-chart-scenario"><span>MARKET SCENARIO</span><strong>{marketDirection}</strong><small>{model.scenario || ''}</small></section>
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
    </>}

    {orderDraft && <div className="ltp-order-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !orderSaving) setOrderDraft(null); }}>
      <form className="ltp-order-dialog" onSubmit={submitAtmPaperOrder}>
        <div className="ltp-order-heading"><h2>{instrumentMode === 'CASH' ? (orderDraft.underlyingSymbol || orderDraft.tradingsymbol) : instrumentMode === 'OPTIONS' ? (orderDraft.optionContract?.tradingsymbol || 'Option contract unavailable') : (orderDraft.futuresContract?.tradingsymbol || 'Select futures contract')}</h2><button type="button" onClick={() => setOrderDraft(null)} disabled={orderSaving}>×</button></div>
        <div className="ltp-order-instrument-modes" role="group" aria-label="Instrument mode">
          {['CASH', 'OPTIONS', 'FUTURES'].map((mode) => <button key={mode} type="button" className={instrumentMode === mode ? 'active' : ''} onClick={async () => {
            setInstrumentMode(mode); setOrderError(''); setOrderLots('1');
            if (mode === 'CASH') {
              setOrderProduct('CNC');
              const cashPrice = n(orderDraft.reversalPrice);
              setOrderPrice(cashPrice !== null ? String(cashPrice) : '');
              setOrderTriggerPrice(cashPrice !== null ? String(cashPrice) : '');
              return;
            }
            setOrderProduct('MIS');
            if (mode === 'OPTIONS') {
              const optionLtp = n(quoteForInstrument(orderDraft.optionContract)?.last_price);
              setOrderPrice(optionLtp !== null ? String(optionLtp) : '');
              setOrderTriggerPrice(optionLtp !== null ? String(optionLtp) : '');
              return;
            }
            setOrderPrice('');
            setOrderTriggerPrice('');
            setFuturesLoading(true);
            try {
              const query = new URLSearchParams({ underlying: orderDraft.underlyingSymbol || symbol });
              const found = await request('/kite/instruments/derivatives?' + query.toString());
              setOrderDraft((current) => current ? { ...current, futuresContract: found.instruments?.[0] || null } : current);
              if (!found.instruments?.length) setOrderError('No active futures contract found for this underlying in the Kite instrument master.');
            } catch (error) { setOrderError(error.message || 'Unable to load futures contracts.'); }
            finally { setFuturesLoading(false); }
          }}>{mode}</button>)}
        </div>
        <div className="ltp-order-quote"><span>{instrumentMode === 'CASH' ? (orderDraft.cashAvailable ? 'NSE · CASH EQUITY' : 'CASH EQUITY NOT AVAILABLE FOR THIS INDEX') : instrumentMode === 'OPTIONS' ? 'NFO · OPTIONS' : 'NFO · FUTURES'}</span><strong>LTP {fmt(instrumentMode === 'CASH' ? orderDraft.lastPrice : quoteForInstrument(instrumentMode === 'OPTIONS' ? orderDraft.optionContract : orderDraft.futuresContract)?.last_price)}</strong></div>
        {futuresLoading && <div className="ltp-order-error">Loading futures contracts…</div>}
        <div className="ltp-order-reversal-price"><span>{instrumentMode === 'CASH' ? 'REVERSAL BUY PRICE' : 'UNDERLYING REVERSAL REFERENCE'}</span><strong>₹{fmt(orderDraft.reversalPrice)}</strong></div>
        <div className="ltp-order-directions">
          <button type="button" className={orderDirection === 'BUY' ? 'active buy' : 'buy'} onClick={() => setOrderDirection('BUY')}>BUY</button>
          <button type="button" className={orderDirection === 'SELL' ? 'active sell' : 'sell'} onClick={() => setOrderDirection('SELL')}>SELL</button>
        </div>
        <label><span>{instrumentMode === 'CASH' ? 'QUANTITY (SHARES)' : 'LOTS'}</span><input type="number" min="1" step="1" value={orderLots} onChange={(event) => setOrderLots(event.target.value)} required /></label>
        <div className="ltp-order-quantity"><span>QUANTITY</span><strong>{(Number(orderLots) > 0 ? Number(orderLots) : 0) * Number(orderDraft.lot_size || 1)}</strong></div>
        {instrumentMode === 'CASH' && <label><span>PRODUCT</span><select value={orderProduct} onChange={(event) => setOrderProduct(event.target.value)}><option value="CNC">CNC</option><option value="MIS">MIS</option></select></label>}
        <div className="ltp-order-directions ltp-order-types">
          {['MARKET', 'LIMIT', 'GTT'].map((type) => <button key={type} type="button" className={orderType === type ? 'active' : ''} onClick={() => setOrderType(type)}>{type}</button>)}
        </div>
        {orderType === 'LIMIT' && <label><span>LIMIT PRICE (₹)</span><input type="number" value={orderPrice} onChange={(event) => setOrderPrice(event.target.value)} required /></label>}
        {orderType === 'GTT' && <><label><span>TRIGGER PRICE (₹)</span><input type="number" value={orderTriggerPrice} onChange={(event) => setOrderTriggerPrice(event.target.value)} required /></label><label><span>ORDER PRICE (₹)</span><input type="number" value={orderPrice} onChange={(event) => setOrderPrice(event.target.value)} required /></label></>}
        {orderError && <div className="ltp-order-error">{orderError}</div>}
        <div className="ltp-order-actions"><button type="button" onClick={() => setOrderDraft(null)} disabled={orderSaving}>CANCEL</button><button type="submit" disabled={orderSaving}>{orderSaving ? 'SUBMITTING…' : 'PLACE ORDER'}</button></div>
        <div className="ltp-order-mode">TRADING MODE: {executionMode} · {executionMode === 'PAPER' ? 'NO BROKER ORDER' : 'KITE ORDER'}</div>
      </form>
    </div>}
    <footer className="ltp-footer">Market data is provided by APIPLATFORM and Kite Connect. Verify all levels independently before placing trades.</footer>
  </section>;
}
