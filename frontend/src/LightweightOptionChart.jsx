import React, { useEffect, useMemo, useRef } from 'react';
import { CandlestickSeries, createChart } from 'lightweight-charts';

function numeric(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function toTimestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.floor(value);
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export default function LightweightOptionChart({ candles = [], livePrice, levels = [] }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const priceLinesRef = useRef([]);
  const normalizedCandles = useMemo(() => {
    const byTime = new Map();
    candles.forEach((item) => {
      const time = toTimestamp(item.time);
      const open = numeric(item.open), high = numeric(item.high);
      const low = numeric(item.low), close = numeric(item.close);
      if (time === null || [open, high, low, close].some((value) => value === null)) return;
      byTime.set(time, { time, open, high, low, close });
    });
    return [...byTime.values()].sort((a, b) => a.time - b.time);
  }, [candles]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: '#142438' },
        textColor: '#e8f0fa',
        fontFamily: 'Inter, Segoe UI, sans-serif',
        fontSize: 12,
      },
      grid: {
        vertLines: { color: '#263b52' },
        horzLines: { color: '#30465e' },
      },
      rightPriceScale: {
        visible: true,
        borderColor: '#52677e',
        textColor: '#e8f0fa',
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: '#52677e',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        barSpacing: 8,
        minBarSpacing: 3,
      },
      crosshair: { vertLine: { color: '#8ca3bd', labelBackgroundColor: '#344b63' }, horzLine: { color: '#8ca3bd', labelBackgroundColor: '#344b63' } },
      handleScroll: true,
      handleScale: true,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#26c281',
      downColor: '#ef5350',
      borderUpColor: '#26c281',
      borderDownColor: '#ef5350',
      wickUpColor: '#26c281',
      wickDownColor: '#ef5350',
      lastValueVisible: true,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    candleSeriesRef.current = series;
    const observer = new ResizeObserver(() => {
      if (containerRef.current) chart.resize(containerRef.current.clientWidth, containerRef.current.clientHeight);
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      priceLinesRef.current = [];
      candleSeriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    series.setData(normalizedCandles);
    if (normalizedCandles.length) chartRef.current?.timeScale().fitContent();
  }, [normalizedCandles]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    priceLinesRef.current.forEach((line) => series.removePriceLine(line));
    priceLinesRef.current = [];
    const definitions = [
      ...levels.map((level) => ({ ...level, value: numeric(level.value) })),
      { title: 'SPOT', value: numeric(livePrice), color: '#ffdf4d', lineStyle: 0, lineWidth: 2 },
    ].filter((level) => level.value !== null && level.value !== undefined);
    definitions.forEach((level) => {
      const line = series.createPriceLine({
        price: level.value,
        color: level.color || '#ffffff',
        lineWidth: level.lineWidth || 2,
        lineStyle: level.lineStyle ?? 2,
        axisLabelVisible: true,
        title: level.title,
      });
      priceLinesRef.current.push(line);
    });
  }, [levels, livePrice]);

  return <div className="ltp-lightweight-chart">
    {normalizedCandles.length === 0 && <div className="ltp-lightweight-empty">Waiting for Kite historical candles…</div>}
    <div className="ltp-lightweight-canvas" ref={containerRef} aria-label="TradingView Lightweight candlestick chart with T1, T2, S1 and S2 levels" />
    <div className="ltp-lightweight-level-key">
      <span className="t1">T1 · Risky Resistance</span><span className="t2">T2 · Moderate Resistance</span>
      <span className="s1">S1 · Risky Support</span><span className="s2">S2 · Moderate Support</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charting by TradingView</a>
    </div>
  </div>;
}
