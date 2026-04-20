import React, { useEffect, useState } from 'react';

interface CoverageData {
  downloaded_count: number;
  total_count: number;
  coverage_pct: number;
}

interface UnverifiedItem {
  ticker: string;
  date: string;
  verification_error_pct: number | null;
  error_tag: string | null;
  data_source: string | null;
}

const DataIntegrityDashboard: React.FC = () => {
  const [coverage, setCoverage] = useState<CoverageData | null>(null);
  const [unverifiedList, setUnverifiedList] = useState<UnverifiedItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isRefetching, setIsRefetching] = useState(false);
  const [refetchMessage, setRefetchMessage] = useState('');

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [coverageResp, unverifiedResp] = await Promise.all([
        fetch('http://localhost:8000/api/data-integrity/coverage'),
        fetch('http://localhost:8000/api/data-integrity/unverified?limit=300'),
      ]);

      if (!coverageResp.ok) throw new Error(`coverage HTTP ${coverageResp.status}`);
      if (!unverifiedResp.ok) throw new Error(`unverified HTTP ${unverifiedResp.status}`);

      const coverageData: CoverageData = await coverageResp.json();
      const unverifiedData: UnverifiedItem[] = await unverifiedResp.json();

      setCoverage(coverageData);
      setUnverifiedList(unverifiedData);
    } catch (error) {
      console.error('Load data integrity dashboard failed:', error);
      setCoverage(null);
      setUnverifiedList([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRefetch = async () => {
    setIsRefetching(true);
    setRefetchMessage('重新抓取中...');
    try {
      const response = await fetch('http://localhost:8000/api/data-integrity/refetch-unverified?limit=300', {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`refetch HTTP ${response.status}`);

      const result = await response.json();
      const failedCount = Array.isArray(result.failed_tickers) ? result.failed_tickers.length : 0;
      setRefetchMessage(`完成：更新 ${result.refetched_rows ?? 0} 筆，失敗 ${failedCount} 檔`);
      await loadData();
    } catch (error) {
      console.error('Refetch unverified failed:', error);
      setRefetchMessage('重新抓取失敗，請稍後再試。');
    } finally {
      setIsRefetching(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div style={{ padding: '20px', backgroundColor: '#101722', color: '#E8F0FF', minHeight: '100vh' }}>
      <h2 style={{ marginBottom: '12px' }}>Data Integrity Dashboard</h2>

      <div
        style={{
          border: '1px solid #2B2B43',
          borderRadius: '8px',
          padding: '14px',
          marginBottom: '16px',
          backgroundColor: '#161F2E',
        }}
      >
        <h3 style={{ marginTop: 0 }}>資料庫覆蓋率</h3>
        {coverage ? (
          <div style={{ fontSize: '15px' }}>
            已下載總數 / 台股總檔數：{coverage.downloaded_count} / {coverage.total_count}
            <div style={{ marginTop: '8px', color: '#7EE787' }}>覆蓋率：{coverage.coverage_pct.toFixed(2)}%</div>
          </div>
        ) : (
          <div>尚無資料</div>
        )}
      </div>

      <div
        style={{
          border: '1px solid #2B2B43',
          borderRadius: '8px',
          padding: '14px',
          backgroundColor: '#161F2E',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
          <h3 style={{ margin: 0 }}>異常資料清單 (is_verified = false)</h3>
          <button
            onClick={handleRefetch}
            disabled={isRefetching || isLoading || unverifiedList.length === 0}
            style={{
              padding: '8px 12px',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              color: 'white',
              backgroundColor: '#FF7B72',
            }}
          >
            {isRefetching ? '重新抓取中...' : '重新抓取 Refetch'}
          </button>
        </div>

        <div style={{ marginBottom: '10px', color: '#B8C7E0', fontSize: '13px' }}>{refetchMessage}</div>

        {isLoading ? (
          <div>載入中...</div>
        ) : unverifiedList.length === 0 ? (
          <div>目前沒有未驗證錯誤資料。</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                <th style={{ borderBottom: '1px solid #2F405A', textAlign: 'left', padding: '6px' }}>Ticker</th>
                <th style={{ borderBottom: '1px solid #2F405A', textAlign: 'left', padding: '6px' }}>Date</th>
                <th style={{ borderBottom: '1px solid #2F405A', textAlign: 'right', padding: '6px' }}>Error %</th>
                <th style={{ borderBottom: '1px solid #2F405A', textAlign: 'left', padding: '6px' }}>Error Tag</th>
              </tr>
            </thead>
            <tbody>
              {unverifiedList.map((item) => (
                <tr key={`${item.ticker}-${item.date}`}>
                  <td style={{ borderBottom: '1px solid #223047', padding: '6px' }}>{item.ticker}</td>
                  <td style={{ borderBottom: '1px solid #223047', padding: '6px' }}>{item.date}</td>
                  <td style={{ borderBottom: '1px solid #223047', padding: '6px', textAlign: 'right' }}>
                    {item.verification_error_pct == null ? '--' : item.verification_error_pct.toFixed(4)}
                  </td>
                  <td style={{ borderBottom: '1px solid #223047', padding: '6px' }}>{item.error_tag ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default DataIntegrityDashboard;
