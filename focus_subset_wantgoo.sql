-- Focus subset build script (WantGoo 0050/0052 + hot30)
-- Run on LOCAL stock_db first, then backup/restore *_focus tables to Neon.

BEGIN;

DROP TABLE IF EXISTS raw_kline_yfinance_focus;
DROP TABLE IF EXISTS monthly_revenue_focus;
DROP TABLE IF EXISTS company_info_focus;
DROP TABLE IF EXISTS company_financials_focus;

CREATE TABLE raw_kline_yfinance_focus AS
SELECT *
FROM raw_kline_yfinance
WHERE ticker IN ('0050.TW', '0052.TW', '1216.TW', '1301.TW', '1303.TW', '2002.TW', '2059.TW', '2207.TW', '2301.TW', '2303.TW', '2308.TW', '2313.TW', '2317.TW', '2324.TW', '2327.TW', '2330.TW', '2344.TW', '2345.TW', '2347.TW', '2353.TW', '2354.TW', '2356.TW', '2357.TW', '2360.TW', '2368.TW', '2376.TW', '2377.TW', '2379.TW', '2382.TW', '2383.TW', '2385.TW', '2395.TW', '2408.TW', '2409.TW', '2412.TW', '2449.TW', '2451.TW', '2454.TW', '2474.TW', '2603.TW', '2609.TW', '2615.TW', '2618.TW', '2880.TW', '2881.TW', '2882.TW', '2883.TW', '2884.TW', '2885.TW', '2886.TW', '2887.TW', '2890.TW', '2891.TW', '2892.TW', '3005.TW', '3008.TW', '3017.TW', '3034.TW', '3036.TW', '3037.TW', '3044.TW', '3045.TW', '3189.TW', '3231.TW', '3443.TW', '3481.TW', '3533.TW', '3653.TW', '3661.TW', '3665.TW', '3702.TW', '3706.TW', '3711.TW', '4904.TW', '4938.TW', '4958.TW', '5269.TW', '5434.TW', '5880.TW', '6176.TW', '6191.TW', '6239.TW', '6415.TW', '6505.TW', '6515.TW', '6526.TW', '6531.TW', '6669.TW', '6770.TW', '6789.TW', '6805.TW', '6919.TW', '7769.TW', '8046.TW', '8210.TW');

CREATE TABLE monthly_revenue_focus AS
SELECT *
FROM monthly_revenue
WHERE ticker IN ('0050.TW', '0052.TW', '1216.TW', '1301.TW', '1303.TW', '2002.TW', '2059.TW', '2207.TW', '2301.TW', '2303.TW', '2308.TW', '2313.TW', '2317.TW', '2324.TW', '2327.TW', '2330.TW', '2344.TW', '2345.TW', '2347.TW', '2353.TW', '2354.TW', '2356.TW', '2357.TW', '2360.TW', '2368.TW', '2376.TW', '2377.TW', '2379.TW', '2382.TW', '2383.TW', '2385.TW', '2395.TW', '2408.TW', '2409.TW', '2412.TW', '2449.TW', '2451.TW', '2454.TW', '2474.TW', '2603.TW', '2609.TW', '2615.TW', '2618.TW', '2880.TW', '2881.TW', '2882.TW', '2883.TW', '2884.TW', '2885.TW', '2886.TW', '2887.TW', '2890.TW', '2891.TW', '2892.TW', '3005.TW', '3008.TW', '3017.TW', '3034.TW', '3036.TW', '3037.TW', '3044.TW', '3045.TW', '3189.TW', '3231.TW', '3443.TW', '3481.TW', '3533.TW', '3653.TW', '3661.TW', '3665.TW', '3702.TW', '3706.TW', '3711.TW', '4904.TW', '4938.TW', '4958.TW', '5269.TW', '5434.TW', '5880.TW', '6176.TW', '6191.TW', '6239.TW', '6415.TW', '6505.TW', '6515.TW', '6526.TW', '6531.TW', '6669.TW', '6770.TW', '6789.TW', '6805.TW', '6919.TW', '7769.TW', '8046.TW', '8210.TW');

CREATE TABLE company_info_focus AS
WITH focus_codes AS (
    SELECT DISTINCT regexp_replace(
        UPPER(regexp_replace(btrim(ticker), '([.]TW|[.]TWO)$', '', 'i')),
        '[^0-9A-Z]',
        '',
        'g'
    ) AS code
    FROM raw_kline_yfinance_focus
)
SELECT ci.*
FROM company_info ci
JOIN focus_codes fc
    ON regexp_replace(
        UPPER(regexp_replace(btrim(ci.ticker), '([.]TW|[.]TWO)$', '', 'i')),
        '[^0-9A-Z]',
        '',
        'g'
    ) = fc.code;

CREATE TABLE company_financials_focus AS
WITH focus_codes AS (
    SELECT DISTINCT regexp_replace(
        UPPER(regexp_replace(btrim(ticker), '([.]TW|[.]TWO)$', '', 'i')),
        '[^0-9A-Z]',
        '',
        'g'
    ) AS code
    FROM raw_kline_yfinance_focus
)
SELECT cf.*
FROM company_financials cf
JOIN focus_codes fc
  ON regexp_replace(
            UPPER(regexp_replace(btrim(cf.ticker), '([.]TW|[.]TWO)$', '', 'i')),
      '[^0-9A-Z]',
      '',
      'g'
  ) = fc.code;

CREATE INDEX IF NOT EXISTS ix_raw_kline_yfinance_focus_ticker_date
    ON raw_kline_yfinance_focus (ticker, date);
CREATE INDEX IF NOT EXISTS ix_monthly_revenue_focus_ticker_ym
    ON monthly_revenue_focus (ticker, revenue_year, revenue_month);
CREATE INDEX IF NOT EXISTS ix_company_financials_focus_ticker_date
    ON company_financials_focus (ticker, report_date);

COMMIT;

-- Verification
SELECT 'raw_kline_yfinance_focus' AS table_name, COUNT(*) AS row_count FROM raw_kline_yfinance_focus
UNION ALL
SELECT 'monthly_revenue_focus', COUNT(*) FROM monthly_revenue_focus
UNION ALL
SELECT 'company_info_focus', COUNT(*) FROM company_info_focus
UNION ALL
SELECT 'company_financials_focus', COUNT(*) FROM company_financials_focus
ORDER BY table_name;
