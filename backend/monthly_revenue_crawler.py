import argparse
import random
import re
import time
from datetime import datetime

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


def default_period() -> tuple[int, int]:
    now = datetime.now()
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def main() -> None:
    default_year, default_month = default_period()

    parser = argparse.ArgumentParser(description="Crawl TW monthly revenue data from official open data.")
    parser.add_argument("--year", type=int, default=default_year, help="Gregorian year, e.g. 2026")
    parser.add_argument("--month", type=int, default=default_month, help="Month number 1-12")
    args = parser.parse_args()

    if args.month < 1 or args.month > 12:
        raise SystemExit("month must be between 1 and 12")

    crawl_monthly_revenue(args.year, args.month)


if __name__ == "__main__":
    main()
