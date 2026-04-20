from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

try:
    from .data_validator import fetch_tpex_daily_closes, fetch_twse_daily_closes
    from .database import SessionLocal
    from .models import CompanyInfo, StockKline
    from .nightly_batch import get_refresh_target_tickers, normalize_base_ticker
except ImportError:
    from data_validator import fetch_tpex_daily_closes, fetch_twse_daily_closes
    from database import SessionLocal
    from models import CompanyInfo, StockKline
    from nightly_batch import get_refresh_target_tickers, normalize_base_ticker


def build_sample_tickers(limit: int, failed_only: bool = True) -> list[str]:
    db = SessionLocal()
    try:
        companies = db.query(CompanyInfo.ticker).order_by(CompanyInfo.ticker.asc()).all()
        if failed_only:
            refresh_targets = get_refresh_target_tickers(db, failed_only=True)
            selected = [
                normalize_base_ticker(row[0])
                for row in companies
                if normalize_base_ticker(row[0]) in refresh_targets
            ]
        else:
            selected = [normalize_base_ticker(row[0]) for row in companies]

        unique: list[str] = []
        seen: set[str] = set()
        for ticker in selected:
            if ticker in seen:
                continue
            seen.add(ticker)
            unique.append(ticker)
            if len(unique) >= limit:
                break

        return unique
    finally:
        db.close()


def verify_sample(limit: int = 50, lookback_days: int = 30) -> None:
    sample = build_sample_tickers(limit=limit, failed_only=True)
    if not sample:
        print("[SampleTest] 找不到可測試的樣本股票。")
        return

    since = date.today() - timedelta(days=lookback_days)
    db = SessionLocal()

    try:
        rows = (
            db.query(StockKline.ticker, StockKline.date, StockKline.close, StockKline.adj_close)
            .filter(StockKline.date >= since)
            .filter(StockKline.ticker.in_([f"{ticker}.TW" for ticker in sample] + [f"{ticker}.TWO" for ticker in sample]))
            .order_by(StockKline.ticker.asc(), StockKline.date.asc())
            .all()
        )

        if not rows:
            print("[SampleTest] 最近區間沒有找到樣本 K 線資料。")
            return

        grouped_by_date: dict[date, list] = defaultdict(list)
        for row in rows:
            grouped_by_date[row.date].append(row)

        total_checked = 0
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        ticker_summary: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})

        for target_date in sorted(grouped_by_date.keys()):
            if target_date.weekday() >= 5:
                for row in grouped_by_date[target_date]:
                    ticker_summary[row.ticker]["skipped"] += 1
                    total_skipped += 1
                continue

            try:
                twse_closes = fetch_twse_daily_closes(target_date)
            except Exception:
                twse_closes = {}

            try:
                tpex_closes = fetch_tpex_daily_closes(target_date)
            except Exception:
                tpex_closes = {}

            for row in grouped_by_date[target_date]:
                ticker = row.ticker
                code = normalize_base_ticker(ticker)
                if ticker.endswith(".TW"):
                    official_close = twse_closes.get(code)
                elif ticker.endswith(".TWO"):
                    official_close = tpex_closes.get(code)
                else:
                    official_close = None

                if official_close is None or official_close <= 0:
                    ticker_summary[ticker]["skipped"] += 1
                    total_skipped += 1
                    continue

                db_close = float(row.close)
                db_adj_close = float(row.adj_close) if row.adj_close is not None else None
                error_candidates = [abs(db_close - official_close) / official_close * 100]
                if db_adj_close is not None and db_adj_close > 0:
                    error_candidates.append(abs(db_adj_close - official_close) / official_close * 100)

                error_pct = min(error_candidates)
                total_checked += 1
                if error_pct <= 0.5:
                    ticker_summary[ticker]["passed"] += 1
                    total_passed += 1
                else:
                    ticker_summary[ticker]["failed"] += 1
                    total_failed += 1

        print(f"[SampleTest] 樣本檔數: {len(sample)}，觀察區間: 最近 {lookback_days} 天")
        print(
            f"[SampleTest] 已檢查={total_checked} 通過={total_passed} 失敗={total_failed} 跳過={total_skipped}"
        )
        if total_checked > 0:
            pass_rate = total_passed * 100 / total_checked
            print(f"[SampleTest] 通過率={pass_rate:.2f}%")

        print("\n[SampleTest] 前 20 檔摘要：")
        shown = 0
        for ticker in sorted(ticker_summary.keys()):
            stats = ticker_summary[ticker]
            print(
                f"  {ticker}: 通過={stats['passed']} 失敗={stats['failed']} 跳過={stats['skipped']}"
            )
            shown += 1
            if shown >= 20:
                break
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="測試安全重抓樣本的 K 線正確率。")
    parser.add_argument("--limit", type=int, default=50, help="要測試幾檔樣本，預設 50")
    parser.add_argument("--lookback-days", type=int, default=30, help="往回檢查幾天，預設 30")
    args = parser.parse_args()
    verify_sample(limit=args.limit, lookback_days=args.lookback_days)


if __name__ == "__main__":
    main()
