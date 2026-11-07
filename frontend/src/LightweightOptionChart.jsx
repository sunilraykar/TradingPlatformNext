import React, { useEffect, useMemo, useRef } from 'react';
import { CandlestickSeries, createChart } from 'lightweight-charts';

function numeric(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function toTimestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    // Kite timestamps are occasionally returned in milliseconds.
    return Math.floor(value > 1e12 ? value / 1000 : value);
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export default function LightweightOptionChart({ candles = [], livePrice, levels = [] }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const priceLinesRef = useRef([]);
  const scaleValuesRef = useRef([]);
  const fittedContentRef = useRef(false);
  const normalizedCandles = useMemo(() => {
    const byTime = new Map();
    candles.forEach((item) => {
      const time = toTimestamp(item.time);
      const open = numeric(item.open);
      const high = numeric(item.high);
      const low = numeric(item.low);
      const close = numeric(item.close);
      if (time === null || [open, high, low, close].some((value) => value === null)) return;
      byTime.set(time, {
        time,
        open,
        high: Math.max(high, open, close, low),
        low: Math.min(low, open, close, high),
        close,
      });
    });
    return [...byTime.values()].sort((a, b) => a.time - b.time);
  }, [candles]);

  const validLevels = useMemo(() => levels
    .map((level) => ({ ...level, value: numeric(level.value) }))
    .filter((level) => level.value !== null), [levels]);
  const currentLivePrice = numeric(livePrice);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = createChart(containerRef.current, {
      width: Math.max(containerRef.current.clientWidth, 320),
      height: Math.max(containerRef.current.clientHeight, 360),
      layout: {
        background: { type: 'solid', color: '#142438' },
        textColor: '#e8f0fa',
        fontFamily: 'Inter, Segoe UI, sans-serif',
        fontSize: 13,
      },
      grid: {
        vertLines: { color: '#263b52' },
        horzLines: { color: '#30465e' },
      },
      rightPriceScale: {
        visible: true,
        borderVisible: true,
        borderColor: '#7890aa',
        entireTextOnly: false,
        minimumWidth: 76,
        autoScale: true,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        visible: true,
        borderColor: '#52677e',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        barSpacing: 9,
        minBarSpacing: 3,
      },
      crosshair: {
        vertLine: { color: '#8ca3bd', labelBackgroundColor: '#344b63' },
        horzLine: { color: '#8ca3bd', labelBackgroundColor: '#344b63' },
      },
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
      // Keep the right axis useful even when levels extend beyond candle highs/lows.
      autoscaleInfoProvider: (original) => {
        const base = original();
        const values = scaleValuesRef.current;
        if (!values.length) return base;
        const minValue = Math.min(...values);
        const maxValue = Math.max(...values);
        if (!base) return {
          priceRange: { minValue, maxValue },
          margins: { above: 0.08, below: 0.08 },
        };
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, minValue),
            maxValue: Math.max(base.priceRange.maxValue, maxValue),
          },
        };
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = series;
    const resize = () => {
      if (!containerRef.current || !chartRef.current) return;
      const width = containerRef.current.clientWidth;
      const height = containerRef.current.clientHeight;
      if (width > 0 && height > 0) chartRef.current.resize(width, height);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(containerRef.current);
    requestAnimationFrame(resize);

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
    const chart = chartRef.current;
    if (!series || !chart) return;
    series.setData(normalizedCandles);
    if (normalizedCandles.length) {
      if (!fittedContentRef.current) {
        chart.timeScale().fitContent();
        fittedContentRef.current = true;
      }
      chart.priceScale('right').applyOptions({ autoScale: true });
    }
  }, [normalizedCandles]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    priceLinesRef.current.forEach((line) => series.removePriceLine(line));
    priceLinesRef.current = [];

    const definitions = [
      ...validLevels.map((level) => ({
        ...level,
        title: level.title,
        lineStyle: 2,
        lineWidth: 2,
      })),
      ...(currentLivePrice !== null ? [{
        title: 'SPOT',
        value: currentLivePrice,
        color: '#ffdf4d',
        lineStyle: 0,
        lineWidth: 2,
      }] : []),
    ];
    scaleValuesRef.current = definitions.map((item) => item.value);

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
    chartRef.current?.priceScale('right').applyOptions({ autoScale: true });
  }, [validLevels, currentLivePrice]);

  return <div className="ltp-lightweight-chart">
    {normalizedCandles.length === 0 && (
      <div className="ltp-lightweight-empty">
        <strong>No recent Kite candles returned</strong>
        <span>Showing the latest available session when Kite provides historical data.</span>
      </div>
    )}
    <div className="ltp-lightweight-canvas" ref={containerRef} aria-label="TradingView Lightweight candlestick chart with T1, T2, S1 and S2 levels" />
    <div className="ltp-lightweight-level-key">
      <span className="t1">T1 · Risky Resistance</span>
      <span className="t2">T2 · Moderate Resistance</span>
      <span className="s1">S1 · Risky Support</span>
      <span className="s2">S2 · Moderate Support</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charting by TradingView</a>
    </div>
  </div>;
}
