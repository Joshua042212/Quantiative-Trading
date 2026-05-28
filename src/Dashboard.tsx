import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import TradingChart from './TradingChart.tsx';

interface NewsItem {
  ticker: string;
  title: string;
  url: string;
  source: string;
  published_at: string;
}

interface EtfPremiumDiscountItem {
  date: string;
  ticker: string;
  nav: number;
  market_price: number;
  premium_discount_pct: number;
}

interface MonthlyRevenueItem {
  ticker: string;
  company_name: string | null;
  revenue_year: number;
  revenue_month: number;
  revenue_amount: number;
  previous_month_revenue: number | null;
  last_year_revenue: number | null;
  mom_pct: number | null;
  yoy_pct: number | null;
  source: string | null;
}

interface ReportLinkItem {
  label: string;
  url: string;
}

interface QuarterlyFinancialItem {
  report_date: string;
  basic_eps: number | null;
  diluted_eps: number | null;
  net_income: number | null;
  total_revenue: number | null;
}

interface FinancialOverview {
  ticker: string;
  report_links: ReportLinkItem[];
  monthly_revenue: MonthlyRevenueItem[];
  quarterly_financials: QuarterlyFinancialItem[];
}

interface PeDataPoint {
  date: string;
  close: number;
  ttm_eps: number;
  pe_ratio: number;
}

interface PeOverview {
  ticker: string;
  data: PeDataPoint[];
}

/** Tiny SVG line chart */
const PeLineChart: React.FC<{ data: PeDataPoint[] }> = ({ data }) => {
  const W = 460, H = 200, PAD_L = 44, PAD_R = 8, PAD_T = 12, PAD_B = 28;
  if (data.length < 2) return <p style={{ color: '#888' }}>資料不足無法繪圖</p>;

  const peValues = data.map(d => d.pe_ratio);
  const minPe = Math.min(...peValues);
  const maxPe = Math.max(...peValues);
  const range = maxPe - minPe || 1;

  const toX = (i: number) => PAD_L + ((i / (data.length - 1)) * (W - PAD_L - PAD_R));
  const toY = (pe: number) => PAD_T + ((maxPe - pe) / range) * (H - PAD_T - PAD_B);

  const pathD = data
    .map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.pe_ratio).toFixed(1)}`)
    .join(' ');

  // Y-axis ticks
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => minPe + (range * i) / (tickCount - 1));

  // X-axis labels (show ~5 evenly spaced years)
  const xLabels: { i: number; label: string }[] = [];
  const step = Math.max(1, Math.floor(data.length / 5));
  for (let i = 0; i < data.length; i += step) {
    xLabels.push({ i, label: data[i].date.slice(0, 7) });
  }

  return (
    <svg width={W} height={H} style={{ overflow: 'visible' }}>
      {/* grid lines */}
      {ticks.map((t) => (
        <line
          key={t}
          x1={PAD_L} y1={toY(t)}
          x2={W - PAD_R} y2={toY(t)}
          stroke="#2B2B43" strokeWidth={1}
        />
      ))}
      {/* Y-axis labels */}
      {ticks.map((t) => (
        <text key={t} x={PAD_L - 4} y={toY(t) + 4} textAnchor="end" fill="#888" fontSize={10}>
          {t.toFixed(1)}
        </text>
      ))}
      {/* X-axis labels */}
      {xLabels.map(({ i, label }) => (
        <text key={i} x={toX(i)} y={H - 4} textAnchor="middle" fill="#888" fontSize={9}>
          {label}
        </text>
      ))}
      {/* P/E line */}
      <path d={pathD} fill="none" stroke="#F0B429" strokeWidth={1.5} />
      {/* current value dot */}
      <circle
        cx={toX(data.length - 1)}
        cy={toY(data[data.length - 1].pe_ratio)}
        r={3}
        fill="#F0B429"
      />
    </svg>
  );
};


const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [ticker, setTicker] = useState('2330.TW');
  const [inputValue, setInputValue] = useState('2330.TW');
  const [isNewsOpen, setIsNewsOpen] = useState(false);
  const [isEtfOpen, setIsEtfOpen] = useState(false);
  const [isFinancialsOpen, setIsFinancialsOpen] = useState(false);
  const [newsItems, setNewsItems] = useState<NewsItem[]>([]);
  const [etfItems, setEtfItems] = useState<EtfPremiumDiscountItem[]>([]);
  const [financialData, setFinancialData] = useState<FinancialOverview | null>(null);
  const [isNewsLoading, setIsNewsLoading] = useState(false);
  const [isEtfLoading, setIsEtfLoading] = useState(false);
  const [isFinancialsLoading, setIsFinancialsLoading] = useState(false);
  const [isPeOpen, setIsPeOpen] = useState(false);
  const [peData, setPeData] = useState<PeOverview | null>(null);
  const [isPeLoading, setIsPeLoading] = useState(false);
  const [peError, setPeError] = useState<string | null>(null);

  const handleLogout = () => {
    navigate('/login');
  };

  const handleSearch = () => {
    const trimmed = inputValue.trim();
    if (trimmed) setTicker(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSearch();
  };

  const formatHHMM = (value: string) => {
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return '--:--';
    return dt.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false });
  };

  const formatCurrency = (value: number | null) => {
    if (value == null) return '--';
    return new Intl.NumberFormat('zh-TW').format(value);
  };

  React.useEffect(() => {
    if (!isNewsOpen) return;

    let isCancelled = false;

    const fetchNews = async () => {
      setIsNewsLoading(true);
      try {
        const response = await fetch(`http://localhost:8000/api/news/${ticker}?limit=30`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data: NewsItem[] = await response.json();
        const toMinuteTimestamp = (input: string) => Math.floor(new Date(input).getTime() / 60000);
        const sortedByMinute = [...data].sort((a, b) => {
          const minuteDiff = toMinuteTimestamp(b.published_at) - toMinuteTimestamp(a.published_at);
          if (minuteDiff !== 0) return minuteDiff;
          return new Date(b.published_at).getTime() - new Date(a.published_at).getTime();
        });
        if (!isCancelled) setNewsItems(sortedByMinute);
      } catch (error) {
        if (!isCancelled) {
          console.error('Fetch news failed:', error);
          setNewsItems([]);
        }
      } finally {
        if (!isCancelled) setIsNewsLoading(false);
      }
    };

    fetchNews();

    return () => {
      isCancelled = true;
    };
  }, [ticker, isNewsOpen]);

  React.useEffect(() => {
    if (!isEtfOpen) return;

    let isCancelled = false;

    const fetchEtfData = async () => {
      setIsEtfLoading(true);
      try {
        const response = await fetch(`http://localhost:8000/api/etf-premium-discount/${ticker}?limit=20`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data: EtfPremiumDiscountItem[] = await response.json();
        if (!isCancelled) setEtfItems(data);
      } catch (error) {
        if (!isCancelled) {
          console.error('Fetch ETF premium/discount failed:', error);
          setEtfItems([]);
        }
      } finally {
        if (!isCancelled) setIsEtfLoading(false);
      }
    };

    fetchEtfData();

    return () => {
      isCancelled = true;
    };
  }, [ticker, isEtfOpen]);

  React.useEffect(() => {
    if (!isFinancialsOpen) return;

    let isCancelled = false;

    const fetchFinancials = async () => {
      setIsFinancialsLoading(true);
      try {
        const response = await fetch(`http://localhost:8000/api/financials/${ticker}?limit=12`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data: FinancialOverview = await response.json();
        if (!isCancelled) setFinancialData(data);
      } catch (error) {
        if (!isCancelled) {
          console.error('Fetch financial data failed:', error);
          setFinancialData({ ticker, report_links: [], monthly_revenue: [], quarterly_financials: [] });
        }
      } finally {
        if (!isCancelled) setIsFinancialsLoading(false);
      }
    };

    fetchFinancials();

    return () => {
      isCancelled = true;
    };
  }, [ticker, isFinancialsOpen]);

  React.useEffect(() => {
    if (!isPeOpen) return;

    let isCancelled = false;

    const fetchPe = async () => {
      setIsPeLoading(true);
      setPeError(null);
      try {
        const response = await fetch(`http://localhost:8000/api/pe/${ticker}?years=10`);
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body?.detail ?? `HTTP ${response.status}`);
        }
        const data: PeOverview = await response.json();
        if (!isCancelled) setPeData(data);
      } catch (error) {
        if (!isCancelled) {
          console.error('Fetch PE failed:', error);
          setPeError(error instanceof Error ? error.message : '讀取失敗');
          setPeData(null);
        }
      } finally {
        if (!isCancelled) setIsPeLoading(false);
      }
    };

    fetchPe();

    return () => {
      isCancelled = true;
    };
  }, [ticker, isPeOpen]);

  return (
    <div style={{ padding: '20px', backgroundColor: '#131722', minHeight: '100vh', color: 'white' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2>我的量化看盤儀表板</h2>
        <button onClick={handleLogout} style={{ padding: '8px 16px', cursor: 'pointer' }}>登出</button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="輸入股票代碼，例如 2330.TW"
          style={{
            padding: '8px 12px',
            fontSize: '14px',
            backgroundColor: '#1E222D',
            color: 'white',
            border: '1px solid #2B2B43',
            borderRadius: '4px',
            width: '220px',
          }}
        />
        <button
          onClick={handleSearch}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            cursor: 'pointer',
            backgroundColor: '#2962FF',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
          }}
        >
          搜尋
        </button>
        <button
          onClick={() => setIsNewsOpen(true)}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            cursor: 'pointer',
            backgroundColor: '#16A085',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
          }}
        >
          新聞
        </button>
        <button
          onClick={() => setIsEtfOpen(true)}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            cursor: 'pointer',
            backgroundColor: '#8E44AD',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
          }}
        >
          折溢價
        </button>
        <button
          onClick={() => setIsFinancialsOpen(true)}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            cursor: 'pointer',
            backgroundColor: '#C97A00',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
          }}
        >
          報表／月營收
        </button>
        <button
          onClick={() => setIsPeOpen(true)}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            cursor: 'pointer',
            backgroundColor: '#1A5C3A',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
          }}
        >
          本益比
        </button>
      </div>

      <h3 style={{ marginBottom: '16px', color: '#26a69a' }}>
        目前正在觀看：{ticker}
      </h3>

      <TradingChart ticker={ticker} />

      {isNewsOpen && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            right: 0,
            width: '420px',
            maxWidth: '92vw',
            height: '100vh',
            backgroundColor: '#0E1623',
            borderLeft: '1px solid #2B2B43',
            zIndex: 1000,
            padding: '16px',
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0 }}>新聞列表 - {ticker}</h3>
            <button onClick={() => setIsNewsOpen(false)} style={{ cursor: 'pointer' }}>
              關閉
            </button>
          </div>

          <div style={{ marginTop: '14px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {isNewsLoading && <p>讀取新聞中...</p>}
            {!isNewsLoading && newsItems.length === 0 && <p>查無新聞資料。</p>}

            {!isNewsLoading &&
              newsItems.map((item, index) => (
                <div
                  key={`${item.url}-${index}`}
                  style={{
                    padding: '10px',
                    border: '1px solid #2B2B43',
                    borderRadius: '6px',
                    backgroundColor: '#131D2B',
                  }}
                >
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: '#7BDFF2', textDecoration: 'none', fontWeight: 600 }}
                  >
                    {item.title}
                  </a>
                  <div style={{ marginTop: '6px', fontSize: '12px', color: '#B7C4D6' }}>
                    <span>來源: {item.source}</span>
                    <span style={{ marginLeft: '10px' }}>時間: {formatHHMM(item.published_at)}</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {isEtfOpen && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            right: 0,
            width: '440px',
            maxWidth: '94vw',
            height: '100vh',
            backgroundColor: '#1A1124',
            borderLeft: '1px solid #2B2B43',
            zIndex: 1000,
            padding: '16px',
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0 }}>ETF 折溢價 - {ticker}</h3>
            <button onClick={() => setIsEtfOpen(false)} style={{ cursor: 'pointer' }}>
              關閉
            </button>
          </div>

          <div style={{ marginTop: '14px' }}>
            {isEtfLoading && <p>讀取折溢價資料中...</p>}
            {!isEtfLoading && etfItems.length === 0 && <p>目前沒有可顯示的 ETF 折溢價資料。</p>}

            {!isEtfLoading && etfItems.length > 0 && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                <thead>
                  <tr>
                    <th style={{ borderBottom: '1px solid #3A2D4A', textAlign: 'left', padding: '6px 4px' }}>日期</th>
                    <th style={{ borderBottom: '1px solid #3A2D4A', textAlign: 'right', padding: '6px 4px' }}>市價</th>
                    <th style={{ borderBottom: '1px solid #3A2D4A', textAlign: 'right', padding: '6px 4px' }}>淨值</th>
                    <th style={{ borderBottom: '1px solid #3A2D4A', textAlign: 'right', padding: '6px 4px' }}>折溢價%</th>
                  </tr>
                </thead>
                <tbody>
                  {etfItems.map((item) => (
                    <tr key={`${item.ticker}-${item.date}`}>
                      <td style={{ borderBottom: '1px solid #2A2038', padding: '6px 4px' }}>{item.date}</td>
                      <td style={{ borderBottom: '1px solid #2A2038', textAlign: 'right', padding: '6px 4px' }}>
                        {item.market_price.toFixed(2)}
                      </td>
                      <td style={{ borderBottom: '1px solid #2A2038', textAlign: 'right', padding: '6px 4px' }}>
                        {item.nav.toFixed(2)}
                      </td>
                      <td
                        style={{
                          borderBottom: '1px solid #2A2038',
                          textAlign: 'right',
                          padding: '6px 4px',
                          color: item.premium_discount_pct >= 0 ? '#FF8FA3' : '#9AE6B4',
                        }}
                      >
                        {item.premium_discount_pct.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {isFinancialsOpen && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            right: 0,
            width: '520px',
            maxWidth: '96vw',
            height: '100vh',
            backgroundColor: '#191612',
            borderLeft: '1px solid #3B2D20',
            zIndex: 1000,
            padding: '16px',
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0 }}>報表／月營收 - {ticker}</h3>
            <button onClick={() => setIsFinancialsOpen(false)} style={{ cursor: 'pointer' }}>
              關閉
            </button>
          </div>

          <div style={{ marginTop: '14px' }}>
            {isFinancialsLoading && <p>讀取報表與月營收中...</p>}

            {!isFinancialsLoading && (
              <>
                <div style={{ marginBottom: '16px' }}>
                  <h4 style={{ marginBottom: '8px' }}>報表連結</h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {(financialData?.report_links ?? []).map((item) => (
                      <a
                        key={item.label}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: '#FFD166', textDecoration: 'none' }}
                      >
                        {item.label}
                      </a>
                    ))}
                    {(financialData?.report_links ?? []).length === 0 && <div>目前沒有可顯示的報表連結。</div>}
                  </div>
                </div>

                {/* ── 季報財務摘要 ── */}
                <div style={{ marginBottom: '20px' }}>
                  <h4 style={{ marginBottom: '8px', color: '#90CDF4' }}>季報財務摘要（最近 12 季）</h4>
                  {(financialData?.quarterly_financials ?? []).length === 0 ? (
                    <div style={{ color: '#888', fontSize: '13px' }}>查無季報資料。</div>
                  ) : (
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                        <thead>
                          <tr>
                            {['報告日期', 'EPS (基本)', 'EPS (稀釋)', '淨利 (千)', '營收 (千)'].map(h => (
                              <th key={h} style={{ borderBottom: '1px solid #2B3A4B', textAlign: h === '報告日期' ? 'left' : 'right', padding: '6px 4px', color: '#aaa', whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(financialData?.quarterly_financials ?? []).map((item) => (
                            <tr key={item.report_date}>
                              <td style={{ borderBottom: '1px solid #1E2A35', padding: '6px 4px', color: '#ccc' }}>{item.report_date}</td>
                              <td style={{ borderBottom: '1px solid #1E2A35', textAlign: 'right', padding: '6px 4px', color: (item.basic_eps ?? 0) >= 0 ? '#9AE6B4' : '#FF8FA3' }}>
                                {item.basic_eps == null ? '--' : item.basic_eps.toFixed(2)}
                              </td>
                              <td style={{ borderBottom: '1px solid #1E2A35', textAlign: 'right', padding: '6px 4px', color: (item.diluted_eps ?? 0) >= 0 ? '#9AE6B4' : '#FF8FA3' }}>
                                {item.diluted_eps == null ? '--' : item.diluted_eps.toFixed(2)}
                              </td>
                              <td style={{ borderBottom: '1px solid #1E2A35', textAlign: 'right', padding: '6px 4px' }}>
                                {item.net_income == null ? '--' : formatCurrency(Math.round(item.net_income / 1000))}
                              </td>
                              <td style={{ borderBottom: '1px solid #1E2A35', textAlign: 'right', padding: '6px 4px' }}>
                                {item.total_revenue == null ? '--' : formatCurrency(Math.round(item.total_revenue / 1000))}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* ── 月營收 ── */}
                <div>
                  <h4 style={{ marginBottom: '8px', color: '#FFD166' }}>最近月營收（最近 12 月）</h4>
                  {(financialData?.monthly_revenue ?? []).length === 0 ? (
                    <div style={{ color: '#888', fontSize: '13px' }}>目前尚未匯入月營收資料。</div>
                  ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'left', padding: '6px 4px' }}>年月</th>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'right', padding: '6px 4px' }}>營收（千）</th>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'right', padding: '6px 4px' }}>月增%</th>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'right', padding: '6px 4px' }}>年增%</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(financialData?.monthly_revenue ?? []).map((item) => (
                          <tr key={`${item.ticker}-${item.revenue_year}-${item.revenue_month}`}>
                            <td style={{ borderBottom: '1px solid #2F241C', padding: '6px 4px' }}>
                              {item.revenue_year}/{String(item.revenue_month).padStart(2, '0')}
                            </td>
                            <td style={{ borderBottom: '1px solid #2F241C', textAlign: 'right', padding: '6px 4px' }}>
                              {formatCurrency(item.revenue_amount)}
                            </td>
                            <td style={{ borderBottom: '1px solid #2F241C', textAlign: 'right', padding: '6px 4px', color: (item.mom_pct ?? 0) >= 0 ? '#9AE6B4' : '#FF8FA3' }}>
                              {item.mom_pct == null ? '--' : item.mom_pct.toFixed(2)}
                            </td>
                            <td style={{ borderBottom: '1px solid #2F241C', textAlign: 'right', padding: '6px 4px', color: (item.yoy_pct ?? 0) >= 0 ? '#9AE6B4' : '#FF8FA3' }}>
                              {item.yoy_pct == null ? '--' : item.yoy_pct.toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {isPeOpen && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            right: 0,
            width: '540px',
            maxWidth: '96vw',
            height: '100vh',
            backgroundColor: '#0D1A13',
            borderLeft: '1px solid #1B3B28',
            zIndex: 1000,
            padding: '16px',
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0, color: '#F0B429' }}>本益比 (TTM P/E) — {ticker}</h3>
            <button onClick={() => setIsPeOpen(false)} style={{ cursor: 'pointer' }}>關閉</button>
          </div>

          <div style={{ marginTop: '14px' }}>
            {isPeLoading && <p>計算本益比中...</p>}
            {peError && <p style={{ color: '#FF8FA3' }}>錯誤：{peError}</p>}

            {!isPeLoading && !peError && peData && (() => {
              const pts = peData.data;
              if (pts.length === 0) return <p>查無本益比資料</p>;

              const peValues = pts.map(d => d.pe_ratio);
              const latest = pts[pts.length - 1];
              const minPe = Math.min(...peValues);
              const maxPe = Math.max(...peValues);
              const avgPe = peValues.reduce((a, b) => a + b, 0) / peValues.length;

              // Percentile of current PE
              const below = peValues.filter(v => v <= latest.pe_ratio).length;
              const pct = ((below / peValues.length) * 100).toFixed(0);

              return (
                <>
                  {/* Summary stats */}
                  <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '16px' }}>
                    {[
                      { label: '目前 P/E', value: latest.pe_ratio.toFixed(1), color: '#F0B429' },
                      { label: '最低', value: minPe.toFixed(1), color: '#9AE6B4' },
                      { label: '平均', value: avgPe.toFixed(1), color: '#90CDF4' },
                      { label: '最高', value: maxPe.toFixed(1), color: '#FF8FA3' },
                      { label: '歷史百分位', value: `${pct}%`, color: '#D6BCFA' },
                    ].map(({ label, value, color }) => (
                      <div key={label} style={{
                        backgroundColor: '#1A2B20',
                        border: '1px solid #2B4A35',
                        borderRadius: '6px',
                        padding: '8px 12px',
                        minWidth: '90px',
                      }}>
                        <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>{label}</div>
                        <div style={{ fontSize: '16px', fontWeight: 600, color }}>{value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Chart */}
                  <div style={{ overflowX: 'auto', marginBottom: '16px' }}>
                    <PeLineChart data={pts} />
                  </div>

                  {/* Recent data table */}
                  <h4 style={{ marginBottom: '8px', color: '#ccc' }}>最近 20 筆</h4>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                    <thead>
                      <tr>
                        {['日期', '收盤價', 'TTM EPS', 'P/E'].map(h => (
                          <th key={h} style={{ borderBottom: '1px solid #2B4A35', textAlign: 'right', padding: '5px 4px', color: '#aaa' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pts.slice(-20).reverse().map((row) => (
                        <tr key={row.date}>
                          <td style={{ borderBottom: '1px solid #1A2B20', padding: '5px 4px', color: '#ccc' }}>{row.date}</td>
                          <td style={{ borderBottom: '1px solid #1A2B20', textAlign: 'right', padding: '5px 4px' }}>
                            {row.close.toLocaleString()}
                          </td>
                          <td style={{ borderBottom: '1px solid #1A2B20', textAlign: 'right', padding: '5px 4px' }}>
                            {row.ttm_eps.toFixed(2)}
                          </td>
                          <td style={{
                            borderBottom: '1px solid #1A2B20',
                            textAlign: 'right',
                            padding: '5px 4px',
                            color: row.pe_ratio < avgPe ? '#9AE6B4' : row.pe_ratio > avgPe * 1.3 ? '#FF8FA3' : '#F0B429',
                          }}>
                            {row.pe_ratio.toFixed(1)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
};

export default Dashboard;