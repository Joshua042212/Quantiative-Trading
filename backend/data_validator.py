"""
data_validator.py — 獨立驗證腳本

從 stock_kline 資料表讀取 is_verified IS NULL 且近 3 年的 K 線記錄，
依日期分群，向 TWSE/TPEx 官方 API 取得當日收盤價，逐筆比對後更新
is_verified 與 verification_error_pct 欄位。
"""

import argparse
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO

import requests
import urllib3
from sqlalchemy import text
from sqlalchemy.orm import Session

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from .database import SessionLocal
except ImportError:
    from database import SessionLocal

# ── API 端點 ────────────────────────────────────────────────────────────────

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TPEX_QUOTES_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"
)

REQUEST_TIMEOUT = 30
MIN_DELAY = 3.0
MAX_DELAY = 6.0
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.twse.com.tw/",
}

# ── 官方資料爬取 ─────────────────────────────────────────────────────────────


def _clean_price(raw: str | None) -> float | None:
    """移除千分位逗號、空白後轉 float；非數字或 '--' 回傳 None。"""
    if raw is None:
        return None
    val = str(raw).strip().replace(",", "").replace("--", "").replace("除", "").replace("權", "")
    if not val or val in ("-", "N/A"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _find_field_index(fields: list[str], keywords: tuple[str, ...]) -> int | None:
    for i, field in enumerate(fields):
        name = str(field).strip()
        if any(keyword in name for keyword in keywords):
            return i
    return None


def _extract_close_map(rows: list[list], code_idx: int, close_idx: int) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        if len(row) <= max(code_idx, close_idx):
            continue

        raw_code = str(row[code_idx]).strip().upper()
        if not re.match(r"^[0-9A-Z]{4,6}$", raw_code):
            continue

        price = _clean_price(str(row[close_idx]))
        if price is not None and price > 0:
            result[raw_code] = price

    return result


def fetch_twse_daily_closes(target_date: date) -> dict[str, float]:
    """
    向 TWSE MI_INDEX API 取得指定日期的上市股票收盤價。
    回傳 {股票代號(不含 .TW 後綴): 收盤價} dict。
    """
    date_str = target_date.strftime("%Y%m%d")
    params = {"response": "json", "date": date_str, "type": "ALLBUT0999"}

    response = requests.get(
        TWSE_MI_INDEX_URL,
        params=params,
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("stat") != "OK":
        return {}

    # 新版 TWSE API 常把欄位放在 tables[n].fields / tables[n].data；舊版仍可能使用 fields8/data8。
    tables: list[dict] = payload.get("tables", [])
    for table in tables:
        fields = table.get("fields", []) or []
        rows = table.get("data", []) or []
        if not fields or not rows:
            continue

        code_idx = _find_field_index(fields, ("證券代號", "代號", "代碼"))
        close_idx = _find_field_index(fields, ("收盤價", "收盤"))
        if code_idx is None or close_idx is None:
            continue

        result = _extract_close_map(rows, code_idx, close_idx)
        if result:
            return result

    for i in range(1, 20):
        fields = payload.get(f"fields{i}", []) or []
        rows = payload.get(f"data{i}", []) or []
        if not fields or not rows:
            continue

        code_idx = _find_field_index(fields, ("證券代號", "代號", "代碼"))
        close_idx = _find_field_index(fields, ("收盤價", "收盤"))
        if code_idx is None or close_idx is None:
            continue

        result = _extract_close_map(rows, code_idx, close_idx)
        if result:
            return result

    return {}


def fetch_tpex_daily_closes(target_date: date) -> dict[str, float]:
    """
    向 TPEx API 取得指定日期的上櫃股票收盤價。
    回傳 {股票代號(不含 .TWO 後綴): 收盤價} dict。
    """
    roc_year = target_date.year - 1911
    date_str = f"{roc_year}/{target_date.month:02d}/{target_date.day:02d}"

    for market_code in ("EW", "AL"):
        params = {"l": "zh-tw", "d": date_str, "se": market_code, "s": "0,asc,0"}
        response = requests.get(
            TPEX_QUOTES_URL,
            params=params,
            headers={**REQUEST_HEADERS, "Referer": "https://www.tpex.org.tw/"},
            timeout=REQUEST_TIMEOUT,
            verify=False,
        )
        response.raise_for_status()
        payload = response.json()

        rows: list[list] = payload.get("aaData", []) or []
        if not rows:
            tables: list[dict] = payload.get("tables", []) or []
            for table in tables:
                rows = table.get("data", []) or []
                fields = table.get("fields", []) or []
                if not rows or not fields:
                    continue

                code_idx = _find_field_index(fields, ("代號", "代碼"))
                close_idx = _find_field_index(fields, ("收盤",))
                if code_idx is None or close_idx is None:
                    continue

                result = _extract_close_map(rows, code_idx, close_idx)
                if result:
                    return result
        else:
            result = _extract_close_map(rows, 0, 2)
            if result:
                return result

    return {}


# ── 資料庫操作 ───────────────────────────────────────────────────────────────


def load_unverified_dates(db: Session, since: date) -> list[date]:
    """取得所有有未驗證記錄的日期，按日期升序排列。"""
    result = db.execute(
        text(
            """
            SELECT DISTINCT date
            FROM stock_kline
            WHERE is_verified IS NULL
              AND date >= :since
            ORDER BY date ASC
            """
        ),
        {"since": since},
    )
    return [row[0] for row in result]


def load_unverified_rows_for_date(db: Session, target_date: date) -> list[dict]:
    """取得指定日期的所有未驗證 K 線記錄。"""
    result = db.execute(
        text(
            """
            SELECT id, ticker, close, adj_close
            FROM stock_kline
            WHERE is_verified IS NULL
              AND date = :target_date
            """
        ),
        {"target_date": target_date},
    )
    return [
        {"id": row[0], "ticker": row[1], "close": row[2], "adj_close": row[3]}
        for row in result
    ]


def update_verification(
    db: Session,
    row_id: int,
    is_verified: bool,
    verification_error_pct: float | None,
) -> None:
    db.execute(
        text(
            """
            UPDATE stock_kline
            SET is_verified = :is_verified,
                verification_error_pct = :error_pct
            WHERE id = :row_id
            """
        ),
        {
            "is_verified": is_verified,
            "error_pct": verification_error_pct,
            "row_id": row_id,
        },
    )


# ── 主驗證邏輯 ───────────────────────────────────────────────────────────────


def validate_date(
    db: Session,
    target_date: date,
    twse_closes: dict[str, float],
    tpex_closes: dict[str, float],
) -> tuple[int, int]:
    """
    對指定日期的所有未驗證記錄進行比對更新。
    回傳 (verified_count, failed_count)。
    """
    rows = load_unverified_rows_for_date(db, target_date)
    if not rows:
        return 0, 0

    verified = 0
    failed = 0
    tolerance_pct = 0.5

    for row in rows:
        ticker: str = row["ticker"]
        db_close_raw = row["close"]
        db_adj_close_raw = row.get("adj_close")
        row_id: int = row["id"]

        try:
            db_close = float(db_close_raw)
        except (TypeError, ValueError):
            continue

        db_adj_close: float | None = None
        if db_adj_close_raw is not None:
            try:
                db_adj_close = float(db_adj_close_raw)
            except (TypeError, ValueError):
                db_adj_close = None

        # 解析代號與市場後綴
        if ticker.endswith(".TW"):
            code = ticker[:-3]
            official_close = twse_closes.get(code)
        elif ticker.endswith(".TWO"):
            code = ticker[:-4]
            official_close = tpex_closes.get(code)
        else:
            continue

        if official_close is None or official_close <= 0:
            continue

        candidate_errors = [abs(db_close - official_close) / official_close * 100]
        if db_adj_close is not None and db_adj_close > 0:
            candidate_errors.append(abs(db_adj_close - official_close) / official_close * 100)

        error_pct = min(candidate_errors)
        is_verified = error_pct <= tolerance_pct
        verification_error_pct = round(error_pct, 6) if not is_verified else None

        update_verification(db, row_id, is_verified, verification_error_pct)

        if is_verified:
            verified += 1
        else:
            failed += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return verified, failed


def run_validation(lookback_years: int = 3) -> None:
    since = date.today() - timedelta(days=lookback_years * 365)
    db = SessionLocal()

    try:
        dates_to_process = load_unverified_dates(db, since)
        total_dates = len(dates_to_process)

        if not total_dates:
            print("[Validator] 沒有需要驗證的記錄。")
            return

        print(f"[Validator] 共 {total_dates} 個日期待驗證（自 {since}）")

        total_verified = 0
        total_failed = 0
        fetch_errors = 0

        for idx, target_date in enumerate(dates_to_process, start=1):
            print(f"[Validator] ({idx}/{total_dates}) 驗證日期 {target_date} ...")

            if target_date.weekday() >= 5:
                print(f"[Validator] {target_date} 為週末，跳過（可能是 yfinance 日期偏移）")
                continue

            # 取得當日官方收盤價
            twse_closes: dict[str, float] = {}
            tpex_closes: dict[str, float] = {}

            try:
                twse_closes = fetch_twse_daily_closes(target_date)
                print(f"[Validator] TWSE {target_date}: {len(twse_closes)} 筆收盤價")
            except Exception as e:
                print(f"[Validator] TWSE {target_date} 取得失敗: {e}")
                fetch_errors += 1

            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            try:
                tpex_closes = fetch_tpex_daily_closes(target_date)
                print(f"[Validator] TPEx {target_date}: {len(tpex_closes)} 筆收盤價")
            except Exception as e:
                print(f"[Validator] TPEx {target_date} 取得失敗: {e}")
                fetch_errors += 1

            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            if not twse_closes and not tpex_closes:
                print(f"[Validator] {target_date} 官方資料空白，跳過（可能為假日或錯誤）")
                continue

            try:
                verified, failed = validate_date(db, target_date, twse_closes, tpex_closes)
                total_verified += verified
                total_failed += failed
                print(f"[Validator] {target_date}: 通過={verified} 失敗={failed}")
            except Exception as e:
                print(f"[Validator] {target_date} 更新資料庫失敗: {e}")

    finally:
        db.close()

    print(
        f"\n[Validator] 完成。總通過={total_verified} 總失敗={total_failed} "
        f"API錯誤={fetch_errors}"
    )


# ── 入口 ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="驗證 stock_kline 資料庫中的 K 線收盤價。")
    parser.add_argument(
        "--lookback-years",
        type=int,
        default=3,
        help="往回驗證幾年的資料，預設 3 年",
    )
    args = parser.parse_args()
    run_validation(lookback_years=args.lookback_years)


if __name__ == "__main__":
    main()
