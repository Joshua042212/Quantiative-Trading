from datetime import date, datetime, timedelta
from io import StringIO
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
    from .models import CrawlLog, EtfPremiumDiscount, MonthlyRevenue, NewsArticle, StockKline
except ImportError:
    from database import Base, engine, get_db
    from models import CrawlLog, EtfPremiumDiscount, MonthlyRevenue, NewsArticle, StockKline

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


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
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


def normalize_ticker_symbol(ticker: str) -> str:
    return ticker.strip().upper()


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


def add_technical_indicators(
    df: pd.DataFrame,
    bb_length: int,
    bb_std: float,
    sar_step: float,
    sar_max: float,
) -> pd.DataFrame:
    # 優先使用 pandas-ta；若環境不支援則退回 pandas 計算，避免 K 線 API 直接失敗。

    result = df.copy()

    # 既有均線欄位保留，避免前端線圖失效。
    result["sma_5"] = result["close"].rolling(window=5, min_periods=5).mean()
    result["sma_10"] = result["close"].rolling(window=10, min_periods=10).mean()
    result["sma_20"] = result["close"].rolling(window=20, min_periods=20).mean()
    result["sma_60"] = result["close"].rolling(window=60, min_periods=60).mean()

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
    # 這個 API 的流程是：
    # 1. 先用 ticker 去資料庫找是否已有歷史 K 線
    # 2. 如果有，就直接回傳，速度最快
    # 3. 如果沒有，才用 yfinance 抓資料
    # 4. 抓到後寫進資料庫，下次同一支股票就可以直接從 DB 讀
    try:
        ticker = normalize_ticker_symbol(ticker)

        # 1. 先查 PostgreSQL
        print("正在查詢資料庫...")
        db_rows = (
            db.query(StockKline)
            .filter(StockKline.ticker == ticker)
            .order_by(StockKline.date.asc())
            .all()
        )
        data = [to_chart_payload(row) for row in db_rows]
        print(f"查詢完成，共 {len(data)} 筆")
        if db_rows:
            df = pd.DataFrame(data)
            df.dropna(inplace=True)
            if df.empty:
                raise HTTPException(status_code=404, detail="資料清理後為空")

            df = add_technical_indicators(
                df,
                bb_length=bb_length,
                bb_std=bb_std,
                sar_step=sar_step,
                sar_max=sar_max,
            )
            return dataframe_to_json_records(df)

        # 2. DB 沒資料才抓 yfinance
        # 明確保留原始 OHLC，並同時拿到調整後收盤價供分析用途。
        df = yf.Ticker(ticker).history(period="6mo", auto_adjust=False, actions=True)
        if df.empty:
            raise HTTPException(status_code=404, detail="找不到該股票資料")

        # 3. 清理髒資料
        # dropna 會刪掉有缺值的列，避免後面轉 float 或寫進 DB 時失敗。
        df.dropna(inplace=True)
        if df.empty:
            raise HTTPException(status_code=404, detail="資料清理後為空")

        # 4. 存入資料庫並組回傳格式
        # yfinance 回來的 Date 常常在 index，所以先攤平成一般欄位。
        df.reset_index(inplace=True)
        rows_to_insert = []
        response_data = []

        for _, row in df.iterrows():
            # 每次迴圈都把一列 dataframe 轉成：
            # 1. 一筆要存進資料庫的 StockKline
            # 2. 一筆要回傳給前端圖表的 dict
            row_date: date = row["Date"].date()
            open_price = float(row["Open"])
            high_price = float(row["High"])
            low_price = float(row["Low"])
            close_price = float(row["Close"])
            adj_close_price = float(row.get("Adj Close", row["Close"]))
            volume_value = float(row["Volume"])
            adjustment_factor = adj_close_price / close_price if close_price else 1.0
            adj_open_price = open_price * adjustment_factor
            adj_high_price = high_price * adjustment_factor
            adj_low_price = low_price * adjustment_factor

            model = StockKline(
                ticker=ticker,
                date=row_date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                adj_open=adj_open_price,
                adj_high=adj_high_price,
                adj_low=adj_low_price,
                adj_close=adj_close_price,
                volume=volume_value,
            )
            rows_to_insert.append(model)

            response_data.append(
                {
                    "time": row_date.strftime("%Y-%m-%d"),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "adj_open": adj_open_price,
                    "adj_high": adj_high_price,
                    "adj_low": adj_low_price,
                    "adj_close": adj_close_price,
                    "volume": volume_value,
                }
            )

        if rows_to_insert:
            # add_all 是批次加入 session，commit 才會真的寫入 PostgreSQL。
            db.add_all(rows_to_insert)
            db.commit()

        response_df = pd.DataFrame(response_data)
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
        # 只要資料庫交易過程出錯，就 rollback，避免資料只寫一半。
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

    rows = (
        db.query(MonthlyRevenue)
        .filter(
            (MonthlyRevenue.ticker == symbol)
            | (MonthlyRevenue.ticker == f"{code}.TW")
            | (MonthlyRevenue.ticker == f"{code}.TWO")
            | (MonthlyRevenue.ticker == code)
        )
        .order_by(MonthlyRevenue.revenue_year.desc(), MonthlyRevenue.revenue_month.desc())
        .limit(limit)
        .all()
    )

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
    }


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