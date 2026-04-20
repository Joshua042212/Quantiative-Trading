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
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from FinMind.data import DataLoader
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

try:
    from .database import Base, SessionLocal, engine
    from .models import CompanyFinancials, CompanyInfo, StockKline, UnsupportedTicker
except ImportError:
    from database import Base, SessionLocal, engine
    from models import CompanyFinancials, CompanyInfo, StockKline, UnsupportedTicker

# 各市場公司清單來源，夜間批次會依序下載這些公開 CSV。
CSV_SOURCES: dict[str, str] = {
    "listed": "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
    "otc": "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
    "emerging": "https://mopsfin.twse.com.tw/opendata/t187ap03_R.csv",
}

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
                ADD COLUMN IF NOT EXISTS adj_low DOUBLE PRECISION
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
    if market == "listed":
        return f"{base}.TW"
    return f"{base}.TWO"


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
    """彙整所有市場的公司清單並同步到 company_info。"""
    frames: list[pd.DataFrame] = []

    # 逐一下載上市、上櫃、興櫃資料。
    for market, url in CSV_SOURCES.items():
        print(f"[CompanyInfo] 下載 {market} CSV: {url}")
        frame = read_company_csv(url, market)
        print(f"[CompanyInfo] {market} 載入 {len(frame)} 筆")
        frames.append(frame)

    # 合併後再去重，避免同一代號因不同來源重複寫入。
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


def upsert_unsupported_ticker(db: Session, ticker: str, reason: str) -> None:
    now = datetime.now(timezone.utc)
    stmt = insert(UnsupportedTicker).values(
        {
            "ticker": ticker,
            "reason": reason[:255],
            "source": "nightly_batch",
            "first_seen_at": now,
            "last_seen_at": now,
        }
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_unsupported_ticker",
        set_={
            "reason": stmt.excluded.reason,
            "source": stmt.excluded.source,
            "last_seen_at": stmt.excluded.last_seen_at,
        },
    )
    db.execute(stmt)
    db.commit()


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


def _normalize_finmind_frame(frame: pd.DataFrame, *, adjusted: bool) -> pd.DataFrame:
    """把 FinMind 回傳欄位整理成系統使用的標準欄位。"""
    if frame is None or frame.empty:
        if adjusted:
            return pd.DataFrame(columns=["date", "adj_open", "adj_high", "adj_low", "adj_close", "volume"])
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.date
    trading_volume = normalized["Trading_Volume"] if "Trading_Volume" in normalized.columns else 0

    if adjusted:
        normalized["adj_open"] = pd.to_numeric(normalized["open"], errors="coerce")
        normalized["adj_high"] = pd.to_numeric(normalized["max"], errors="coerce")
        normalized["adj_low"] = pd.to_numeric(normalized["min"], errors="coerce")
        normalized["adj_close"] = pd.to_numeric(normalized["close"], errors="coerce")
        normalized["volume"] = pd.to_numeric(trading_volume, errors="coerce").fillna(0)
        return normalized[["date", "adj_open", "adj_high", "adj_low", "adj_close", "volume"]]

    normalized["open"] = pd.to_numeric(normalized["open"], errors="coerce")
    normalized["high"] = pd.to_numeric(normalized["max"], errors="coerce")
    normalized["low"] = pd.to_numeric(normalized["min"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized["volume"] = pd.to_numeric(trading_volume, errors="coerce").fillna(0)
    return normalized[["date", "open", "high", "low", "close", "volume"]]


def fetch_finmind_history(stock_id: str, start_date: str = FINMIND_START_DATE) -> pd.DataFrame:
    """使用 FinMind 同步抓取未還原與還原後價格，並按日期合併。"""
    end_date = datetime.now().date().isoformat()

    raw_history = pd.DataFrame()
    adj_history = pd.DataFrame()
    raw_error: Exception | None = None
    adj_error: Exception | None = None

    try:
        raw_history = api.taiwan_stock_daily(stock_id=stock_id, start_date=start_date, end_date=end_date)
    except Exception as exc:
        raw_error = exc

    try:
        # 目前環境中的 FinMind 版本需要明確傳入 end_date。
        adj_history = api.taiwan_stock_daily_adj(stock_id=stock_id, start_date=start_date, end_date=end_date)
    except Exception as exc:
        adj_error = exc
        print(f"[K-line][Warn] {stock_id} 還原價抓取失敗，改以原始價作為備援：{exc}")

    raw = _normalize_finmind_frame(raw_history, adjusted=False)
    adj = _normalize_finmind_frame(adj_history, adjusted=True)

    if raw.empty and adj.empty:
        raise RuntimeError(f"FinMind returned no history for {stock_id}; raw_error={raw_error}; adj_error={adj_error}")

    if raw.empty:
        raw = adj.rename(
            columns={
                "adj_open": "open",
                "adj_high": "high",
                "adj_low": "low",
                "adj_close": "close",
            }
        )
    if adj.empty:
        adj = raw.rename(
            columns={
                "open": "adj_open",
                "high": "adj_high",
                "low": "adj_low",
                "close": "adj_close",
            }
        )

    merged = raw.merge(adj, on="date", how="outer", suffixes=("", "_adj"))
    if "volume_adj" in merged.columns:
        merged["volume"] = merged["volume"].fillna(merged["volume_adj"])
        merged = merged.drop(columns=["volume_adj"])

    merged["open"] = merged["open"].fillna(merged.get("adj_open"))
    merged["high"] = merged["high"].fillna(merged.get("adj_high"))
    merged["low"] = merged["low"].fillna(merged.get("adj_low"))
    merged["close"] = merged["close"].fillna(merged.get("adj_close"))
    merged["adj_open"] = merged["adj_open"].fillna(merged["open"])
    merged["adj_high"] = merged["adj_high"].fillna(merged["high"])
    merged["adj_low"] = merged["adj_low"].fillna(merged["low"])
    merged["adj_close"] = merged["adj_close"].fillna(merged["close"])
    merged["volume"] = merged["volume"].fillna(0)

    merged = merged.dropna(subset=["date", "open", "high", "low", "close", "adj_close"])
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


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

        try:
            print(f"[K-line] ({index}/{len(pending)}) 下載 {yf_symbol}")

            # 若本次是刷新模式，先把舊資料移除再重新寫回。
            if force_refresh:
                deleted = delete_existing_kline_for_symbol(db, raw_ticker, yf_symbol)
                if deleted:
                    print(f"[K-line] {yf_symbol} 已刪除舊資料 {deleted} 筆，準備重抓")

            # 使用 FinMind 下載台股日 K；原始價與還原價分別抓取後再依日期合併。
            history = fetch_finmind_history(raw_ticker, start_date=FINMIND_START_DATE)

            # 若來源完全沒有資料，記錄成 unsupported 以便後續排查。
            if history is None or history.empty:
                upsert_unsupported_ticker(db, raw_ticker, "no_finmind_history")
                print(f"[K-line][Warn] {yf_symbol} 無歷史資料，已記錄後跳過")
                continue

            rows: list[dict[str, Any]] = []

            # 將 FinMind 合併後的 DataFrame 逐列轉成資料庫可接受的 dict。
            for _, row in history.iterrows():
                open_price = float(row["open"])
                high_price = float(row["high"])
                low_price = float(row["low"])
                close_price = float(row["close"])
                adj_open_price = float(row["adj_open"])
                adj_high_price = float(row["adj_high"])
                adj_low_price = float(row["adj_low"])
                adj_close_price = float(row["adj_close"])

                rows.append(
                    {
                        "ticker": yf_symbol,
                        "date": pd.to_datetime(row["date"]).date(),
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "adj_open": adj_open_price,
                        "adj_high": adj_high_price,
                        "adj_low": adj_low_price,
                        "adj_close": adj_close_price,
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
        except Exception as exc:
            # 單一代號失敗不影響整批，回滾後記錄錯誤原因。
            db.rollback()
            try:
                upsert_unsupported_ticker(db, raw_ticker, f"history_error: {exc}")
            except Exception:
                db.rollback()
            print(f"[K-line][Error] {yf_symbol} 下載失敗：{exc}")
            continue
        finally:
            # 適度隨機延遲，降低外部來源的請求壓力。
            time.sleep(random.uniform(1.5, 3.5))

    return total_rows


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

    stmt = insert(CompanyFinancials).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_company_financials_ticker_date_metric",
        set_={
            "value": stmt.excluded.value,
            "source": stmt.excluded.source,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def sync_company_financials(db: Session) -> int:
    """同步公司季報資料，並順手補齊公司網站欄位。"""
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

            # 再抓季度財報，這是本階段最核心的資料來源。
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

            rows: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc)

            # 把 yfinance 的列、欄結構攤平成資料庫紀錄。
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
    args = parser.parse_args()

    # 啟動前先補齊資料庫 schema，再建立 session。
    ensure_runtime_schema()
    db = SessionLocal()

    try:
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
