from __future__ import annotations

"""統一爬蟲入口。

把原本分散的市場資料、夜間批次、月營收、新聞抓取整合到同一支程式，
並集中放在 backend/crawlers 底下，方便管理與啟動。
"""

import argparse
from datetime import date
import random
import sys
import time
from pathlib import Path

# 允許直接用 `python backend/crawlers/data_crawler.py ...` 啟動。
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from ..database import SessionLocal
    from ..monthly_revenue_crawler import crawl_monthly_revenue, crawl_monthly_revenue_backfill
    from ..news_crawler import run_news_crawler
    from ..batch_core import (
        FINMIND_BAN_SLEEP_SECONDS,
        YFINANCE_BAN_SLEEP_SECONDS,
        FinMindIpBannedError,
        YFinanceIpBannedError,
        append_failed_download_log,
        build_yfinance_symbol,
        ensure_runtime_schema,
        fetch_and_store_yfinance_raw,
        fetch_finmind_history,
        get_company_universe,
        normalize_base_ticker,
        sync_company_financials,
        sync_company_info_from_csv,
        sync_kline_history,
        upsert_stock_history_rows,
    )
except ImportError:
    from database import SessionLocal
    from monthly_revenue_crawler import crawl_monthly_revenue, crawl_monthly_revenue_backfill
    from news_crawler import run_news_crawler
    from batch_core import (
        FINMIND_BAN_SLEEP_SECONDS,
        YFINANCE_BAN_SLEEP_SECONDS,
        FinMindIpBannedError,
        YFinanceIpBannedError,
        append_failed_download_log,
        build_yfinance_symbol,
        ensure_runtime_schema,
        fetch_and_store_yfinance_raw,
        fetch_finmind_history,
        get_company_universe,
        normalize_base_ticker,
        sync_company_financials,
        sync_company_info_from_csv,
        sync_kline_history,
        upsert_stock_history_rows,
    )


HOT_TICKERS = [
    # ETF 只保留 0050 / 0052，避免首版折溢價資料源過多。
    "0050.TW", "0052.TW",
    # 補 20~30 檔高成交股票（首版先上核心權值與金融、航運、電子）。
    "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2303.TW", "2412.TW",
    "2881.TW", "2882.TW", "2886.TW", "2884.TW", "2891.TW", "2885.TW", "2880.TW", "2883.TW", "2892.TW",
    "2603.TW", "2609.TW", "2615.TW", "2618.TW",
    "3037.TW", "3711.TW", "3231.TW", "2382.TW", "2357.TW", "2379.TW", "3045.TW",
    "5880.TW", "2002.TW", "2207.TW", "6505.TW",
]

FOCUS_STOCK_TICKERS = [ticker for ticker in HOT_TICKERS if not ticker.startswith("00")]


def fetch_and_store_ticker(db, ticker: str, period: str = "10y") -> int:
    """同步單一股票的 FinMind K 線與 yfinance 原始資料。"""
    del period

    raw_ticker = normalize_base_ticker(ticker)
    market = None
    if ticker.upper().endswith(".TW"):
        market = "listed"
    elif ticker.upper().endswith(".TWO"):
        market = "otc"
    yf_symbol = build_yfinance_symbol(raw_ticker, market)

    while True:
        try:
            history = fetch_finmind_history(raw_ticker)
            rows = []

            for _, row in history.iterrows():
                rows.append(
                    {
                        "ticker": yf_symbol,
                        "date": row["date"],
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

            while True:
                try:
                    yf_written = fetch_and_store_yfinance_raw(db, yf_symbol)
                    print(f"[yf-raw] {yf_symbol} 寫入 {yf_written} 筆")
                    break
                except YFinanceIpBannedError as exc:
                    print(f"[yf-raw][Warn] {exc}；自動暫停 {YFINANCE_BAN_SLEEP_SECONDS // 60} 分鐘後重試")
                    append_failed_download_log("yfinance_raw_single_ip_ban", yf_symbol, exc)
                    time.sleep(YFINANCE_BAN_SLEEP_SECONDS)
                except Exception as exc:
                    print(f"[yf-raw][Warn] {yf_symbol} yfinance 原始資料失敗，跳過：{exc}")
                    append_failed_download_log("yfinance_raw_single", yf_symbol, exc)
                    break

            return written
        except FinMindIpBannedError as exc:
            db.rollback()
            print(f"[K-line][Warn] {exc}；自動暫停 {FINMIND_BAN_SLEEP_SECONDS // 60} 分鐘後重試")
            append_failed_download_log("finmind_single_ip_ban", yf_symbol, exc)
            time.sleep(FINMIND_BAN_SLEEP_SECONDS)
        except Exception:
            db.rollback()
            raise


def crawl_once(period: str = "10y", min_delay: float = 1.0, max_delay: float = 3.0) -> int:
    """同步目前公司池的全市場 K 線。"""
    del period

    ensure_runtime_schema()
    db = SessionLocal()

    try:
        companies = get_company_universe(db)
        total = len(companies)
        total_rows = 0

        for index, item in enumerate(companies, start=1):
            raw_ticker = normalize_base_ticker(item["ticker"] or "")
            market = item.get("market")
            yf_symbol = build_yfinance_symbol(raw_ticker, market)
            print(f"[Full K-line] ({index}/{total}) 下載 {yf_symbol}")

            try:
                total_rows += fetch_and_store_ticker(db, yf_symbol)
            except Exception as exc:
                db.rollback()
                print(f"[Full K-line][Error] {yf_symbol} 下載失敗：{exc}")
                append_failed_download_log("full_kline", yf_symbol, exc)

            if max_delay > 0:
                time.sleep(random.uniform(max(0.0, min_delay), max(min_delay, max_delay)))

        return total_rows
    finally:
        db.close()


def run_company_info_sync() -> int:
    """只同步公司基本資料。"""
    ensure_runtime_schema()
    db = SessionLocal()
    try:
        return sync_company_info_from_csv(db)
    finally:
        db.close()


def run_hot_ticker_sync(period: str = "10y", delay_seconds: float = 5.0) -> int:
    """只同步熱門股票清單的 K 線資料。"""
    ensure_runtime_schema()
    db = SessionLocal()
    total_rows = 0
    total = len(HOT_TICKERS)

    try:
        for index, ticker in enumerate(HOT_TICKERS, start=1):
            print(f"[Hot K-line] ({index}/{total}) 下載 {ticker}")
            try:
                written = fetch_and_store_ticker(db, ticker, period=period)
                total_rows += written
                print(f"[Hot K-line] {ticker} 完成，寫入 {written} 筆")
            except Exception as error:
                db.rollback()
                print(f"[Hot K-line][Error] {ticker} 下載失敗：{error}")

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        return total_rows
    finally:
        db.close()


def run_monthly_revenue_sync(year: int | None = None, month: int | None = None) -> None:
    """同步月營收資料。"""
    crawl_monthly_revenue(year, month)


def run_focus_revenue_backfill(
    months: int = 60,
    sleep_min: float = 0.3,
    sleep_max: float = 0.8,
    workers: int = 8,
) -> None:
    """只補抓焦點股票池的歷史月營收（不含 ETF）。"""
    today = date.today()
    if today.month == 1:
        end_year, end_month = today.year - 1, 12
    else:
        end_year, end_month = today.year, today.month - 1

    total = end_year * 12 + end_month - (months - 1)
    start_year = (total - 1) // 12
    start_month = (total - 1) % 12 + 1

    crawl_monthly_revenue_backfill(
        start_year=start_year,
        start_month=start_month,
        end_year=end_year,
        end_month=end_month,
        sleep_min=sleep_min,
        sleep_max=sleep_max,
        workers=workers,
        tickers_filter=FOCUS_STOCK_TICKERS,
    )


def run_news_sync(tickers: list[str] | None = None) -> None:
    """同步個股新聞資料。"""
    run_news_crawler(tickers)


def run_nightly_sync(
    force_refresh_kline: bool = False,
    refresh_failed_only: bool = False,
    kline_limit: int | None = None,
    skip_financials: bool = False,
) -> None:
    """執行完整夜間批次。"""
    ensure_runtime_schema()
    db = SessionLocal()

    try:
        try:
            sync_company_info_from_csv(db)
        except Exception as exc:
            db.rollback()
            print(f"[Stage 1][Error] 公司基本資料更新失敗：{exc}")

        try:
            sync_kline_history(
                db,
                force_refresh=force_refresh_kline or refresh_failed_only,
                refresh_failed_only=refresh_failed_only,
                limit=kline_limit,
            )
        except Exception as exc:
            db.rollback()
            print(f"[Stage 2][Error] K 線更新失敗：{exc}")

        if skip_financials:
            print("[Stage 3] 已依參數跳過財報更新")
        else:
            try:
                sync_company_financials(db)
            except Exception as exc:
                db.rollback()
                print(f"[Stage 3][Error] 財報更新失敗：{exc}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified crawler entry point for all backend data jobs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("company-info", help="同步公司基本資料")

    hot_parser = subparsers.add_parser("hot-kline", help="同步焦點股票池 K 線（含 0050/0052）")
    hot_parser.add_argument("--period", default="5y", help="yfinance history period，預設 5y")
    hot_parser.add_argument("--delay", type=float, default=5.0, help="每檔股票之間的等待秒數")

    full_parser = subparsers.add_parser("full-kline", help="同步全市場 K 線")
    full_parser.add_argument("--period", default="10y", help="yfinance history period，預設 10y")
    full_parser.add_argument("--continuous", action="store_true", help="持續循環執行")
    full_parser.add_argument("--cycle-sleep", type=int, default=300, help="每輪之間休息秒數")
    full_parser.add_argument("--min-delay", type=float, default=1.0, help="單一代號最小等待秒數")
    full_parser.add_argument("--max-delay", type=float, default=3.0, help="單一代號最大等待秒數")

    nightly_parser = subparsers.add_parser("nightly", help="執行夜間整批同步")
    nightly_parser.add_argument("--force-refresh-kline", action="store_true", help="強制重抓所有 K 線")
    nightly_parser.add_argument("--refresh-failed-only", action="store_true", help="只重抓失敗或未驗證的 K 線")
    nightly_parser.add_argument("--kline-limit", type=int, default=None, help="只處理前 N 檔，方便測試")
    nightly_parser.add_argument("--skip-financials", action="store_true", help="略過財報同步")

    revenue_parser = subparsers.add_parser("monthly-revenue", help="同步月營收資料")
    revenue_parser.add_argument("--year", type=int, default=None, help="西元年，例如 2026")
    revenue_parser.add_argument("--month", type=int, default=None, help="月份 1-12")

    focus_revenue_parser = subparsers.add_parser("focus-revenue", help="只補抓焦點股票池月營收（不含 ETF）")
    focus_revenue_parser.add_argument("--months", type=int, default=60, help="回補最近 N 個月，預設 60")
    focus_revenue_parser.add_argument("--sleep-min", type=float, default=0.3, help="每檔請求最小 sleep 秒數")
    focus_revenue_parser.add_argument("--sleep-max", type=float, default=0.8, help="每檔請求最大 sleep 秒數")
    focus_revenue_parser.add_argument("--workers", type=int, default=8, help="並發 worker 數，預設 8")

    news_parser = subparsers.add_parser("news", help="同步個股新聞")
    news_parser.add_argument("--tickers", nargs="*", default=None, help="指定股票代號，例如 2330.TW 0050.TW")

    args = parser.parse_args()

    if args.command == "company-info":
        written = run_company_info_sync()
        print(f"[CrawlerSuite] 公司基本資料同步完成，共寫入 {written} 筆")
    elif args.command == "hot-kline":
        total_rows = run_hot_ticker_sync(period=args.period, delay_seconds=args.delay)
        print(f"[CrawlerSuite] 熱門股票 K 線同步完成，共寫入 {total_rows} 筆")
    elif args.command == "full-kline":
        if args.continuous:
            cycle = 1
            while True:
                print(f"[CrawlerSuite] Full K-line cycle {cycle} start")
                crawl_once(period=args.period, min_delay=args.min_delay, max_delay=args.max_delay)
                print(f"[CrawlerSuite] Full K-line cycle {cycle} finished，休息 {args.cycle_sleep} 秒")
                time.sleep(args.cycle_sleep)
                cycle += 1
        else:
            crawl_once(period=args.period, min_delay=args.min_delay, max_delay=args.max_delay)
    elif args.command == "nightly":
        run_nightly_sync(
            force_refresh_kline=args.force_refresh_kline,
            refresh_failed_only=args.refresh_failed_only,
            kline_limit=args.kline_limit,
            skip_financials=args.skip_financials,
        )
    elif args.command == "monthly-revenue":
        run_monthly_revenue_sync(year=args.year, month=args.month)
    elif args.command == "focus-revenue":
        run_focus_revenue_backfill(
            months=args.months,
            sleep_min=args.sleep_min,
            sleep_max=args.sleep_max,
            workers=args.workers,
        )
    elif args.command == "news":
        run_news_sync(tickers=args.tickers)


if __name__ == "__main__":
    main()
