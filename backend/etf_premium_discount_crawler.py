from datetime import date

import requests
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

try:
    from .database import Base, SessionLocal, engine
    from .models import EtfPremiumDiscount
except ImportError:
    from database import Base, SessionLocal, engine
    from models import EtfPremiumDiscount

# TWSE OpenAPI ETF 淨值與折溢價資料。
TWSE_ETF_API_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BFI82U"


def parse_twse_date(raw_value: str) -> date:
    value = (raw_value or "").strip().replace("-", "/")
    if not value:
        raise ValueError("date is empty")

    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError(f"unsupported date format: {raw_value}")

    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])

    # TWSE 常見民國年格式 (例如 114/04/12)。
    if year < 1911:
        year += 1911

    return date(year, month, day)


def parse_float(raw_value: str) -> float:
    text = (str(raw_value) if raw_value is not None else "").strip()
    if not text:
        raise ValueError("numeric value is empty")

    cleaned = text.replace(",", "").replace("%", "")
    return float(cleaned)


def pick_first(data: dict, keys: list[str]) -> str:
    for key in keys:
        if key in data and str(data[key]).strip() != "":
            return str(data[key]).strip()
    raise KeyError(f"none of keys found: {keys}")


def fetch_twse_rows() -> list[dict]:
    response = requests.get(TWSE_ETF_API_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("unexpected TWSE payload format")

    cleaned_rows = []
    for row in payload:
        try:
            ticker = pick_first(row, ["證券代號", "基金代號", "ticker", "Ticker"])
            trade_date = parse_twse_date(pick_first(row, ["日期", "資料日期", "date", "Date"]))
            nav = parse_float(pick_first(row, ["基金淨值", "淨值", "NAV", "nav"]))
            market_price = parse_float(
                pick_first(row, ["市價", "收盤價", "市場價格", "MarketPrice", "market_price"])
            )
            premium_discount_pct = parse_float(
                pick_first(
                    row,
                    [
                        "折溢價(%)",
                        "折溢價百分比",
                        "折溢價",
                        "PremiumDiscountPct",
                        "premium_discount_pct",
                    ],
                )
            )
        except (KeyError, ValueError):
            continue

        cleaned_rows.append(
            {
                "date": trade_date,
                "ticker": ticker,
                "nav": nav,
                "market_price": market_price,
                "premium_discount_pct": premium_discount_pct,
            }
        )

    return cleaned_rows


def upsert_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0

    stmt = insert(EtfPremiumDiscount).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_etf_pd_date_ticker",
        set_={
            "nav": stmt.excluded.nav,
            "market_price": stmt.excluded.market_price,
            "premium_discount_pct": stmt.excluded.premium_discount_pct,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def run_etf_premium_discount_crawler() -> None:
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        rows = fetch_twse_rows()
        affected = upsert_rows(db, rows)
        print(f"[ETF] upserted {affected} rows")
    except Exception as error:
        db.rollback()
        print(f"[ETF] failed -> {error}")
    finally:
        db.close()


if __name__ == "__main__":
    run_etf_premium_discount_crawler()
