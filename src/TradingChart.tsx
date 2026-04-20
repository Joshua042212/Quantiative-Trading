import React, { useEffect, useRef, useState } from 'react';
import {
  createChart,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts';

interface TradingChartProps {
  ticker: string;
}

interface KlineApiItem {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  sma_5: number | null;
  sma_10: number | null;
  sma_20: number | null;
  sma_60: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
  bb_percent_b: number | null;
  bb_bandwidth: number | null;
  sar: number | null;
}

type IndicatorState = {
  ma5: boolean;
  ma10: boolean;
  ma20: boolean;
  ma60: boolean;
  bollinger: boolean;
  sar: boolean;
};

type IndicatorSeriesKey = 'ma5' | 'ma10' | 'ma20' | 'ma60' | 'bbUpper' | 'bbLower' | 'sar';

type IndicatorParamState = {
  bbLength: number;
  bbStd: number;
  sarStep: number;
  sarMax: number;
};

const TradingChart: React.FC<TradingChartProps> = ({ ticker }) => {
  const [indicatorState, setIndicatorState] = useState<IndicatorState>({
    ma5: true,
    ma10: false,
    ma20: true,
    ma60: false,
    bollinger: false,
    sar: false,
  });
  const [indicatorParams, setIndicatorParams] = useState<IndicatorParamState>({
    bbLength: 20,
    bbStd: 2,
    sarStep: 0.02,
    sarMax: 0.2,
  });
  const [legendItem, setLegendItem] = useState<KlineApiItem | null>(null);

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  const candlestickSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const indicatorSeriesRef = useRef<Partial<Record<IndicatorSeriesKey, any>>>({});
  const latestDataRef = useRef<KlineApiItem[]>([]);

  const formatNullable = (value: number | null | undefined, digits = 2) => {
    if (value === null || value === undefined || Number.isNaN(value)) return '--';
    return value.toFixed(digits);
  };

  const normalizedParams = {
    bbLength: Math.max(1, Math.floor(Number(indicatorParams.bbLength) || 20)),
    bbStd: Math.max(0.1, Number(indicatorParams.bbStd) || 2),
    sarStep: Math.max(0.001, Number(indicatorParams.sarStep) || 0.02),
    sarMax: Math.max(0.01, Number(indicatorParams.sarMax) || 0.2),
  };

  const removeIndicatorSeries = (key: IndicatorSeriesKey) => {
    const chart = chartRef.current;
    const targetSeries = indicatorSeriesRef.current[key];
    if (!chart || !targetSeries) return;

    chart.removeSeries(targetSeries);
    delete indicatorSeriesRef.current[key];
  };

  const clearAllIndicatorSeries = () => {
    const keys = Object.keys(indicatorSeriesRef.current) as IndicatorSeriesKey[];
    keys.forEach((key) => removeIndicatorSeries(key));
  };

  const upsertLineSeries = (
    key: IndicatorSeriesKey,
    color: string,
    sourceKey: keyof KlineApiItem,
    lineStyle: 0 | 1 | 2 = 0,
  ) => {
    const chart = chartRef.current;
    if (!chart) return;

    let series = indicatorSeriesRef.current[key];
    if (!series) {
      // lightweight-charts v5 以 addSeries(LineSeries, options) 取代 addLineSeries。
      series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      indicatorSeriesRef.current[key] = series;
    }

    const lineData = latestDataRef.current
      .filter((item) => item[sourceKey] !== null)
      .map((item) => ({
        time: item.time,
        value: Number(item[sourceKey]),
      }));

    series.setData(lineData);
  };

  const syncIndicatorSeries = () => {
    if (!chartRef.current) return;

    if (indicatorState.ma5) {
      upsertLineSeries('ma5', '#FFD166', 'sma_5');
    } else {
      removeIndicatorSeries('ma5');
    }

    if (indicatorState.ma10) {
      upsertLineSeries('ma10', '#EF476F', 'sma_10');
    } else {
      removeIndicatorSeries('ma10');
    }

    if (indicatorState.ma20) {
      upsertLineSeries('ma20', '#06D6A0', 'sma_20');
    } else {
      removeIndicatorSeries('ma20');
    }

    if (indicatorState.ma60) {
      upsertLineSeries('ma60', '#118AB2', 'sma_60');
    } else {
      removeIndicatorSeries('ma60');
    }

    if (indicatorState.bollinger) {
      upsertLineSeries('bbUpper', '#F4A261', 'bb_upper', 2);
      upsertLineSeries('bbLower', '#F4A261', 'bb_lower', 2);
    } else {
      removeIndicatorSeries('bbUpper');
      removeIndicatorSeries('bbLower');
    }

    if (indicatorState.sar) {
      upsertLineSeries('sar', '#C77DFF', 'sar', 1);
    } else {
      removeIndicatorSeries('sar');
    }
  };

  const toggleIndicator = (key: keyof IndicatorState) => {
    setIndicatorState((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const onParamChange = (key: keyof IndicatorParamState, rawValue: string) => {
    const parsedValue = Number(rawValue);
    setIndicatorParams((prev) => ({
      ...prev,
      [key]: Number.isFinite(parsedValue) ? parsedValue : prev[key],
    }));
  };

  const timeToKey = (timeValue: unknown): string | null => {
    if (!timeValue) return null;
    if (typeof timeValue === 'string') return timeValue;

    if (typeof timeValue === 'object') {
      const item = timeValue as { year?: number; month?: number; day?: number };
      if (item.year && item.month && item.day) {
        const yyyy = String(item.year);
        const mm = String(item.month).padStart(2, '0');
        const dd = String(item.day).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
      }
    }

    return null;
  };

  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return;

    // 初始化圖表 (這部分維持不變，TypeScript 會自動推導正確的 chart 型別)
    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: '#1E222D' },
        textColor: '#D9D9D9',
      },
      grid: {
        vertLines: { color: '#2B2B43' },
        horzLines: { color: '#2B2B43' },
      },
      crosshair: {
        mode: 0,
      },
    });
    chartRef.current = chart;

    // 2. v5 版本的全新寫法！
    // 不再使用 chart.addCandlestickSeries()，而是使用 addSeries 並傳入 CandlestickSeries 模組
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });
    candlestickSeriesRef.current = candlestickSeries;

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: '',
      priceFormat: {
        type: 'volume',
      },
    });
    volumeSeriesRef.current = volumeSeries;

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    const handleCrosshairMove = (param: any) => {
      if (!latestDataRef.current.length) {
        setLegendItem(null);
        return;
      }

      const timeKey = timeToKey(param?.time);
      if (!timeKey) {
        setLegendItem(latestDataRef.current[latestDataRef.current.length - 1]);
        return;
      }

      const matched = latestDataRef.current.find((row) => row.time === timeKey);
      setLegendItem(matched ?? latestDataRef.current[latestDataRef.current.length - 1]);
    };
    chart.subscribeCrosshairMove(handleCrosshairMove);

    // 視窗縮放自適應
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    // 卸載時清理記憶體
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      clearAllIndicatorSeries();
      candlestickSeriesRef.current = null;
      volumeSeriesRef.current = null;
      latestDataRef.current = [];
      setLegendItem(null);
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candlestickSeries = candlestickSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    if (!chart || !candlestickSeries || !volumeSeries) return;

    let isCancelled = false;

    const fetchKlineData = async () => {
      candlestickSeries.setData([]);
      volumeSeries.setData([]);
      latestDataRef.current = [];

      // 切換股票時先移除所有指標線，避免舊線疊在新股票資料上。
      clearAllIndicatorSeries();

      try {
        const query = new URLSearchParams({
          bb_length: String(normalizedParams.bbLength),
          bb_std: String(normalizedParams.bbStd),
          sar_step: String(normalizedParams.sarStep),
          sar_max: String(normalizedParams.sarMax),
        });
        const response = await fetch(`http://localhost:8000/api/kline/${ticker}?${query.toString()}`);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data: KlineApiItem[] = await response.json();

        const candlestickData = data.map((item) => ({
          time: item.time,
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        }));
        const volumeData = data.map((item) => ({
          time: item.time,
          value: item.volume,
          color: item.close >= item.open ? '#26a69a' : '#ef5350',
        }));

        if (isCancelled) return;

        latestDataRef.current = data;
        setLegendItem(data[data.length - 1] ?? null);
        candlestickSeries.setData(candlestickData);
        volumeSeries.setData(volumeData);

        syncIndicatorSeries();
        chart.timeScale().fitContent();
        chart.timeScale().scrollToRealTime();
      } catch (error) {
        if (!isCancelled) {
          console.error(`Failed to fetch kline data for ${ticker}:`, error);
        }
      }
    };

    fetchKlineData();

    return () => {
      isCancelled = true;
      clearAllIndicatorSeries();
    };
  }, [
    ticker,
    normalizedParams.bbLength,
    normalizedParams.bbStd,
    normalizedParams.sarStep,
    normalizedParams.sarMax,
  ]);

  useEffect(() => {
    // 勾選狀態變更時即時同步圖上指標，並用 removeSeries 清掉未勾選的線。
    syncIndicatorSeries();
  }, [indicatorState]);

  return (
    <div style={{ width: '100%' }}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '10px',
          padding: '10px 12px',
          marginBottom: '10px',
          border: '1px solid #2B2B43',
          borderRadius: '6px',
          backgroundColor: '#161B26',
          color: '#D9D9D9',
        }}
      >
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="number"
            min={1}
            step={1}
            value={indicatorParams.bbLength}
            onChange={(e) => onParamChange('bbLength', e.target.value)}
            style={{ width: '70px' }}
          />
          BB Length
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="number"
            min={0.1}
            step={0.1}
            value={indicatorParams.bbStd}
            onChange={(e) => onParamChange('bbStd', e.target.value)}
            style={{ width: '70px' }}
          />
          BB Std
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="number"
            min={0.001}
            step={0.001}
            value={indicatorParams.sarStep}
            onChange={(e) => onParamChange('sarStep', e.target.value)}
            style={{ width: '84px' }}
          />
          SAR Step
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="number"
            min={0.01}
            step={0.01}
            value={indicatorParams.sarMax}
            onChange={(e) => onParamChange('sarMax', e.target.value)}
            style={{ width: '84px' }}
          />
          SAR Max
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input type="checkbox" checked={indicatorState.ma5} onChange={() => toggleIndicator('ma5')} />
          MA5
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input type="checkbox" checked={indicatorState.ma10} onChange={() => toggleIndicator('ma10')} />
          MA10
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input type="checkbox" checked={indicatorState.ma20} onChange={() => toggleIndicator('ma20')} />
          MA20
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input type="checkbox" checked={indicatorState.ma60} onChange={() => toggleIndicator('ma60')} />
          MA60
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={indicatorState.bollinger}
            onChange={() => toggleIndicator('bollinger')}
          />
          布林通道
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input type="checkbox" checked={indicatorState.sar} onChange={() => toggleIndicator('sar')} />
          SAR
        </label>
      </div>

      <div
        style={{
          marginBottom: '10px',
          padding: '8px 12px',
          border: '1px solid #2B2B43',
          borderRadius: '6px',
          backgroundColor: '#101620',
          color: '#C8D4E3',
          fontSize: '13px',
          display: 'flex',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <span>Time: {legendItem?.time ?? '--'}</span>
        <span>Close: {formatNullable(legendItem?.close, 2)}</span>
        <span>BB Upper: {formatNullable(legendItem?.bb_upper, 2)}</span>
        <span>BB Lower: {formatNullable(legendItem?.bb_lower, 2)}</span>
        <span>BB %b: {formatNullable(legendItem?.bb_percent_b, 4)}</span>
        <span>BB Bandwidth: {formatNullable(legendItem?.bb_bandwidth, 4)}</span>
      </div>

      <div
        ref={chartContainerRef}
        style={{ width: '100%', height: '500px', border: '1px solid #2B2B43' }}
      />
    </div>
  );
};

export default TradingChart;