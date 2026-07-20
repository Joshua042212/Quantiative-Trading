"""每日排程任務入口。

由 Windows Task Scheduler 每天 20:00 呼叫：
    python backend/scheduled_tasks.py --backfill-days 5

根據當天日期自動決定執行哪些任務：
    - K 線增量更新：每天
    - 月營收更新：每月 REVENUE_DAYS 所列的日期
    - 公司基本資料：每月 COMPANY_INFO_DAY
    - 財報更新：FINANCIALS_MONTHS 月份中的 FINANCIALS_DAY 號

若電腦當天關機錯過排程，Task Scheduler 設定 StartWhenAvailable，
開機後會自動補跑。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# ────────────────────────────────────────────────────────────────
#  可調整常數區：改這裡即可調整各任務的觸發頻率
# ────────────────────────────────────────────────────────────────

KLINE_BACKFILL_DAYS: int = 5
"""K 線增量補抓天數。每日只下載最近 N 天缺漏的資料。首次執行建議用 --backfill-days 10。"""

REVENUE_DAYS: set[int] = {5, 6, 7, 8, 9, 10, 11}
"""每月哪幾號要更新月營收。台上市公司須於次月 10 日前公告，11 號後幾乎全部公告完畢。"""

COMPANY_INFO_DAY: int = 1
"""每月幾號更新公司基本資料（新掛牌、下市偵測）。"""

FINANCIALS_MONTHS: set[int] = {3, 4, 5, 6, 8, 9, 11, 12}
"""哪些月份要更新財報。Q1 在 5 月底公告、Q2 在 8 月底、Q3 在 11 月底、Q4 在 3 月底；
各月及其下個月（確保資料齊全）都觸發一次。"""

FINANCIALS_DAY: int = 15
"""財報月份中的第幾號執行。"""

# ────────────────────────────────────────────────────────────────
#  路徑設定
# ────────────────────────────────────────────────────────────────

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_LOG_DIR = _BACKEND_DIR / "logs"

# 讓 `python backend/scheduled_tasks.py` 與 `python -m backend.scheduled_tasks` 都能正常 import。
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ────────────────────────────────────────────────────────────────
#  Logger 設定：同時寫到 console 與 logs/scheduled_YYYYMMDD.log
# ────────────────────────────────────────────────────────────────

def setup_logging(today: date) -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / f"scheduled_{today.strftime('%Y%m%d')}.log"

    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("scheduled_tasks")


# ────────────────────────────────────────────────────────────────
#  Lazy imports（避免頂層 import 拖慢啟動，且讓路徑設定先完成）
# ────────────────────────────────────────────────────────────────

def _import_deps():
    """延遲 import 所有後端依賴，確保 sys.path 已設定完成。"""
    from backend.database import SessionLocal
    from backend.monthly_revenue_crawler import crawl_monthly_revenue, crawl_monthly_revenue_backfill
    from backend.batch_core import (
        compute_and_store_sma,
        configure_finmind_auth,
        ensure_runtime_schema,
        sync_kline_incremental,
        sync_company_financials,
        sync_company_info_from_csv,
    )

    return {
        "SessionLocal": SessionLocal,
        "crawl_monthly_revenue": crawl_monthly_revenue,
        "crawl_monthly_revenue_backfill": crawl_monthly_revenue_backfill,
        "compute_and_store_sma": compute_and_store_sma,
        "configure_finmind_auth": configure_finmind_auth,
        "ensure_runtime_schema": ensure_runtime_schema,
        "sync_kline_incremental": sync_kline_incremental,
        "sync_company_financials": sync_company_financials,
        "sync_company_info_from_csv": sync_company_info_from_csv,
        "SessionLocal": SessionLocal,
    }


# ────────────────────────────────────────────────────────────────
#  任務實作
# ────────────────────────────────────────────────────────────────

def run_kline_incremental(backfill_days: int, log: logging.Logger) -> None:
    """K 線增量更新。

    對所有股票：
    - 若已有資料 → 只補最近 backfill_days 天缺漏的資料（快）
    - 若從未下載 → 下載完整歷史（新上市股票）
    全部使用 ON CONFLICT DO NOTHING，不覆蓋既有資料。
    """
    deps = _import_deps()
    SessionLocal = deps["SessionLocal"]
    ensure_runtime_schema = deps["ensure_runtime_schema"]
    sync_kline_incremental = deps["sync_kline_incremental"]

    ensure_runtime_schema()
    db = SessionLocal()

    try:
        summary = sync_kline_incremental(db, backfill_days=backfill_days, log=log)
        log.info(
            f"[K線增量] 完成。成功 {summary['success']}，失敗 {summary['failed']}，"
            f"總寫入 {summary['written']} 筆"
        )

    finally:
        db.close()


def run_sma_update(log: logging.Logger, ticker: str | None = None) -> None:
    """計算並寫入 raw_kline_yfinance 的 8 條 SMA。

    ticker=None 時處理全部股票（backfill 或每日全量更新）。
    指定 ticker 時只更新該支（增量用）。
    """
    deps = _import_deps()
    SessionLocal = deps["SessionLocal"]
    compute_and_store_sma = deps["compute_and_store_sma"]

    label = ticker if ticker else "全部股票"
    log.info(f"[SMA] 開始計算 {label}")
    db = SessionLocal()
    try:
        updated = compute_and_store_sma(db, ticker=ticker)
        log.info(f"[SMA] 完成，更新 {updated} 筆")
    except Exception as exc:
        db.rollback()
        log.error(f"[SMA] 失敗：{exc}")
    finally:
        db.close()


def run_monthly_revenue(log: logging.Logger) -> None:
    """更新當月月營收（上個月資料）。"""
    deps = _import_deps()
    crawl_monthly_revenue = deps["crawl_monthly_revenue"]

    today = date.today()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    log.info(f"[月營收] 開始抓取 {year}/{month:02d} 月營收")
    try:
        crawl_monthly_revenue(year, month)
        log.info(f"[月營收] {year}/{month:02d} 完成")
    except Exception as exc:
        log.error(f"[月營收] 失敗：{exc}")


def run_revenue_backfill(
    log: logging.Logger,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
    sleep_min: float = 0.2,
    sleep_max: float = 0.5,
    workers: int = 10,
) -> None:
    """手動補抓歷史月營收（FinMind 多執行緒）。不納入每日自動排程。"""
    deps = _import_deps()
    crawl_monthly_revenue_backfill = deps["crawl_monthly_revenue_backfill"]

    log.info(
        f"[月營收補抓] 起始 {start_year}-{start_month:02d}，"
        f"結束 {end_year}-{end_month:02d}，workers={workers}"
    )
    try:
        crawl_monthly_revenue_backfill(
            start_year, start_month, end_year, end_month,
            sleep_min=sleep_min, sleep_max=sleep_max,
            workers=workers,
        )
        log.info("[月營收補抓] 完成")
    except Exception as exc:
        log.error(f"[月營收補抓] 失敗：{exc}")
        raise


def run_company_info(log: logging.Logger) -> None:
    """更新公司基本資料（新掛牌、下市偵測）。"""
    deps = _import_deps()
    SessionLocal = deps["SessionLocal"]
    ensure_runtime_schema = deps["ensure_runtime_schema"]
    sync_company_info_from_csv = deps["sync_company_info_from_csv"]

    ensure_runtime_schema()
    db = SessionLocal()
    try:
        log.info("[公司資料] 開始同步")
        count = sync_company_info_from_csv(db)
        log.info(f"[公司資料] 完成，寫入 {count} 筆")
    except Exception as exc:
        db.rollback()
        log.error(f"[公司資料] 失敗：{exc}")
    finally:
        db.close()


def run_financials(log: logging.Logger) -> None:
    """更新公司季報財務指標。"""
    deps = _import_deps()
    SessionLocal = deps["SessionLocal"]
    ensure_runtime_schema = deps["ensure_runtime_schema"]
    sync_company_financials = deps["sync_company_financials"]

    ensure_runtime_schema()
    db = SessionLocal()
    try:
        log.info("[財報] 開始同步")
        count = sync_company_financials(db)
        log.info(f"[財報] 完成，寫入 {count} 筆")
    except Exception as exc:
        db.rollback()
        log.error(f"[財報] 失敗：{exc}")
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────
#  主程式
# ────────────────────────────────────────────────────────────────

def main() -> None:
    today = date.today()
    log = setup_logging(today)

    parser = argparse.ArgumentParser(
        description="每日自動排程任務。不帶參數時根據日期自動決定執行項目。"
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=KLINE_BACKFILL_DAYS,
        metavar="N",
        help=f"K 線增量補抓天數（預設 {KLINE_BACKFILL_DAYS}）。首次建議填 10。",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="強制執行全部任務（不論日期，測試用）。",
    )
    parser.add_argument(
        "--task",
        choices=["kline", "revenue", "company-info", "financials", "sma", "revenue-backfill"],
        default=None,
        help="只執行指定的單一任務。revenue-backfill 需搭配 --backfill-months 或 --backfill-from。",
    )
    parser.add_argument(
        "--backfill-months", type=int, metavar="N", default=None,
        help="revenue-backfill 用：補抓最近 N 個月（含上個月）",
    )
    parser.add_argument(
        "--backfill-from", type=str, metavar="YYYY-MM", default=None,
        help="revenue-backfill 用：補抓起始年月，如 2022-01",
    )
    parser.add_argument(
        "--backfill-to", type=str, metavar="YYYY-MM", default=None,
        help="revenue-backfill 用：補抓結束年月（預設為上個月）",
    )
    parser.add_argument(
        "--backfill-sleep-min", type=float, default=0.2, metavar="SEC",
        help="revenue-backfill 用：每檔請求後最小 sleep（秒），預設 0.2",
    )
    parser.add_argument(
        "--backfill-sleep-max", type=float, default=0.5, metavar="SEC",
        help="revenue-backfill 用：每檔請求後最大 sleep（秒），預設 0.5",
    )
    parser.add_argument(
        "--backfill-workers", type=int, default=10, metavar="N",
        help="revenue-backfill 用：並發 worker 數，預設 10",
    )
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(f"  排程任務啟動  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    # 嘗試 FinMind 登入（K 線增量用 yfinance，但萬一需要也備著）
    try:
        from backend.batch_core import configure_finmind_auth
        configure_finmind_auth()
    except Exception:
        pass

    day = today.day
    month = today.month

    # 決定要執行哪些任務
    if args.task == "revenue-backfill":
        # 手動歷史補抓，處理完直接結束，不走一般任務流程
        from backend.monthly_revenue_crawler import default_period as _dp
        end_year, end_month = _dp()
        if args.backfill_to:
            try:
                end_year, end_month = [int(x) for x in args.backfill_to.split("-")]
            except ValueError:
                raise SystemExit("--backfill-to 格式錯誤，應為 YYYY-MM")

        if args.backfill_months:
            total = end_year * 12 + end_month - (args.backfill_months - 1)
            start_year  = (total - 1) // 12
            start_month = total - start_year * 12
        elif args.backfill_from:
            try:
                start_year, start_month = [int(x) for x in args.backfill_from.split("-")]
            except ValueError:
                raise SystemExit("--backfill-from 格式錯誤，應為 YYYY-MM")
        else:
            raise SystemExit(
                "--task revenue-backfill 需搭配 --backfill-months N 或 --backfill-from YYYY-MM"
            )

        log.info("=" * 60)
        log.info("  月營收歷史補抓（MOPS POST）")
        log.info("=" * 60)
        run_revenue_backfill(
            log, start_year, start_month, end_year, end_month,
            sleep_min=args.backfill_sleep_min,
            sleep_max=args.backfill_sleep_max,
            workers=args.backfill_workers,
        )
        log.info("=" * 60)
        log.info(f"  完成  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.info("=" * 60)
        return

    if args.task:
        run_kline   = args.task == "kline"
        run_rev     = args.task == "revenue"
        run_company = args.task == "company-info"
        run_fin     = args.task == "financials"
        run_sma     = args.task == "sma"
    elif args.force_all:
        run_kline = run_rev = run_company = run_fin = run_sma = True
    else:
        run_kline   = True
        run_rev     = day in REVENUE_DAYS
        run_company = day == COMPANY_INFO_DAY
        run_fin     = (month in FINANCIALS_MONTHS and day == FINANCIALS_DAY)
        run_sma     = run_kline  # K 線更新後必跑 SMA

    log.info(f"今日 {today}（{month} 月 {day} 日）執行項目：")
    log.info(f"  K 線增量     : {'[Y]' if run_kline   else '[ ]'}")
    log.info(f"  SMA 更新     : {'[Y]' if run_sma     else '[ ]'}")
    log.info(f"  月營收       : {'[Y]' if run_rev     else '[ ]'}")
    log.info(f"  公司基本資料 : {'[Y]' if run_company else '[ ]'}")
    log.info(f"  財報         : {'[Y]' if run_fin     else '[ ]'}")
    log.info("-" * 60)

    if run_company:
        log.info("── 任務：公司基本資料 ──")
        run_company_info(log)

    if run_kline:
        log.info(f"── 任務：K 線增量更新（補 {args.backfill_days} 天）──")
        run_kline_incremental(args.backfill_days, log)

    if run_sma:
        log.info("── 任務：SMA 更新 ──")
        run_sma_update(log)

    if run_rev:
        log.info("── 任務：月營收更新 ──")
        run_monthly_revenue(log)

    if run_fin:
        log.info("── 任務：財報更新 ──")
        run_financials(log)

    log.info("=" * 60)
    log.info(f"  全部任務完成  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
