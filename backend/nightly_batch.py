from __future__ import annotations

"""夜間批次核心邏輯。

這支程式主要負責三件事：
1. 同步上市/上櫃/興櫃公司的基本資料。
2. 補抓或刷新股票 K 線歷史資料。
3. 下載公司財報欄位與官網網址。

目前建議從 market_data_sync.py 啟動，這個檔案保留核心實作。
註解以「資料來源 → 清洗 → 寫回資料庫」的流程來標示，方便後續除錯。
目前建議從 backend/crawlers/data_crawler.py 啟動，這個檔案保留核心實作。
"""

import argparse
import csv
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from FinMind.data import DataLoader
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

try:
    from .database import Base, SessionLocal, engine
    from .models import CompanyFinancials, CompanyInfo, RawKlineYFinance, StockKline
except ImportError:
    from database import Base, SessionLocal, engine
    from models import CompanyFinancials, CompanyInfo, RawKlineYFinance, StockKline

# 各市場公司清單來源，夜間批次會依序下載這些公開 CSV。
CSV_SOURCES: dict[str, str] = {
    "listed": "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
    "otc": "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
    "emerging": "https://mopsfin.twse.com.tw/opendata/t187ap03_R.csv",
}

# 台灣證交所 ETF 上市清單頁面。
ETF_LIST_URL = "https://www.twse.com.tw/zh/products/securities/etf/products/list.html"

# 不同來源檔案的欄位名稱可能略有差異，因此先定義候選欄位名稱。
COLUMN_CANDIDATES: dict[str, list[str]] = {
    "ticker": ["公司代號"],
    "short_name": ["公司簡稱"],
    "company_name": ["公司名稱"],
    "sector": ["產業別"],
    "capital": ["實收資本額"],
}

# FinMind 作為台股 K 線主要來源；財報階段仍保留 yfinance。
api = DataLoader()
FINMIND_START_DATE = "2014-01-01"
FINMIND_FINANCIAL_START_DATE = "2014-01-01"
FINMIND_BAN_SLEEP_SECONDS = 30 * 60
YFINANCE_BAN_SLEEP_SECONDS = 30 * 60
FAILED_LOG_FILE = Path(__file__).resolve().parent / "logs" / "nightly_failed_downloads.csv"


class FinMindIpBannedError(RuntimeError):
    """FinMind API 因流量限制封鎖當前 IP。"""


class YFinanceIpBannedError(RuntimeError):
    """yfinance 因流量限制封鎖當前 IP（HTTP 429 Too Many Requests）。"""


def ensure_failed_download_log_file() -> None:
    """確保失敗紀錄檔存在，方便後續固定位置查詢。"""
    FAILED_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if FAILED_LOG_FILE.exists():
        return

    with FAILED_LOG_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp_utc", "stage", "ticker", "reason"])


def append_failed_download_log(stage: str, ticker: str, reason: str) -> None:
    """把下載失敗寫入持久化 CSV，方便隔天回看與重跑。"""
    try:
        FAILED_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        need_header = not FAILED_LOG_FILE.exists()
        with FAILED_LOG_FILE.open("a", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            if need_header:
                writer.writerow(["timestamp_utc", "stage", "ticker", "reason"])
            writer.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    stage,
                    ticker,
                    str(reason).replace("\n", " ").strip(),
                ]
            )
    except Exception as exc:
        print(f"[Log][Warn] 失敗紀錄寫入 CSV 失敗：{exc}")


def configure_finmind_auth() -> None:
    """若有提供 Token，先登入 FinMind，避免匿名額度限制。"""
    token = os.getenv("FINMIND_API_TOKEN", "").strip()
    if not token:
        print("[FinMind][Warn] 未設定 FINMIND_API_TOKEN，將以匿名模式呼叫 API")
        return

    try:
        api.login_by_token(api_token=token)
        print("[FinMind] 已使用 FINMIND_API_TOKEN 登入")
    except Exception as exc:
        print(f"[FinMind][Warn] Token 登入失敗，改用匿名模式：{exc}")


def ensure_runtime_schema() -> None:
    """啟動時確認資料表與必要欄位存在，避免批次在半路因 schema 缺漏失敗。"""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE company_info ADD COLUMN IF NOT EXISTS website VARCHAR(1024)"))
        conn.execute(
            text(
                """
                ALTER TABLE stock_kline
                ADD COLUMN IF NOT EXISTS adj_open DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS adj_high DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS adj_low DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS adj_close DOUBLE PRECISION
                """
            )
        )
        # 允許 adj_close 為 NULL（欄位已存在且非 NULL 時才需要執行）
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'stock_kline'
                          AND column_name = 'adj_close'
                          AND is_nullable = 'NO'
                    ) THEN
                        ALTER TABLE stock_kline ALTER COLUMN adj_close DROP NOT NULL;
                    END IF;
                END
                $$;
                """
            )
        )


def normalize_column_name(name: Any) -> str:
    """清理 CSV 欄名中的 BOM 或前後空白。"""
    return str(name).replace("\ufeff", "").strip()


def first_existing_column(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    """依候選欄位名稱順序，找到第一個可用欄位。"""
    for candidate in candidates:
        if candidate in df.columns:
            return df[candidate]
    return pd.Series([None] * len(df), index=df.index, dtype="object")


def parse_capital(value: Any) -> int | None:
    """把資本額欄位轉成整數，若格式異常則回傳 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    digits = re.sub(r"[^\d-]", "", str(value))
    if not digits or digits == "-":
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def normalize_base_ticker(ticker: str) -> str:
    """將代號正規化，只保留主代號本體。"""
    return str(ticker).strip().upper().split(".", 1)[0]


def build_yfinance_symbol(ticker: str, market: str | None) -> str:
    """依市場別補上 yfinance 需要的尾碼，例如 .TW 或 .TWO。"""
    raw = str(ticker).strip().upper()
    if "." in raw:
        return raw

    base = normalize_base_ticker(raw)
    if market in ("listed", "etf"):
        return f"{base}.TW"
    return f"{base}.TWO"


def fetch_etf_list_from_twse() -> list[dict[str, Any]]:
    """從 TWSE ISIN 資料庫抓取所有上市 ETF 代號與名稱。

    ETF 代號格式：以 00 開頭，4~6 碼，可能含英文字母尾碼（如 00625K、00631L）。
    資料來源與 main.py 的 fetch_ticker_codes_from_isin 相同。
    """
    from io import StringIO as _StringIO

    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        resp = requests.get(url, timeout=30, headers=headers)
        resp.raise_for_status()
        resp.encoding = "big5"
        tables = pd.read_html(_StringIO(resp.text))
    except Exception as exc:
        raise RuntimeError(f"[ETF] 無法從 ISIN 資料庫取得 ETF 清單：{exc}") from exc

    if not tables:
        raise RuntimeError("[ETF] ISIN 頁面無法解析")

    df = tables[0].copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:]

    if "有價證券代號及名稱" not in df.columns:
        raise RuntimeError(f"[ETF] ISIN 頁面欄位異動：{list(df.columns)}")

    # ETF 代號以 00 開頭，可能含字母尾碼
    etf_pattern = re.compile(r"^(00[0-9]{2,4}[A-Z0-9]*)\s*[\s　](.+)$")

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in df["有價證券代號及名稱"].astype(str):
        raw = raw.strip()
        m = etf_pattern.match(raw)
        if not m:
            continue
        ticker = m.group(1).strip().upper()
        name = m.group(2).strip()
        if ticker in seen:
            continue
        seen.add(ticker)
        results.append(
            {
                "ticker": ticker,
                "short_name": name,
                "company_name": name,
                "sector": "ETF",
                "capital": None,
                "market": "etf",
            }
        )

    print(f"[ETF] 從 ISIN 資料庫取得 {len(results)} 支上市 ETF")
    return results


def is_probably_etf_or_etn(ticker: str) -> bool:
    """粗略判斷是否為 ETF/ETN，避免去抓原本就不會有季報的商品。"""
    code = normalize_base_ticker(ticker)
    return code.startswith("00") or bool(re.search(r"[A-Z]$", code))


def read_company_csv(url: str, market: str) -> pd.DataFrame:
    """下載單一市場的公司清單，並整理成統一欄位格式。"""
    last_error: Exception | None = None

    # 公開資料常出現不同編碼，因此逐一嘗試直到成功為止。
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            df = pd.read_csv(
                url,
                dtype=str,
                encoding=encoding,
                encoding_errors="ignore",
                on_bad_lines="skip",
                engine="python",
            )
            df.columns = [normalize_column_name(col) for col in df.columns]

            # 將各種原始欄位映射到系統內固定欄位名稱。
            cleaned = pd.DataFrame(
                {
                    "ticker": first_existing_column(df, COLUMN_CANDIDATES["ticker"]),
                    "short_name": first_existing_column(df, COLUMN_CANDIDATES["short_name"]),
                    "company_name": first_existing_column(df, COLUMN_CANDIDATES["company_name"]),
                    "sector": first_existing_column(df, COLUMN_CANDIDATES["sector"]),
                    "capital": first_existing_column(df, COLUMN_CANDIDATES["capital"]),
                }
            )

            # 基本清洗：統一代號格式、去除空白、過濾無效資料。
            cleaned = cleaned.fillna("")
            cleaned["ticker"] = cleaned["ticker"].astype(str).str.strip().str.upper()
            cleaned = cleaned[cleaned["ticker"].str.match(r"^[0-9A-Z]{4,6}$", na=False)].copy()
            cleaned["short_name"] = cleaned["short_name"].astype(str).str.strip()
            cleaned["company_name"] = cleaned["company_name"].astype(str).str.strip()
            cleaned["sector"] = cleaned["sector"].astype(str).str.strip()
            cleaned["capital"] = cleaned["capital"].apply(parse_capital)
            cleaned["market"] = market
            cleaned = cleaned.drop_duplicates(subset=["ticker"], keep="last")
            return cleaned
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed to read CSV from {url}: {last_error}")


def upsert_company_info_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    """把公司基本資料批次寫回資料庫；若已存在則更新。"""
    if not rows:
        return 0

    stmt = insert(CompanyInfo).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyInfo.ticker],
        set_={
            "short_name": stmt.excluded.short_name,
            "company_name": stmt.excluded.company_name,
            "sector": stmt.excluded.sector,
            "capital": stmt.excluded.capital,
            "market": stmt.excluded.market,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def sync_company_info_from_csv(db: Session) -> int:
    """彙整所有市場的公司清單（含 ETF）並同步到 company_info。"""
    frames: list[pd.DataFrame] = []

    # 逐一下載上市、上櫃、興櫃資料。
    for market, url in CSV_SOURCES.items():
        print(f"[CompanyInfo] 下載 {market} CSV: {url}")
        frame = read_company_csv(url, market)
        print(f"[CompanyInfo] {market} 載入 {len(frame)} 筆")
        frames.append(frame)

    # 另外抓取台灣證交所 ETF 上市清單。
    try:
        etf_records = fetch_etf_list_from_twse()
        if etf_records:
            frames.append(pd.DataFrame(etf_records))
    except Exception as exc:
        print(f"[CompanyInfo][Warn] ETF 清單下載失敗，略過：{exc}")

    # 合併後再去重，ETF 代號優先（keep='last' 配合 frames 順序）。
    all_companies = pd.concat(frames, ignore_index=True)
    all_companies = all_companies.drop_duplicates(subset=["ticker"], keep="last")

    records = all_companies.to_dict(orient="records")
    written = upsert_company_info_rows(db, records)
    print(f"[CompanyInfo] 完成更新，共寫入 {written} 筆")
    return written


def get_company_universe(db: Session) -> list[dict[str, str | None]]:
    """取得目前資料庫中的公司代號清單，作為後續批次母體。"""
    rows = db.query(CompanyInfo.ticker, CompanyInfo.market).order_by(CompanyInfo.ticker.asc()).all()
    return [{"ticker": row[0], "market": row[1]} for row in rows]


def get_existing_kline_tickers(db: Session) -> set[str]:
    """找出已存在 K 線資料的代號，用於斷點續傳。"""
    rows = db.execute(text("SELECT DISTINCT ticker FROM stock_kline")).fetchall()
    return {normalize_base_ticker(row[0]) for row in rows if row[0]}


def get_refresh_target_tickers(db: Session, failed_only: bool = False) -> set[str]:
    if failed_only:
        rows = db.execute(
            text(
                """
                SELECT DISTINCT ticker
                FROM stock_kline
                WHERE is_verified = FALSE OR is_verified IS NULL
                """
            )
        ).fetchall()
    else:
        rows = db.execute(text("SELECT DISTINCT ticker FROM stock_kline")).fetchall()

    return {normalize_base_ticker(row[0]) for row in rows if row[0]}


def delete_existing_kline_for_symbol(db: Session, raw_ticker: str, yf_symbol: str) -> int:
    result = db.execute(
        text(
            """
            DELETE FROM stock_kline
            WHERE ticker = :raw_ticker OR ticker = :yf_symbol
            """
        ),
        {"raw_ticker": raw_ticker, "yf_symbol": yf_symbol},
    )
    db.commit()
    return result.rowcount or 0


def get_existing_financial_tickers(db: Session) -> set[str]:
    rows = db.execute(text("SELECT DISTINCT ticker FROM company_financials")).fetchall()
    return {normalize_base_ticker(row[0]) for row in rows if row[0]}


def upsert_stock_history_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    stmt = insert(StockKline).values(rows)
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
    return result.rowcount or 0


def _normalize_finmind_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """把 FinMind 回傳欄位整理成系統使用的標準欄位（僅原始價，不含還原價）。"""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.date
    trading_volume = normalized["Trading_Volume"] if "Trading_Volume" in normalized.columns else 0

    normalized["open"] = pd.to_numeric(normalized["open"], errors="coerce")
    normalized["high"] = pd.to_numeric(normalized["max"], errors="coerce")
    normalized["low"] = pd.to_numeric(normalized["min"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized["volume"] = pd.to_numeric(trading_volume, errors="coerce").fillna(0)
    return normalized[["date", "open", "high", "low", "close", "volume"]]


def is_finmind_ip_banned(stock_id: str, start_date: str, end_date: str) -> bool:
    """直接檢查 FinMind HTTP 回應，判斷是否為 ip banned。"""
    token = os.getenv("FINMIND_API_TOKEN", "").strip()
    if not token:
        return False

    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={
                "dataset": "TaiwanStockPrice",
                "data_id": stock_id,
                "start_date": start_date,
                "end_date": end_date,
                "token": token,
            },
            timeout=30,
        )
        payload = response.json()
    except Exception:
        return False

    if response.status_code == 403 and isinstance(payload, dict):
        message = str(payload.get("msg", "")).strip().lower()
        return message == "ip banned"

    return False


def fetch_finmind_history(stock_id: str, start_date: str = FINMIND_START_DATE) -> pd.DataFrame:
    """使用 FinMind 抓取台股日 K 原始價格（不含還原價，還原價改由其他來源補充）。"""
    end_date = datetime.now().date().isoformat()
    raw_error: Exception | None = None
    raw_history = pd.DataFrame()

    try:
        raw_history = api.taiwan_stock_daily(stock_id=stock_id, start_date=start_date, end_date=end_date)
    except Exception as exc:
        raw_error = exc

    raw = _normalize_finmind_frame(raw_history)

    if raw.empty:
        if is_finmind_ip_banned(stock_id, start_date, end_date):
            raise FinMindIpBannedError(f"FinMind ip banned while downloading {stock_id}")
        raise RuntimeError(f"FinMind returned no history for {stock_id}; raw_error={raw_error}")

    raw["volume"] = raw["volume"].fillna(0)
    raw = raw.dropna(subset=["date", "open", "high", "low", "close"])
    raw = raw.sort_values("date").reset_index(drop=True)
    return raw


def fetch_finmind_financials(stock_id: str, start_date: str = FINMIND_FINANCIAL_START_DATE) -> pd.DataFrame:
    """使用 FinMind 抓取單一股票財報欄位，回傳標準化欄位。"""
    end_date = datetime.now().date().isoformat()
    data = pd.DataFrame()
    errors: list[str] = []

    # 不同 FinMind 版本函式名稱可能不同，依序嘗試。
    candidate_methods = [
        "taiwan_stock_financial_statement",
        "taiwan_stock_financial_statements",
    ]

    for method_name in candidate_methods:
        method = getattr(api, method_name, None)
        if method is None:
            continue

        try:
            data = method(stock_id=stock_id, start_date=start_date, end_date=end_date)
            if isinstance(data, pd.DataFrame) and not data.empty:
                break
        except TypeError:
            try:
                data = method(stock_id=stock_id, start_date=start_date)
                if isinstance(data, pd.DataFrame) and not data.empty:
                    break
            except Exception as exc:  # pragma: no cover - network/runtime fallback
                errors.append(f"{method_name}(no_end_date): {exc}")
        except Exception as exc:  # pragma: no cover - network/runtime fallback
            errors.append(f"{method_name}: {exc}")

    if data is None or data.empty:
        detail = "; ".join(errors) if errors else "no_data"
        raise RuntimeError(f"FinMind returned no financials for {stock_id}; details={detail}")

    normalized = data.copy()
    if "date" not in normalized.columns or "value" not in normalized.columns:
        raise RuntimeError(f"FinMind financials schema changed for {stock_id}; columns={list(normalized.columns)}")

    normalized["report_date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
    metric_series = normalized["origin_name"] if "origin_name" in normalized.columns else pd.Series(index=normalized.index, dtype="object")
    if "type" in normalized.columns:
        metric_series = metric_series.fillna(normalized["type"])
    if "item" in normalized.columns:
        metric_series = metric_series.fillna(normalized["item"])
    normalized["metric"] = metric_series.astype(str).str.strip()
    normalized["value"] = pd.to_numeric(normalized["value"], errors="coerce")

    normalized = normalized.dropna(subset=["report_date", "value"])
    normalized = normalized[normalized["metric"].astype(bool)]
    return normalized[["report_date", "metric", "value"]]


SMA_PERIODS: list[int] = [2, 5, 10, 20, 30, 60, 120, 240]
"""前端均線選單所有可選週期，需全部預算並存入 raw_kline_yfinance。"""


def compute_and_store_sma(db: Session, ticker: str | None = None) -> int:
    """計算並寫入 raw_kline_yfinance 的 8 條 SMA（週期：2/5/10/20/30/60/120/240）。

    ticker=None 時處理全部股票（backfill 用）；
    指定 ticker 時只更新該支（K 線增量更新後呼叫）。
    回傳更新的列數合計。
    """
    from sqlalchemy import text

    if ticker:
        tickers: list[str] = [ticker]
    else:
        rows = db.execute(
            text("SELECT DISTINCT ticker FROM raw_kline_yfinance ORDER BY ticker")
        ).fetchall()
        tickers = [r[0] for r in rows]

    set_clause = ", ".join([f"sma_{p} = :sma_{p}" for p in SMA_PERIODS])
    update_sql = text(f"UPDATE raw_kline_yfinance SET {set_clause} WHERE id = :id")

    total_updated = 0
    for t in tickers:
        rows_data = db.execute(
            text(
                "SELECT id, close FROM raw_kline_yfinance "
                "WHERE ticker = :ticker ORDER BY date ASC"
            ),
            {"ticker": t},
        ).fetchall()

        if not rows_data:
            continue

        df = pd.DataFrame(rows_data, columns=["id", "close"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")

        for period in SMA_PERIODS:
            df[f"sma_{period}"] = df["close"].rolling(window=period, min_periods=period).mean()

        update_rows: list[dict] = []
        for _, row in df.iterrows():
            params: dict = {"id": int(row["id"])}
            for period in SMA_PERIODS:
                val = row[f"sma_{period}"]
                params[f"sma_{period}"] = None if pd.isna(val) else round(float(val), 6)
            update_rows.append(params)

        chunk_size = 500
        for offset in range(0, len(update_rows), chunk_size):
            db.execute(update_sql, update_rows[offset : offset + chunk_size])
            db.commit()

        total_updated += len(update_rows)

    return total_updated


def fetch_and_store_yfinance_raw(db: Session, yf_symbol: str) -> int:
    """從 yfinance 下載原始 K 線資料並寫入 raw_kline_yfinance。

    使用 auto_adjust=False 以保留未還原的 OHLCV 與獨立的 Adj Close 欄位。
    actions=True 額外取得 Dividends 與 Stock Splits 除權息事件。
    起始日期與 FinMind 對齊（FINMIND_START_DATE），方便日後比對。
    衝突（相同 ticker + date）時忽略，不覆蓋舊資料。
    """
    import math

    try:
        raw = yf.download(
            yf_symbol,
            start=FINMIND_START_DATE,
            auto_adjust=False,
            actions=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        # yfinance 有時在 429 或網路錯誤時直接拋出例外。
        exc_str = str(exc).lower()
        if "429" in exc_str or "too many requests" in exc_str or "rate limit" in exc_str:
            raise YFinanceIpBannedError(f"yfinance ip banned while downloading {yf_symbol}: {exc}") from exc
        raise

    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance 未回傳任何資料：{yf_symbol}")

    # yfinance >= 0.2 回傳多層欄位（MultiIndex），攤平成單層。
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0] for col in raw.columns]

    fetched_at = datetime.now(tz=timezone.utc)

    def _safe_float(val) -> float | None:
        try:
            f = float(val)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _safe_int(val) -> int | None:
        try:
            f = float(val)
            if math.isnan(f):
                return None
            return int(f)
        except (TypeError, ValueError):
            return None

    rows: list[dict] = []
    for idx_date, row in raw.iterrows():
        rows.append(
            {
                "ticker": yf_symbol,
                "date": pd.Timestamp(idx_date).date(),
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "adj_close": _safe_float(row.get("Adj Close")),
                "volume": _safe_int(row.get("Volume")),
                "dividends": _safe_float(row.get("Dividends")),
                "stock_splits": _safe_float(row.get("Stock Splits")),
                "fetched_at": fetched_at,
            }
        )

    if not rows:
        return 0

    chunk_size = 500
    total_written = 0
    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]
        stmt = insert(RawKlineYFinance).values(chunk)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_raw_yf_ticker_date")
        result = db.execute(stmt)
        db.commit()
        total_written += result.rowcount

    return total_written


def sync_kline_history(
    db: Session,
    force_refresh: bool = False,
    refresh_failed_only: bool = False,
    limit: int | None = None,
) -> int:
    """同步股票歷史 K 線。

    支援三種模式：
    1. 斷點續傳：只抓資料庫還沒有的代號。
    2. 強制刷新全部：先刪掉舊資料再重抓。
    3. 強制刷新失敗項目：只重抓曾失敗或未驗證的資料。
    """
    companies = get_company_universe(db)
    total = len(companies)

    # 先決定本次要處理哪些代號。
    if force_refresh:
        refresh_targets = get_refresh_target_tickers(db, failed_only=refresh_failed_only)
        if refresh_failed_only:
            pending = [
                item for item in companies if normalize_base_ticker(item["ticker"] or "") in refresh_targets
            ]
            mode_label = "強制刷新失敗/未驗證"
        else:
            pending = companies
            mode_label = "強制刷新全部"
    else:
        existing = get_existing_kline_tickers(db)
        pending = [item for item in companies if normalize_base_ticker(item["ticker"] or "") not in existing]
        mode_label = "斷點續傳"

    # 測試時可只處理前 N 檔，避免一次跑太久。
    if limit is not None and limit > 0:
        pending = pending[:limit]

    skipped = total - len(pending)
    print(f"[K-line] 模式={mode_label}；共 {total} 檔，已跳過 {skipped} 檔，準備下載 {len(pending)} 檔。")

    total_rows = 0

    for index, item in enumerate(pending, start=1):
        raw_ticker = normalize_base_ticker(item["ticker"] or "")
        market = item.get("market")
        yf_symbol = build_yfinance_symbol(raw_ticker, market)

        while True:
            try:
                print(f"[K-line] ({index}/{len(pending)}) 下載 {yf_symbol}")

                # 若本次是刷新模式，先把舊資料移除再重新寫回。
                if force_refresh:
                    deleted = delete_existing_kline_for_symbol(db, raw_ticker, yf_symbol)
                    if deleted:
                        print(f"[K-line] {yf_symbol} 已刪除舊資料 {deleted} 筆，準備重抓")

                # 使用 FinMind 下載台股日 K；原始價與還原價分別抓取後再依日期合併。
                history = fetch_finmind_history(raw_ticker, start_date=FINMIND_START_DATE)

                if history is None or history.empty:
                    print(f"[K-line][Warn] {yf_symbol} 無歷史資料，跳過")
                    append_failed_download_log("finmind_kline_no_data", yf_symbol, "no_history")
                    break

                rows: list[dict[str, Any]] = []

                for _, row in history.iterrows():
                    rows.append(
                        {
                            "ticker": yf_symbol,
                            "date": pd.to_datetime(row["date"]).date(),
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "adj_open": None,
                            "adj_high": None,
                            "adj_low": None,
                            "adj_close": None,
                            "volume": float(row.get("volume", 0.0) or 0.0),
                            "is_verified": None,
                            "data_source": "finmind",
                            "error_tag": None,
                            "has_anomaly": False,
                            "anomaly_reason": None,
                        }
                    )

                written = upsert_stock_history_rows(db, rows)
                total_rows += written
                print(f"[K-line] {yf_symbol} 完成，寫入 {written} 筆")

                # 同步抓取 yfinance 原始資料；遇到 IP 封鎖時暫停後重試，其他錯誤只 log。
                yf_retry = True
                while yf_retry:
                    yf_retry = False
                    try:
                        yf_written = fetch_and_store_yfinance_raw(db, yf_symbol)
                        print(f"[yf-raw] {yf_symbol} 寫入 {yf_written} 筆")
                    except YFinanceIpBannedError as yf_ban:
                        print(f"[yf-raw][Warn] {yf_ban}；自動暫停 {YFINANCE_BAN_SLEEP_SECONDS // 60} 分鐘後重試")
                        append_failed_download_log("yfinance_raw_ip_ban", yf_symbol, yf_ban)
                        time.sleep(YFINANCE_BAN_SLEEP_SECONDS)
                        yf_retry = True
                    except Exception as yf_exc:
                        print(f"[yf-raw][Warn] {yf_symbol} yfinance 原始資料失敗，跳過：{yf_exc}")
                        append_failed_download_log("yfinance_raw", yf_symbol, yf_exc)

                break
            except FinMindIpBannedError as exc:
                db.rollback()
                print(f"[K-line][Warn] {exc}；自動暫停 {FINMIND_BAN_SLEEP_SECONDS // 60} 分鐘後重試")
                append_failed_download_log("finmind_kline_ip_ban", yf_symbol, exc)
                time.sleep(FINMIND_BAN_SLEEP_SECONDS)
                continue
            except Exception as exc:
                # 單一代號失敗不影響整批，回滾後記錄錯誤原因。
                db.rollback()
                print(f"[K-line][Error] {yf_symbol} 下載失敗：{exc}")
                append_failed_download_log("finmind_kline", yf_symbol, exc)
                break
            finally:
                # 縮短延遲以加快全量下載速度；仍保留隨機抖動避免被封鎖。
                time.sleep(random.uniform(0.5, 1.5))

    return total_rows


def sync_yfinance_raw_only(db: Session, limit: int | None = None) -> int:
    """針對所有公司，補抓尚未有 raw_kline_yfinance 資料的代號。

    適用於 stock_kline 已有大量 FinMind 資料但 raw_kline_yfinance 尚未填滿的情況。
    遇到 IP 封鎖時自動暫停 30 分鐘後重試。
    """
    companies = get_company_universe(db)

    # 找出 raw_kline_yfinance 已有的 ticker（用 yf_symbol 格式比對）。
    existing_yf = {
        row[0]
        for row in db.execute(text("SELECT DISTINCT ticker FROM raw_kline_yfinance")).fetchall()
        if row[0]
    }

    pending = [
        item for item in companies
        if build_yfinance_symbol(normalize_base_ticker(item["ticker"] or ""), item.get("market")) not in existing_yf
    ]

    if limit is not None and limit > 0:
        pending = pending[:limit]

    total = len(pending)
    print(f"[yf-raw-sync] 共 {len(companies)} 檔，已有 {len(existing_yf)} 檔，準備補抓 {total} 檔。")

    total_written = 0
    for index, item in enumerate(pending, start=1):
        raw_ticker = normalize_base_ticker(item["ticker"] or "")
        market = item.get("market")
        yf_symbol = build_yfinance_symbol(raw_ticker, market)

        print(f"[yf-raw-sync] ({index}/{total}) 下載 {yf_symbol}")

        while True:
            try:
                written = fetch_and_store_yfinance_raw(db, yf_symbol)
                total_written += written
                print(f"[yf-raw-sync] {yf_symbol} 寫入 {written} 筆")
                break
            except YFinanceIpBannedError as exc:
                print(f"[yf-raw-sync][Warn] {exc}；自動暫停 {YFINANCE_BAN_SLEEP_SECONDS // 60} 分鐘後重試")
                append_failed_download_log("yfinance_raw_sync_ip_ban", yf_symbol, exc)
                time.sleep(YFINANCE_BAN_SLEEP_SECONDS)
                continue
            except Exception as exc:
                print(f"[yf-raw-sync][Warn] {yf_symbol} 失敗，跳過：{exc}")
                append_failed_download_log("yfinance_raw_sync", yf_symbol, exc)
                break
            finally:
                time.sleep(random.uniform(0.5, 1.5))

    return total_written


def upsert_company_website(db: Session, ticker: str, website: str | None) -> None:
    """把公司官網網址補回 company_info。"""
    if not website:
        return

    stmt = insert(CompanyInfo).values({"ticker": ticker, "website": website})
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyInfo.ticker],
        set_={"website": stmt.excluded.website},
    )
    db.execute(stmt)
    db.commit()


def upsert_financial_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    """將整理好的財報欄位批次寫入 company_financials。"""
    if not rows:
        return 0

    # 財報欄位很多，單次 INSERT 可能參數過大，改為分批寫入。
    chunk_size = 120
    total_written = 0

    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]

        stmt = insert(CompanyFinancials).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_company_financials_ticker_date_metric",
            set_={
                "value": stmt.excluded.value,
                "source": stmt.excluded.source,
                "updated_at": stmt.excluded.updated_at,
            },
        )

        try:
            result = db.execute(stmt)
            db.commit()
            total_written += result.rowcount or 0
        except Exception as exc:
            db.rollback()
            print(f"[Financials][Warn] chunk upsert 失敗，改逐筆寫入；offset={offset}, error={exc}")

            # 某些來源資料可能在同一批內有衝突，逐筆寫入可避免整批失敗。
            for row in chunk:
                row_stmt = insert(CompanyFinancials).values([row])
                row_stmt = row_stmt.on_conflict_do_update(
                    constraint="uq_company_financials_ticker_date_metric",
                    set_={
                        "value": row_stmt.excluded.value,
                        "source": row_stmt.excluded.source,
                        "updated_at": row_stmt.excluded.updated_at,
                    },
                )

                try:
                    row_result = db.execute(row_stmt)
                    db.commit()
                    total_written += row_result.rowcount or 0
                except Exception as row_exc:
                    db.rollback()
                    print(
                        "[Financials][Warn] 單筆寫入失敗，略過；"
                        f"ticker={row.get('ticker')}, report_date={row.get('report_date')}, metric={row.get('metric')}, error={row_exc}"
                    )

    return total_written


def sync_company_financials(db: Session) -> int:
    """同步公司季報資料（僅 yfinance），並順手補齊公司網站欄位。"""
    companies = get_company_universe(db)
    existing = get_existing_financial_tickers(db)
    pending = [item for item in companies if normalize_base_ticker(item["ticker"] or "") not in existing]

    total = len(companies)
    skipped = total - len(pending)
    print(f"[Financials] 共 {total} 檔，已跳過 {skipped} 檔，準備下載 {len(pending)} 檔。")

    total_rows = 0

    for index, item in enumerate(pending, start=1):
        raw_ticker = normalize_base_ticker(item["ticker"] or "")
        market = item.get("market")
        yf_symbol = build_yfinance_symbol(raw_ticker, market)

        # ETF/ETN 通常不適用公司財報資料，先跳過避免不必要錯誤。
        if is_probably_etf_or_etn(raw_ticker):
            print(f"[Financials] {yf_symbol} 判定為 ETF/ETN，無公司季報，跳過")
            continue

        try:
            print(f"[Financials] ({index}/{len(pending)}) 下載 {yf_symbol}")
            rows: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc)

            yf_ticker = yf.Ticker(yf_symbol)

            # 先讀取公司資訊中的網站欄位，成功的話一併補回資料庫。
            website: str | None = None
            try:
                info = yf_ticker.info
                if isinstance(info, dict):
                    website = info.get("website") or None
            except KeyError as exc:
                print(f"[Financials][Warn] {yf_symbol} website 欄位缺失：{exc}")
            except AttributeError as exc:
                print(f"[Financials][Warn] {yf_symbol} website 屬性缺失：{exc}")
            except Exception as exc:
                print(f"[Financials][Warn] {yf_symbol} website 讀取失敗：{exc}")

            if website:
                upsert_company_website(db, raw_ticker, website)

            try:
                quarterly = yf_ticker.quarterly_financials
            except KeyError as exc:
                print(f"[Financials][Warn] {yf_symbol} 季報 KeyError：{exc}，跳過")
                continue
            except AttributeError as exc:
                print(f"[Financials][Warn] {yf_symbol} 季報 AttributeError：{exc}，跳過")
                continue
            except Exception as exc:
                print(f"[Financials][Warn] {yf_symbol} 季報抓取失敗：{exc}，跳過")
                continue

            if quarterly is None or quarterly.empty:
                print(f"[Financials][Warn] {yf_symbol} 無季報資料，跳過")
                continue

            for metric, values in quarterly.iterrows():
                metric_name = str(metric).strip()
                for report_date, value in values.items():
                    if pd.isna(value):
                        continue

                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError):
                        continue

                    rows.append(
                        {
                            "ticker": raw_ticker,
                            "report_date": pd.to_datetime(report_date).date(),
                            "metric": metric_name,
                            "value": numeric_value,
                            "source": "yfinance",
                            "updated_at": now,
                        }
                    )

            if not rows:
                print(f"[Financials][Warn] {yf_symbol} 無可寫入的季報欄位，跳過")
                continue

            written = upsert_financial_rows(db, rows)
            total_rows += written
            print(f"[Financials] {yf_symbol} 完成，寫入 {written} 筆")
        except Exception as exc:
            # 單一標的出錯只回滾自身，避免整體批次中止。
            db.rollback()
            print(f"[Financials][Error] {yf_symbol} 處理失敗：{exc}")
            continue
        finally:
            time.sleep(random.uniform(1.5, 3.5))

    return total_rows


def main() -> None:
    """批次入口：解析參數後，依序執行公司資料、K 線、財報同步。"""
    parser = argparse.ArgumentParser(description="Nightly batch for TW market data sync.")
    parser.add_argument(
        "--force-refresh-kline",
        action="store_true",
        help="忽略既有 K 線資料，重新抓取後覆蓋。",
    )
    parser.add_argument(
        "--refresh-failed-only",
        action="store_true",
        help="只重抓曾驗證失敗或尚未驗證的代號。",
    )
    parser.add_argument(
        "--kline-limit",
        type=int,
        default=None,
        help="測試用：只處理前 N 檔 K 線。",
    )
    parser.add_argument(
        "--skip-financials",
        action="store_true",
        help="測試 K 線時跳過季報與官網更新。",
    )
    parser.add_argument(
        "--sync-yfinance-raw",
        action="store_true",
        help="只補抓尚未有 raw_kline_yfinance 資料的代號，不重跑 FinMind。",
    )
    args = parser.parse_args()

    ensure_failed_download_log_file()

    # 啟動時先嘗試用 Token 登入 FinMind，提升穩定性與配額。
    configure_finmind_auth()

    # 啟動前先補齊資料庫 schema，再建立 session。
    ensure_runtime_schema()
    db = SessionLocal()

    try:
        # --sync-yfinance-raw 模式：只補抓 yfinance 原始資料，跳過其他階段。
        if args.sync_yfinance_raw:
            sync_yfinance_raw_only(db, limit=args.kline_limit)
            return

        # Stage 1：先同步公司基本資料，提供後續所有流程的代號母體。
        try:
            sync_company_info_from_csv(db)
        except Exception as exc:
            db.rollback()
            print(f"[Stage 1][Error] 公司基本資料更新失敗：{exc}")

        # Stage 2：下載或刷新股票 K 線歷史資料。
        try:
            sync_kline_history(
                db,
                force_refresh=(args.force_refresh_kline or args.refresh_failed_only),
                refresh_failed_only=args.refresh_failed_only,
                limit=args.kline_limit,
            )
        except Exception as exc:
            db.rollback()
            print(f"[Stage 2][Error] K 線更新失敗：{exc}")

        # Stage 3：補抓公司財報；可透過參數在測試時略過。
        if args.skip_financials:
            print("[Stage 3] 已依參數跳過財報更新")
        else:
            try:
                sync_company_financials(db)
            except Exception as exc:
                db.rollback()
                print(f"[Stage 3][Error] 財報更新失敗：{exc}")
    finally:
        # 無論成功或失敗都釋放資料庫連線。
        db.close()


if __name__ == "__main__":
    main()
