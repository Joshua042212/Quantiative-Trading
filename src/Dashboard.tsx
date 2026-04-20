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

interface FinancialOverview {
  ticker: string;
  report_links: ReportLinkItem[];
  monthly_revenue: MonthlyRevenueItem[];
}

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
          setFinancialData({ ticker, report_links: [], monthly_revenue: [] });
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

                <div>
                  <h4 style={{ marginBottom: '8px' }}>最近月營收</h4>
                  {(financialData?.monthly_revenue ?? []).length === 0 ? (
                    <div>目前尚未匯入月營收資料。</div>
                  ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'left', padding: '6px 4px' }}>年月</th>
                          <th style={{ borderBottom: '1px solid #4B3B2B', textAlign: 'right', padding: '6px 4px' }}>營收</th>
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
                            <td style={{ borderBottom: '1px solid #2F241C', textAlign: 'right', padding: '6px 4px' }}>
                              {item.mom_pct == null ? '--' : item.mom_pct.toFixed(2)}
                            </td>
                            <td
                              style={{
                                borderBottom: '1px solid #2F241C',
                                textAlign: 'right',
                                padding: '6px 4px',
                                color: (item.yoy_pct ?? 0) >= 0 ? '#9AE6B4' : '#FF8FA3',
                              }}
                            >
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
    </div>
  );
};

export default Dashboard;