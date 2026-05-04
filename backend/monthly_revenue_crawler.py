import argparse
import concurrent.futures
import random
import re
import time
import threading
from datetime import datetime
from html.parser import HTMLParser

import requests
import urllib3
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

try:
    from .database import Base, SessionLocal, engine
    from .models import MonthlyRevenue
except ImportError:
    from database import Base, SessionLocal, engine
    from models import MonthlyRevenue

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENAPI_SOURCES = [
    ("TW", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    ("TWO", "https://openapi.twse.com.tw/v1/opendata/t187ap05_P"),
]

# ─── MOPS POST 歷史補抓 ────────────────────────────────────────────────────────
MOPS_REVENUE_URL = "https://mops.twse.com.tw/server-java/t05st09_q"

# CompanyInfo.market → MOPS TYPEK 參數
_MARKET_TO_TYPEK: dict[str, str] = {
    "listed": "sii",
    "etf":    "sii",
    "otc":    "otc",
    "emerging": "rotc",
}


def clean_number(raw: object) -> float | None:
    text = str(raw).strip().replace(",", "")
    if not text or text in {"nan", "--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_roc_year_month(raw: object) -> tuple[int, int] | None:
    text = str(raw).strip()
    match = re.fullmatch(r"(\d{3})(\d{2})", text)
    if not match:
        return None
    year = int(match.group(1)) + 1911
    month = int(match.group(2))
    return year, month


def fetch_openapi_rows(url: str) -> list[dict]:
    response = requests.get(url, timeout=30, verify=False)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"unexpected payload from {url}")
    return payload


def normalize_openapi_rows(
    payload: list[dict],
    market_suffix: str,
    revenue_year: int | None = None,
    revenue_month: int | None = None,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, int, int]] = set()

    for item in payload:
        code = str(item.get("公司代號", "")).strip()
        # 只保留一般股票四位數代號，略過券商等六位數公開發行公司
        if not re.fullmatch(r"\d{4}", code):
            continue

        ym = parse_roc_year_month(item.get("資料年月"))
        if ym is None:
            continue
        year, month = ym

        if revenue_year is not None and revenue_month is not None:
            if year != revenue_year or month != revenue_month:
                continue

        revenue_amount = clean_number(item.get("營業收入-當月營收"))
        if revenue_amount is None:
            continue

        row = {
            "ticker": f"{code}.{market_suffix}",
            "company_name": str(item.get("公司名稱", "")).strip() or None,
            "revenue_year": year,
            "revenue_month": month,
            "revenue_amount": revenue_amount,
            "previous_month_revenue": clean_number(item.get("營業收入-上月營收")),
            "last_year_revenue": clean_number(item.get("營業收入-去年當月營收")),
            "mom_pct": clean_number(item.get("營業收入-上月比較增減(%)")),
            "yoy_pct": clean_number(item.get("營業收入-去年同月增減(%)")),
            "source": "twse_openapi",
        }

        dedupe_key = (row["ticker"], year, month)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(row)

    return rows


def upsert_monthly_revenue(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0

    stmt = insert(MonthlyRevenue).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_monthly_revenue_ticker_period",
        set_={
            "company_name": stmt.excluded.company_name,
            "revenue_amount": stmt.excluded.revenue_amount,
            "previous_month_revenue": stmt.excluded.previous_month_revenue,
            "last_year_revenue": stmt.excluded.last_year_revenue,
            "mom_pct": stmt.excluded.mom_pct,
            "yoy_pct": stmt.excluded.yoy_pct,
            "source": stmt.excluded.source,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def crawl_monthly_revenue(revenue_year: int | None = None, revenue_month: int | None = None) -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        total = 0
        for market_suffix, url in OPENAPI_SOURCES:
            print(f"[Revenue] Fetching {market_suffix} monthly revenue...")
            payload = fetch_openapi_rows(url)
            rows = normalize_openapi_rows(payload, market_suffix, revenue_year, revenue_month)
            count = upsert_monthly_revenue(db, rows)
            total += count
            print(f"[Revenue] {market_suffix} upserted {count} rows")
            time.sleep(random.uniform(2, 5))

        print(f"[Revenue] Done. Total upserted: {total}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ─── MOPS POST 歷史補抓 ────────────────────────────────────────────────────────


def _base_code(ticker: str) -> str:
    """去掉 .TW / .TWO 後綴，回傳純 4 位數股票代號。"""
    return ticker.split(".")[0]


def _ad_to_roc(ad_year: int) -> int:
    return ad_year - 1911


def _iter_year_months(
    start_year: int, start_month: int, end_year: int, end_month: int
) -> list[tuple[int, int]]:
    """回傳 (AD 年, 月) 清單，含頭尾。"""
    result: list[tuple[int, int]] = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        result.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


class _TableParser(HTMLParser):
    """用 stdlib html.parser 擷取 HTML 表格所有儲存格文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("td", "th"):
            self._in_cell = True
            self._cell_buf = []
        elif tag == "tr":
            self._current_row = []
        elif tag == "br" and self._in_cell:
            self._cell_buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._in_cell = False
            self._current_row.append("".join(self._cell_buf).strip())
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)


def fetch_mops_revenue_html(code: str, typek: str, roc_year: int) -> str:
    """POST 到 MOPS t05st09_q，回傳某公司某年度全年月營收 HTML。"""
    data = {
        "id":                code,
        "TYPEK":             typek,
        "year":              str(roc_year),
        "month":             "0",
        "firstin":           "1",
        "off":               "1",
        "queryName":         "co_id",
        "inpuType":          "co_id",
        "TYPEK2":            "",
        "encodeURIComponent": "1",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":      "https://mops.twse.com.tw/mops/web/t05st09",
    }
    resp = requests.post(
        MOPS_REVENUE_URL, data=data, headers=headers, timeout=20, verify=False
    )
    resp.raise_for_status()
    # MOPS 頁面宣告編碼不一定正確，依序嘗試
    for enc in ("utf-8", "big5", "cp950"):
        try:
            return resp.content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return resp.text


def _parse_mops_html(
    html: str,
    code: str,
    market_suffix: str,
    target_months: set[tuple[int, int]],
) -> list[dict]:
    """從 MOPS t05st09_q HTML 解析該公司指定月份的月營收行。

    MOPS 表格欄位順序（標準版）：
      [0] 年度（ROC 3 位）  [1] 月份  [2] 當月營收(千元)
      [3] 上月比較增減(%)   [4] 去年同月增減(%)  ...其餘不取
    """
    parser = _TableParser()
    parser.feed(html)

    results: list[dict] = []
    for row in parser.rows:
        if len(row) < 3:
            continue

        year_raw  = row[0].replace(",", "").replace("\xa0", "").strip()
        month_raw = row[1].replace(",", "").replace("\xa0", "").strip()

        # 民國年（3 位數）
        if not re.fullmatch(r"\d{3}", year_raw):
            continue
        roc_year = int(year_raw)
        ad_year  = roc_year + 1911

        try:
            month = int(month_raw)
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue

        if (ad_year, month) not in target_months:
            continue

        revenue = clean_number(row[2]) if len(row) > 2 else None
        if revenue is None:
            continue

        mom_pct = clean_number(row[3]) if len(row) > 3 else None
        yoy_pct = clean_number(row[4]) if len(row) > 4 else None

        results.append({
            "ticker":                 f"{code}.{market_suffix}",
            "company_name":           None,   # t05st09_q 不含公司名稱欄
            "revenue_year":           ad_year,
            "revenue_month":          month,
            "revenue_amount":         revenue,
            "previous_month_revenue": None,   # MOPS 只提供增減百分比，無絕對值
            "last_year_revenue":      None,
            "mom_pct":                mom_pct,
            "yoy_pct":                yoy_pct,
            "source":                 "mops_post",
        })

    return results


def crawl_monthly_revenue_backfill(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
    sleep_min: float = 0.5,
    sleep_max: float = 2.0,
    workers: int = 10,
    tickers_filter: list[str] | None = None,
) -> None:
    """從 FinMind API 多執行緒補抓歷史月營收，寫入 monthly_revenue 表。

    以 CompanyInfo 股票池為對象，用 ThreadPoolExecutor 並發向 FinMind
    請求月營收資料，解析後批次 upsert 寫回 DB。

    Args:
        start_year/month: 補抓起始年月（AD 年）。
        end_year/month:   補抓結束年月（AD 年），含此月。
        sleep_min/max:    每筆請求完成後的 sleep 區間（秒），控制速率。
        workers:          ThreadPoolExecutor 的並發數，預設 10。
    """
    from FinMind.data import DataLoader

    # 延遲 import 避免循環相依
    try:
        from .nightly_batch import get_company_universe
        from .database import SessionLocal
        from .models import MonthlyRevenue  # noqa: F401 (ensure table exists)
    except ImportError:
        from nightly_batch import get_company_universe
        from database import SessionLocal
        from models import MonthlyRevenue  # noqa: F401

    Base.metadata.create_all(bind=engine)
    db_main = SessionLocal()

    # FinMind 日期格式：YYYY-MM-DD
    # 月營收 date 欄位為「發布月份第一天」= 收入月+1，故 end_date 需多抓一個月
    start_date = f"{start_year}-{start_month:02d}-01"
    if end_month == 12:
        fetch_end_year, fetch_end_month = end_year + 1, 1
    else:
        fetch_end_year, fetch_end_month = end_year, end_month + 1
    end_date = f"{fetch_end_year}-{fetch_end_month:02d}-28"

    target_months: set[tuple[int, int]] = set(
        _iter_year_months(start_year, start_month, end_year, end_month)
    )

    try:
        companies = get_company_universe(db_main)
        # 只處理一般 4 位數股票，ETF / 存託憑證等略過
        companies = [
            c for c in companies
            if re.fullmatch(r"\d{4}", _base_code(c["ticker"]))
        ]

        # 若有指定 tickers，只保留符合的股票
        if tickers_filter:
            filter_codes = {_base_code(t) for t in tickers_filter}
            companies = [c for c in companies if _base_code(c["ticker"]) in filter_codes]
            if not companies:
                raise SystemExit(f"指定的 tickers 在股票池中找不到：{tickers_filter}")

        print(
            f"[Backfill] {len(companies)} 檔股票，"
            f"{start_year}-{start_month:02d} → {end_year}-{end_month:02d}，"
            f"目標 {len(target_months)} 個月份，workers={workers}"
        )

        counter_lock = threading.Lock()
        total_upserted = 0
        completed = 0
        failed: list[str] = []

        def fetch_company(company: dict) -> list[dict]:
            """每個 worker 建立自己的 DataLoader，避免共享 session 競爭。"""
            code          = _base_code(company["ticker"])
            market        = company.get("market") or "listed"
            market_suffix = "TWO" if market == "otc" else "TW"
            ticker        = f"{code}.{market_suffix}"

            dl = DataLoader()
            try:
                df = dl.taiwan_stock_month_revenue(
                    stock_id=code,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                return [{"_error": code, "_msg": str(exc)}]

            time.sleep(random.uniform(sleep_min, sleep_max))

            if df is None or df.empty:
                return []

            rows: list[dict] = []
            for _, row in df.iterrows():
                ym = (int(row["revenue_year"]), int(row["revenue_month"]))
                if ym not in target_months:
                    continue
                rev = row.get("revenue")
                revenue_amount = float(rev) / 1000.0 if rev is not None else None
                rows.append({
                    "ticker":                  ticker,
                    "company_name":            None,
                    "revenue_year":            ym[0],
                    "revenue_month":           ym[1],
                    "revenue_amount":          revenue_amount,
                    "previous_month_revenue":  None,
                    "last_year_revenue":       None,
                    "mom_pct":                 None,
                    "yoy_pct":                 None,
                    "source":                  "finmind",
                })
            return rows

        n = len(companies)
        db_write = SessionLocal()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_map = {executor.submit(fetch_company, c): c for c in companies}
                for future in concurrent.futures.as_completed(future_map):
                    rows = future.result()
                    with counter_lock:
                        completed += 1
                        # 記錄錯誤
                        err_rows = [r for r in rows if "_error" in r]
                        data_rows = [r for r in rows if "_error" not in r]
                        for e in err_rows:
                            failed.append(e["_error"])
                            print(f"  [WARN] {e['_error']}: {e['_msg']}")

                        if data_rows:
                            upserted = upsert_monthly_revenue(db_write, data_rows)
                            total_upserted += upserted

                        if completed % 100 == 0 or completed == n:
                            print(
                                f"  [Backfill] {completed}/{n} 完成，"
                                f"累計寫入 {total_upserted} 筆"
                            )
        finally:
            db_write.close()

        print(
            f"[Backfill] 完成。寫入 {total_upserted} 筆，"
            f"失敗 {len(failed)} 個（{failed[:10]}{'…' if len(failed) > 10 else ''}）"
        )

    except Exception:
        raise
    finally:
        db_main.close()


def default_period() -> tuple[int, int]:
    now = datetime.now()
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def main() -> None:
    default_year, default_month = default_period()

    parser = argparse.ArgumentParser(
        description="Crawl TW monthly revenue data from official open data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 單月全市場（TWSE OpenAPI，預設行為）
  python -m backend.monthly_revenue_crawler --year 2026 --month 3

  # 補抓最近 N 個月歷史（MOPS POST，所有股票）
  python -m backend.monthly_revenue_crawler --backfill-months 24

  # 補抓指定區間
  python -m backend.monthly_revenue_crawler --backfill-from 2022-01 --backfill-to 2026-03

  # 加速（較快但對 MOPS 壓力較大）
  python -m backend.monthly_revenue_crawler --backfill-months 12 --sleep-min 0.3 --sleep-max 1.0
""",
    )
    # 單月 OpenAPI 模式
    parser.add_argument("--year",  type=int, default=default_year,  help="抓取年份（西元），預設為上個月")
    parser.add_argument("--month", type=int, default=default_month, help="抓取月份 1-12，預設為上個月")

    # Backfill 模式（互斥二選一）
    bf_group = parser.add_argument_group("MOPS 歷史補抓選項（擇一使用）")
    bf_group.add_argument(
        "--backfill-months", type=int, metavar="N",
        help="補抓最近 N 個月（以上個月為終點）",
    )
    bf_group.add_argument(
        "--backfill-from", type=str, metavar="YYYY-MM",
        help="補抓起始年月，如 2022-01",
    )
    bf_group.add_argument(
        "--backfill-to", type=str, metavar="YYYY-MM",
        help="補抓結束年月（預設：上個月），如 2026-03",
    )

    # Backfill 速率控制
    bf_group.add_argument("--sleep-min", type=float, default=0.2, metavar="SEC",
                          help="每檔請求後最小 sleep（秒），預設 0.2")
    bf_group.add_argument("--sleep-max", type=float, default=0.5, metavar="SEC",
                          help="每檔請求後最大 sleep（秒），預設 0.5")
    bf_group.add_argument("--workers", type=int, default=10, metavar="N",
                          help="並發 worker 數，預設 10")
    bf_group.add_argument("--tickers", type=str, metavar="CODE[,CODE...]",
                          help="只補抓指定股票代碼（逗號分隔），如 2330,2454，不填則全市場")

    args = parser.parse_args()

    # ── Backfill 模式 ──────────────────────────────────────────────────────────
    if args.backfill_months or args.backfill_from:
        # 決定結束年月
        end_year, end_month = default_period()
        if args.backfill_to:
            try:
                end_year, end_month = [int(x) for x in args.backfill_to.split("-")]
            except ValueError:
                raise SystemExit("--backfill-to 格式錯誤，應為 YYYY-MM（如 2026-03）")

        # 決定起始年月
        if args.backfill_months:
            # 往前推 N 個月（含終點月份共 N 個月）
            total = end_year * 12 + end_month - (args.backfill_months - 1)
            start_year  = (total - 1) // 12
            start_month = total - start_year * 12
        elif args.backfill_from:
            try:
                start_year, start_month = [int(x) for x in args.backfill_from.split("-")]
            except ValueError:
                raise SystemExit("--backfill-from 格式錯誤，應為 YYYY-MM（如 2022-01）")
        else:
            raise SystemExit("請提供 --backfill-months N 或 --backfill-from YYYY-MM")

        if (start_year, start_month) > (end_year, end_month):
            raise SystemExit("起始年月不可晚於結束年月")

        print(f"[Backfill] 範圍：{start_year}-{start_month:02d} → {end_year}-{end_month:02d}")
        tickers_filter = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
        crawl_monthly_revenue_backfill(
            start_year, start_month, end_year, end_month,
            sleep_min=args.sleep_min, sleep_max=args.sleep_max,
            workers=args.workers,
            tickers_filter=tickers_filter,
        )

    # ── 單月 OpenAPI 模式（既有行為）─────────────────────────────────────────
    else:
        if args.month < 1 or args.month > 12:
            raise SystemExit("month must be between 1 and 12")
        crawl_monthly_revenue(args.year, args.month)


if __name__ == "__main__":
    main()
