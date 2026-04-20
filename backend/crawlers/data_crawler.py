from __future__ import annotations

"""統一爬蟲入口。

把原本分散的市場資料、夜間批次、月營收、新聞抓取整合到同一支程式，
並集中放在 backend/crawlers 底下，方便管理與啟動。
"""

import argparse
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
    from ..full_market_crawler import crawl_once, fetch_and_store_ticker
    from ..monthly_revenue_crawler import crawl_monthly_revenue
    from ..news_crawler import run_news_crawler
    from ..nightly_batch import ensure_runtime_schema, sync_company_financials, sync_company_info_from_csv, sync_kline_history
except ImportError:
    from database import SessionLocal
    from full_market_crawler import crawl_once, fetch_and_store_ticker
    from monthly_revenue_crawler import crawl_monthly_revenue
    from news_crawler import run_news_crawler
    from nightly_batch import ensure_runtime_schema, sync_company_financials, sync_company_info_from_csv, sync_kline_history


HOT_TICKERS = [
    "2330.TW", "0050.TW", "2317.TW", "2454.TW", "2603.TW", "2881.TW", "2308.TW", "2303.TW", "2882.TW", "1303.TW",
    "1301.TW", "2412.TW", "2891.TW", "2886.TW", "2884.TW", "2885.TW", "2880.TW", "2887.TW", "5880.TW", "2002.TW",
    "3711.TW", "1216.TW", "1101.TW", "1102.TW", "1326.TW", "6505.TW", "2207.TW", "2327.TW", "2408.TW", "3034.TW",
    "3008.TW", "2382.TW", "2357.TW", "2379.TW", "6669.TW", "3443.TW", "3037.TW", "3661.TW", "2353.TW", "2356.TW",
    "2383.TW", "2883.TW", "2892.TW", "3045.TW", "3231.TW", "4904.TW", "2615.TW", "2618.TW", "2609.TW", "2610.TW",
    "2645.TW", "5871.TW", "2888.TW", "6415.TW", "8046.TW", "2458.TW", "2376.TW", "3017.TW", "2409.TW", "4938.TW",
    "2345.TW", "2404.TW", "5269.TW", "3529.TW", "1590.TW", "9910.TW", "9921.TW", "9945.TW", "2912.TW", "5876.TW",
    "8454.TW", "2201.TW", "2385.TW", "1476.TW", "2834.TW", "6005.TW", "9904.TW", "9914.TW", "9933.TW", "2105.TW",
    "2106.TW", "2606.TW", "2637.TW", "6770.TW", "1513.TW", "1504.TW", "1605.TW", "2377.TW", "2301.TW", "2347.TW",
    "2474.TW", "3715.TW", "6531.TW", "2324.TW", "2371.TW", "2449.TW", "2607.TW", "4906.TW", "4935.TW", "4958.TW",
]


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

    hot_parser = subparsers.add_parser("hot-kline", help="同步熱門股票 K 線")
    hot_parser.add_argument("--period", default="10y", help="yfinance history period，預設 10y")
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
    elif args.command == "news":
        run_news_sync(tickers=args.tickers)


if __name__ == "__main__":
    main()
