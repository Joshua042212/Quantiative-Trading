from datetime import date, datetime, timedelta, timezone
from io import StringIO
import os
import re

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

try:
    from .database import Base, engine, get_db
    from .models import CrawlLog, EtfPremiumDiscount, MonthlyRevenue, NewsArticle, RawKlineYFinance, StockKline
except ImportError:
    from database import Base, engine, get_db
    from models import CrawlLog, EtfPremiumDiscount, MonthlyRevenue, NewsArticle, RawKlineYFinance, StockKline

try:
    import pandas_ta as ta  # type: ignore[import-not-found]
except ImportError:
    ta = None

Base.metadata.create_all(bind=engine)


def ensure_runtime_schema() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE stock_kline
                ADD COLUMN IF NOT EXISTS is_verified BOOLEAN,
                ADD COLUMN IF NOT EXISTS data_source VARCHAR(64),
                ADD COLUMN IF NOT EXISTS error_tag VARCHAR(255),
                ADD COLUMN IF NOT EXISTS verification_error_pct DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS has_anomaly BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS anomaly_reason VARCHAR(512),
                ADD COLUMN IF NOT EXISTS adj_open DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS adj_high DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS adj_low DOUBLE PRECISION
                """
            )
        )


ensure_runtime_schema()

ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp"
_UNIVERSE_CACHE: dict[str, object] = {"count": None, "updated_at": None}
_TABLE_NAME_CACHE: dict[str, str] = {}


def resolve_table_name(db: Session, base_table: str) -> str:
    """Resolve runtime table name with local-first defaults.

    Default behavior prefers full base tables locally, and falls back to
    *_focus tables when base tables are unavailable (e.g., Neon focus-only DB).
    Set PREFER_FOCUS_TABLES=true to force focus-first resolution.
    """
    cached = _TABLE_NAME_CACHE.get(base_table)
    if cached:
        return cached

    prefer_focus = os.getenv("PREFER_FOCUS_TABLES", "false").strip().lower() in {"1", "true", "yes", "on"}
    if prefer_focus:
        candidates = [f"{base_table}_focus", base_table]
    else:
        candidates = [base_table, f"{base_table}_focus"]

    for candidate in candidates:
        found = db.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": candidate},
        ).scalar()
        if found:
            _TABLE_NAME_CACHE[base_table] = candidate
            return candidate

    # Keep behavior explicit if neither table exists.
    raise HTTPException(status_code=500, detail=f"Table not found: {base_table} or {base_table}_focus")


app = FastAPI()


def get_allowed_origins() -> list[str]:
    origins = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]

    # Accept comma-separated production frontend origins from environment.
    extra_origins = os.getenv("FRONTEND_ORIGINS", "").strip()
    single_origin = os.getenv("FRONTEND_ORIGIN", "").strip()
    if single_origin:
        extra_origins = f"{extra_origins},{single_origin}" if extra_origins else single_origin

    for origin in [item.strip() for item in extra_origins.split(",") if item.strip()]:
        if origin not in origins:
            origins.append(origin)

    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "股票後端 API 已啟動"}


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {
        "status": "ok",
        "database": db_status,
        "pandas_ta": ta is not None,
    }


def to_chart_payload(row: StockKline):
    # 把資料庫的 ORM 物件轉成前端圖表比較好使用的 JSON 格式。
    return {
        "time": row.date.strftime("%Y-%m-%d"),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "adj_open": row.adj_open,
        "adj_high": row.adj_high,
        "adj_low": row.adj_low,
        "adj_close": row.adj_close,
        "volume": row.volume,
    }


def is_etf_ticker(ticker: str) -> bool:
    """判斷是否為 ETF/ETN：代號以 00 開頭，或末碼為英文字母。"""
    import re as _re
    code = ticker.strip().upper().split(".", 1)[0]
    return code.startswith("00") or bool(_re.search(r"[A-Z]$", code))


def to_chart_payload_from_yf(row: RawKlineYFinance) -> dict:
    """將 raw_kline_yfinance ORM 列轉成圖表 payload。

    非 ETF 股票具有 adj_close，用以回推所有 adj OHLC 欄位，
    使長期 K 線在除權息後仍保持連續。
    """
    close = row.close or 0.0
    adj_close = row.adj_close
    if close and adj_close is not None:
        factor = adj_close / close
    else:
        factor = 1.0
        adj_close = row.close

    open_ = row.open or 0.0
    high = row.high or 0.0
    low = row.low or 0.0

    return {
        "time": row.date.strftime("%Y-%m-%d"),
        "open": round(open_ * factor, 4),
        "high": round(high * factor, 4),
        "low": round(low * factor, 4),
        "close": round(close * factor, 4),
        "adj_open": round(open_ * factor, 4),
        "adj_high": round(high * factor, 4),
        "adj_low": round(low * factor, 4),
        "adj_close": round(adj_close, 4) if adj_close is not None else None,
        "volume": row.volume,
        "sma_2": row.sma_2,
        "sma_5": row.sma_5,
        "sma_10": row.sma_10,
        "sma_20": row.sma_20,
        "sma_30": row.sma_30,
        "sma_60": row.sma_60,
        "sma_120": row.sma_120,
        "sma_240": row.sma_240,
    }


def normalize_ticker_symbol(ticker: str) -> str:
    raw = ticker.strip().upper()
    if re.fullmatch(r"\d{4,6}", raw):
        return f"{raw}.TW"
    return raw


def normalize_ticker_for_etf(ticker: str) -> str:
    symbol = normalize_ticker_symbol(ticker)
    if "." in symbol:
        return symbol.split(".", 1)[0]
    return symbol


def build_report_links(ticker: str) -> list[dict]:
    code = normalize_ticker_for_etf(ticker)
    return [
        {
            "label": f"月營收（{code}）",
            "url": f"https://goodinfo.tw/tw/ShowSaleMonChart.asp?STOCK_ID={code}",
        },
        {
            "label": f"季財報（{code}）",
            "url": f"https://goodinfo.tw/tw/StockFinDetail.asp?RPT_CAT=M_QUAR&STOCK_ID={code}",
        },
        {
            "label": f"公司總覽（{code}）",
            "url": f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={code}",
        },
        {
            "label": "MOPS 官方首頁",
            "url": "https://mops.twse.com.tw/mops/web/index",
        },
    ]


def fetch_ticker_codes_from_isin(str_mode: int) -> set[str]:
    response = requests.get(ISIN_URL, params={"strMode": str_mode}, timeout=20)
    response.raise_for_status()
    response.encoding = "big5"

    tables = pd.read_html(StringIO(response.text))
    if not tables:
        return set()

    df = tables[0].copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:]
    if "有價證券代號及名稱" not in df.columns:
        return set()

    codes: set[str] = set()
    for raw in df["有價證券代號及名稱"].astype(str):
        match = re.match(r"^(\d{4,6})", raw.strip())
        if not match:
            continue
        codes.add(match.group(1))

    return codes


def get_tw_total_ticker_count() -> int:
    updated_at = _UNIVERSE_CACHE.get("updated_at")
    cached_count = _UNIVERSE_CACHE.get("count")
    if isinstance(updated_at, datetime) and isinstance(cached_count, int):
        if datetime.utcnow() - updated_at < timedelta(hours=12):
            return cached_count

    listed = fetch_ticker_codes_from_isin(2)
    otc = fetch_ticker_codes_from_isin(4)
    total_count = len(listed.union(otc))

    _UNIVERSE_CACHE["count"] = total_count
    _UNIVERSE_CACHE["updated_at"] = datetime.utcnow()
    return total_count


_ALL_SMA_PERIODS = [2, 5, 10, 20, 30, 60, 120, 240]


def add_technical_indicators(
    df: pd.DataFrame,
    bb_length: int,
    bb_std: float,
    sar_step: float,
    sar_max: float,
) -> pd.DataFrame:
    # 優先使用 pandas-ta；若環境不支援則退回 pandas 計算，避免 K 線 API 直接失敗。

    result = df.copy()

    # 均線：若已從 DB 讀入（Path 1）則不重複計算；否則（Path 2/3）動態計算全部 8 条。
    if "sma_5" not in result.columns:
        for period in _ALL_SMA_PERIODS:
            result[f"sma_{period}"] = result["close"].rolling(window=period, min_periods=period).mean()

    if ta is not None:
        bbands_df = ta.bbands(result["close"], length=bb_length, std=bb_std)
        if bbands_df is not None:
            # pandas-ta 會依 length/std 組出欄名，例如 BBU_20_2.0。
            upper_col = next((col for col in bbands_df.columns if col.startswith("BBU_")), None)
            lower_col = next((col for col in bbands_df.columns if col.startswith("BBL_")), None)
            percent_b_col = next((col for col in bbands_df.columns if col.startswith("BBP_")), None)
            bandwidth_col = next((col for col in bbands_df.columns if col.startswith("BBB_")), None)

            result["bb_upper"] = bbands_df[upper_col] if upper_col else np.nan
            result["bb_lower"] = bbands_df[lower_col] if lower_col else np.nan
            result["bb_percent_b"] = bbands_df[percent_b_col] if percent_b_col else np.nan
            result["bb_bandwidth"] = bbands_df[bandwidth_col] if bandwidth_col else np.nan
        else:
            result["bb_upper"] = np.nan
            result["bb_lower"] = np.nan
            result["bb_percent_b"] = np.nan
            result["bb_bandwidth"] = np.nan

        psar_df = ta.psar(
            high=result["high"],
            low=result["low"],
            close=result["close"],
            af0=sar_step,
            af=sar_step,
            max_af=sar_max,
        )
        if psar_df is not None and not psar_df.empty:
            long_col = next((col for col in psar_df.columns if col.startswith("PSARl_")), None)
            short_col = next((col for col in psar_df.columns if col.startswith("PSARs_")), None)

            psar_long = psar_df[long_col] if long_col else pd.Series(np.nan, index=result.index)
            psar_short = psar_df[short_col] if short_col else pd.Series(np.nan, index=result.index)
            result["sar"] = psar_long.combine_first(psar_short)
        else:
            result["sar"] = np.nan
    else:
        rolling_mean = result["close"].rolling(window=bb_length, min_periods=bb_length).mean()
        rolling_std = result["close"].rolling(window=bb_length, min_periods=bb_length).std()
        bb_upper = rolling_mean + (bb_std * rolling_std)
        bb_lower = rolling_mean - (bb_std * rolling_std)
        bb_range = (bb_upper - bb_lower).replace(0, np.nan)

        result["bb_upper"] = bb_upper
        result["bb_lower"] = bb_lower
        result["bb_percent_b"] = (result["close"] - bb_lower) / bb_range
        result["bb_bandwidth"] = (bb_range / rolling_mean.replace(0, np.nan)) * 100
        # pandas fallback 不實作 PSAR，避免在不支援環境中讓 API 失敗。
        result["sar"] = np.nan

    return result


def dataframe_to_json_records(df: pd.DataFrame) -> list[dict]:
    # FastAPI 不能直接序列化 NaN/Infinity；統一轉成 None。
    normalized_df = df.replace([np.inf, -np.inf], np.nan)
    safe_df = normalized_df.astype(object).where(pd.notna(normalized_df), None)
    return safe_df.to_dict(orient="records")


@app.get("/api/kline/{ticker}")
async def get_kline(
    ticker: str,
    bb_length: int = Query(default=20, ge=1),
    bb_std: float = Query(default=2.0, gt=0),
    sar_step: float = Query(default=0.02, gt=0),
    sar_max: float = Query(default=0.2, gt=0),
    db: Session = Depends(get_db),
):
    # 流程：
    # 1. 所有股票（含 ETF）→ 優先從 raw_kline_yfinance 讀取（有 adj_close，還原後顯示）
    # 2. 找不到時 → 從 stock_kline 讀取（FinMind 原始資料，ETF 舊資料在此）
    # 3. 兩張表都沒資料 → 即時呼叫 yfinance 並存入 raw_kline_yfinance
    _CORE_COLS = ["open", "high", "low", "close", "volume"]

    try:
        ticker = normalize_ticker_symbol(ticker)

        # --- 路徑 1：查詢 raw_kline_yfinance(_focus)（所有股票、ETF 均適用）---
        kline_table = resolve_table_name(db, "raw_kline_yfinance")
        print(f"[kline] 查詢 {kline_table}：{ticker}")
        yf_rows = db.execute(
            text(
                f"""
                SELECT date, open, high, low, close, adj_close, volume,
                       sma_2, sma_5, sma_10, sma_20, sma_30, sma_60, sma_120, sma_240
                FROM {kline_table}
                WHERE ticker = :ticker
                ORDER BY date ASC
                """
            ),
            {"ticker": ticker},
        ).fetchall()
        if yf_rows:
            data = []
            for row in yf_rows:
                close = row.close or 0.0
                adj_close = row.adj_close
                factor = (adj_close / close) if (close and adj_close is not None) else 1.0
                if adj_close is None:
                    adj_close = row.close

                open_ = row.open or 0.0
                high = row.high or 0.0
                low = row.low or 0.0

                data.append(
                    {
                        "time": row.date.strftime("%Y-%m-%d"),
                        "open": round(open_ * factor, 4),
                        "high": round(high * factor, 4),
                        "low": round(low * factor, 4),
                        "close": round(close * factor, 4),
                        "adj_open": round(open_ * factor, 4),
                        "adj_high": round(high * factor, 4),
                        "adj_low": round(low * factor, 4),
                        "adj_close": round(adj_close, 4) if adj_close is not None else None,
                        "volume": row.volume,
                        "sma_2": row.sma_2,
                        "sma_5": row.sma_5,
                        "sma_10": row.sma_10,
                        "sma_20": row.sma_20,
                        "sma_30": row.sma_30,
                        "sma_60": row.sma_60,
                        "sma_120": row.sma_120,
                        "sma_240": row.sma_240,
                    }
                )
            df = pd.DataFrame(data)
            df.dropna(subset=_CORE_COLS, inplace=True)
            if not df.empty:
                df = add_technical_indicators(
                    df,
                    bb_length=bb_length,
                    bb_std=bb_std,
                    sar_step=sar_step,
                    sar_max=sar_max,
                )
                print(f"[kline] {ticker} 從 {kline_table} 回傳 {len(df)} 筆")
                return dataframe_to_json_records(df)

        # --- 路徑 2：raw_kline_yfinance 無資料，查詢 stock_kline（FinMind 舊資料）---
        print(f"[kline] 查詢 stock_kline：{ticker}")
        db_rows = (
            db.query(StockKline)
            .filter(StockKline.ticker == ticker)
            .order_by(StockKline.date.asc())
            .all()
        )
        if db_rows:
            data = [to_chart_payload(row) for row in db_rows]
            df = pd.DataFrame(data)
            df.dropna(subset=_CORE_COLS, inplace=True)
            if not df.empty:
                df = add_technical_indicators(
                    df,
                    bb_length=bb_length,
                    bb_std=bb_std,
                    sar_step=sar_step,
                    sar_max=sar_max,
                )
                print(f"[kline] {ticker} 從 stock_kline 回傳 {len(df)} 箆")
                return dataframe_to_json_records(df)

        # --- 路徑 3：DB 完全沒資料，即時從 yfinance 抓，存入 raw_kline_yfinance ---
        print(f"[kline] DB 無資料，即時從 yfinance 抓取：{ticker}")
        raw = yf.Ticker(ticker).history(period="6mo", auto_adjust=False, actions=True)
        if raw is None or raw.empty:
            raise HTTPException(status_code=404, detail="找不到該股票資料")

        raw.reset_index(inplace=True)
        insert_rows: list[RawKlineYFinance] = []
        response_data: list[dict] = []
        fetched_at = datetime.now(timezone.utc)

        for _, row in raw.iterrows():
            row_date: date = row["Date"].date()
            open_price = float(row["Open"])
            high_price = float(row["High"])
            low_price = float(row["Low"])
            close_price = float(row["Close"])
            adj_close_price = float(row.get("Adj Close") or row["Close"])
            volume_value = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            factor = adj_close_price / close_price if close_price else 1.0

            insert_rows.append(
                RawKlineYFinance(
                    ticker=ticker,
                    date=row_date,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    adj_close=adj_close_price,
                    volume=volume_value,
                    dividends=float(row["Dividends"]) if "Dividends" in row and not pd.isna(row["Dividends"]) else 0.0,
                    stock_splits=float(row["Stock Splits"]) if "Stock Splits" in row and not pd.isna(row["Stock Splits"]) else 0.0,
                    fetched_at=fetched_at,
                )
            )
            response_data.append(
                {
                    "time": row_date.strftime("%Y-%m-%d"),
                    "open": round(open_price * factor, 4),
                    "high": round(high_price * factor, 4),
                    "low": round(low_price * factor, 4),
                    "close": round(close_price * factor, 4),
                    "adj_open": round(open_price * factor, 4),
                    "adj_high": round(high_price * factor, 4),
                    "adj_low": round(low_price * factor, 4),
                    "adj_close": round(adj_close_price, 4),
                    "volume": volume_value,
                }
            )

        if insert_rows:
            db.add_all(insert_rows)
            db.commit()
        response_df = pd.DataFrame(response_data)
        response_df.dropna(subset=_CORE_COLS, inplace=True)
        if response_df.empty:
            raise HTTPException(status_code=404, detail="資料清理後為空")
        response_df = add_technical_indicators(
            response_df,
            bb_length=bb_length,
            bb_std=bb_std,
            sar_step=sar_step,
            sar_max=sar_max,
        )
        return dataframe_to_json_records(response_df)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/news/{ticker}")
async def get_news(
    ticker: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    symbol = normalize_ticker_symbol(ticker)
    symbol_without_suffix = symbol.split(".", 1)[0]

    rows = (
        db.query(NewsArticle)
        .filter(
            (NewsArticle.ticker == symbol)
            | (NewsArticle.ticker == symbol_without_suffix)
            | (NewsArticle.ticker.like(f"{symbol_without_suffix}.%"))
        )
        .order_by(func.date_trunc("minute", NewsArticle.published_at).desc(), NewsArticle.published_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "ticker": row.ticker,
            "title": row.title,
            "url": row.url,
            "source": row.source,
            "published_at": row.published_at.isoformat(),
        }
        for row in rows
    ]


@app.get("/api/etf-premium-discount/{ticker}")
async def get_etf_premium_discount(
    ticker: str,
    limit: int = Query(default=30, ge=1, le=120),
    db: Session = Depends(get_db),
):
    etf_symbol = normalize_ticker_for_etf(ticker)

    rows = (
        db.query(EtfPremiumDiscount)
        .filter(EtfPremiumDiscount.ticker == etf_symbol)
        .order_by(EtfPremiumDiscount.date.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "date": row.date.isoformat(),
            "ticker": row.ticker,
            "nav": row.nav,
            "market_price": row.market_price,
            "premium_discount_pct": row.premium_discount_pct,
        }
        for row in rows
    ]


@app.get("/api/financials/{ticker}")
async def get_financials(
    ticker: str,
    limit: int = Query(default=12, ge=1, le=36),
    db: Session = Depends(get_db),
):
    symbol = normalize_ticker_symbol(ticker)
    code = normalize_ticker_for_etf(ticker)

    monthly_table = resolve_table_name(db, "monthly_revenue")
    rows = db.execute(
        text(
            f"""
            SELECT ticker, company_name, revenue_year, revenue_month, revenue_amount,
                   previous_month_revenue, last_year_revenue, mom_pct, yoy_pct, source
            FROM {monthly_table}
            WHERE ticker IN (:symbol, :code_tw, :code_two, :code)
            ORDER BY revenue_year DESC, revenue_month DESC
            LIMIT :lim
            """
        ),
        {
            "symbol": symbol,
            "code_tw": f"{code}.TW",
            "code_two": f"{code}.TWO",
            "code": code,
            "lim": limit,
        },
    ).fetchall()

    # ── Quarterly financials from company_financials (EAV) ──────────────
    QUARTERLY_METRICS = ["Basic EPS", "Diluted EPS", "Net Income", "Total Revenue"]
    financials_table = resolve_table_name(db, "company_financials")
    qf_rows = db.execute(
        text(
            f"""
            SELECT report_date, metric, value
            FROM {financials_table}
            WHERE ticker = :code
              AND metric = ANY(:metrics)
            ORDER BY report_date DESC
            LIMIT :lim
            """
        ),
        {"code": code, "metrics": QUARTERLY_METRICS, "lim": limit * len(QUARTERLY_METRICS)},
    ).fetchall()

    # Pivot: group by report_date
    from collections import defaultdict
    qf_by_date: dict = defaultdict(dict)
    for r in qf_rows:
        qf_by_date[str(r.report_date)][r.metric] = r.value

    quarterly_financials = [
        {
            "report_date": d,
            "basic_eps": qf_by_date[d].get("Basic EPS"),
            "diluted_eps": qf_by_date[d].get("Diluted EPS"),
            "net_income": qf_by_date[d].get("Net Income"),
            "total_revenue": qf_by_date[d].get("Total Revenue"),
        }
        for d in sorted(qf_by_date.keys(), reverse=True)[:limit]
    ]

    return {
        "ticker": symbol,
        "report_links": build_report_links(symbol),
        "monthly_revenue": [
            {
                "ticker": row.ticker,
                "company_name": row.company_name,
                "revenue_year": row.revenue_year,
                "revenue_month": row.revenue_month,
                "revenue_amount": row.revenue_amount,
                "previous_month_revenue": row.previous_month_revenue,
                "last_year_revenue": row.last_year_revenue,
                "mom_pct": row.mom_pct,
                "yoy_pct": row.yoy_pct,
                "source": row.source,
            }
            for row in rows
        ],
        "quarterly_financials": quarterly_financials,
    }


@app.get("/api/pe/{ticker}")
async def get_pe_ratio(
    ticker: str,
    years: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """
    Return historical trailing-twelve-month (TTM) P/E ratio.
    Joins stock_kline (close price, ticker with .TW suffix) with
    company_financials Basic EPS (ticker without suffix, quarterly).
    """
    symbol = normalize_ticker_symbol(ticker)           # e.g. "2330.TW"
    code = normalize_ticker_for_etf(symbol)             # e.g. "2330"

    start_date = date.today() - timedelta(days=365 * years + 180)

    # ── 1. Fetch quarterly Basic EPS ──────────────────────────────────────
    financials_table = resolve_table_name(db, "company_financials")
    eps_rows = db.execute(
        text(
            f"""
            SELECT report_date, value
            FROM {financials_table}
            WHERE ticker = :code AND metric = 'Basic EPS'
            ORDER BY report_date
            """
        ),
        {"code": code},
    ).fetchall()

    if not eps_rows:
        raise HTTPException(status_code=404, detail=f"No EPS data found for {code}")

    # Build pandas Series for TTM rolling sum (4 quarters)
    eps_df = pd.DataFrame(eps_rows, columns=["report_date", "eps"])
    eps_df["report_date"] = pd.to_datetime(eps_df["report_date"])
    eps_df = eps_df.sort_values("report_date").set_index("report_date")
    # TTM EPS = sum of latest 4 quarters; forward-fill so every calendar day has a value
    eps_series = eps_df["eps"]
    ttm_series = eps_series.rolling(window=4, min_periods=4).sum()

    # ── 2. Fetch daily close prices ───────────────────────────────────────
    price_rows = db.execute(
        text(
            """
            SELECT date, close
            FROM stock_kline
            WHERE ticker = :sym AND date >= :start
            ORDER BY date
            """
        ),
        {"sym": symbol, "start": start_date},
    ).fetchall()

    if not price_rows:
        raise HTTPException(status_code=404, detail=f"No price data found for {symbol}")

    price_df = pd.DataFrame(price_rows, columns=["date", "close"])
    price_df["date"] = pd.to_datetime(price_df["date"])
    price_df = price_df.set_index("date")

    # ── 3. Align: for each trading day, find the most-recent TTM EPS ─────
    # Reindex ttm_series to daily, forward-fill quarterly values
    combined_idx = price_df.index.union(ttm_series.index)
    ttm_daily = ttm_series.reindex(combined_idx).ffill()
    ttm_on_price_dates = ttm_daily.reindex(price_df.index)

    price_df["ttm_eps"] = ttm_on_price_dates.values
    price_df = price_df.dropna(subset=["ttm_eps"])
    price_df = price_df[price_df["ttm_eps"] != 0]
    price_df["pe_ratio"] = price_df["close"] / price_df["ttm_eps"]
    # Clip extreme values (negative EPS → skip; extremely high → likely data error)
    price_df = price_df[price_df["ttm_eps"] > 0]

    result = [
        {
            "date": row.Index.strftime("%Y-%m-%d"),
            "close": round(float(row.close), 2),
            "ttm_eps": round(float(row.ttm_eps), 4),
            "pe_ratio": round(float(row.pe_ratio), 2),
        }
        for row in price_df.itertuples()
    ]

    return {"ticker": symbol, "data": result}


@app.get("/api/data-integrity/coverage")
def get_data_coverage(db: Session = Depends(get_db)):
    downloaded_count = db.query(StockKline.ticker).distinct().count()
    total_count = get_tw_total_ticker_count()
    coverage_pct = (downloaded_count / total_count * 100) if total_count else 0.0
    return {
        "downloaded_count": downloaded_count,
        "total_count": total_count,
        "coverage_pct": round(coverage_pct, 2),
    }


@app.get("/api/data-integrity/unverified")
def get_unverified_records(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(StockKline)
        .filter(StockKline.is_verified.is_(False))
        .order_by(StockKline.date.desc(), StockKline.ticker.asc())
        .limit(limit)
        .all()
    )

    return [
        {
            "ticker": row.ticker,
            "date": row.date.isoformat(),
            "verification_error_pct": row.verification_error_pct,
            "error_tag": row.error_tag,
            "data_source": row.data_source,
        }
        for row in rows
    ]


@app.post("/api/data-integrity/refetch-unverified")
def refetch_unverified_records(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    targets = (
        db.query(StockKline)
        .filter(StockKline.is_verified.is_(False))
        .order_by(StockKline.date.desc())
        .limit(limit)
        .all()
    )

    if not targets:
        return {"refetched_rows": 0, "failed_tickers": []}

    grouped: dict[str, set[date]] = {}
    for row in targets:
        grouped.setdefault(row.ticker, set()).add(row.date)

    refetched_rows = 0
    failed_tickers: list[str] = []

    for ticker, date_set in grouped.items():
        try:
            start_date = min(date_set)
            end_date = max(date_set) + timedelta(days=1)
            yf_df = yf.Ticker(ticker).history(
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                auto_adjust=False,
                actions=True,
            )
            if yf_df.empty:
                failed_tickers.append(ticker)
                continue

            yf_df.dropna(inplace=True)
            if yf_df.empty:
                failed_tickers.append(ticker)
                continue

            yf_df.reset_index(inplace=True)
            payload: list[dict] = []
            for _, row in yf_df.iterrows():
                row_date = row["Date"].date()
                if row_date not in date_set:
                    continue

                payload.append(
                    {
                        "ticker": ticker,
                        "date": row_date,
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "adj_open": float(row["Open"]) * (
                            float(row.get("Adj Close", row["Close"])) / float(row["Close"])
                            if float(row["Close"])
                            else 1.0
                        ),
                        "adj_high": float(row["High"]) * (
                            float(row.get("Adj Close", row["Close"])) / float(row["Close"])
                            if float(row["Close"])
                            else 1.0
                        ),
                        "adj_low": float(row["Low"]) * (
                            float(row.get("Adj Close", row["Close"])) / float(row["Close"])
                            if float(row["Close"])
                            else 1.0
                        ),
                        "adj_close": float(row.get("Adj Close", row["Close"])),
                        "volume": float(row["Volume"]),
                        "is_verified": None,
                        "data_source": "yfinance_refetch",
                        "error_tag": "refetched",
                    }
                )

            if not payload:
                failed_tickers.append(ticker)
                continue

            stmt = insert(StockKline).values(payload)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ticker_date",
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "adj_open": stmt.excluded.adj_open,
                    "adj_high": stmt.excluded.adj_high,
                    "adj_low": stmt.excluded.adj_low,
                    "adj_close": stmt.excluded.adj_close,
                    "volume": stmt.excluded.volume,
                    "is_verified": stmt.excluded.is_verified,
                    "data_source": stmt.excluded.data_source,
                    "error_tag": stmt.excluded.error_tag,
                },
            )
            result = db.execute(stmt)
            db.commit()
            refetched_rows += result.rowcount or 0
        except Exception:
            db.rollback()
            failed_tickers.append(ticker)

    return {"refetched_rows": refetched_rows, "failed_tickers": failed_tickers}