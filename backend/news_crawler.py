from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

try:
    from .database import Base, SessionLocal, engine
    from .models import NewsArticle
except ImportError:
    from database import Base, SessionLocal, engine
    from models import NewsArticle

# Yahoo 台股新聞 RSS。
RSS_URL_TEMPLATE = "https://tw.stock.yahoo.com/rss?s={ticker}"
LOCAL_TZ = ZoneInfo("Asia/Taipei")
DEFAULT_TICKERS = ["2330.TW", "0050.TW", "2317.TW"]


def parse_published_at(raw_value: str) -> datetime:
    """Parse RSS time string to timezone-aware datetime rounded to minute."""
    if not raw_value:
        raise ValueError("published_at is empty")

    dt = None
    try:
        dt = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError):
        dt = None

    if dt is None:
        normalized = raw_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)

    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def fetch_rss_items(ticker: str) -> list[dict]:
    url = RSS_URL_TEMPLATE.format(ticker=ticker)
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    root = ElementTree.fromstring(response.content)
    items = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()

        if not title or not link or not pub_date_raw:
            continue

        try:
            published_at = parse_published_at(pub_date_raw)
        except ValueError:
            continue

        items.append(
            {
                "ticker": ticker,
                "title": title,
                "url": link,
                "source": "Yahoo Finance TW",
                "published_at": published_at,
            }
        )

    return items


def upsert_news_items(db: Session, items: list[dict]) -> int:
    if not items:
        return 0

    stmt = insert(NewsArticle).values(items)
    stmt = stmt.on_conflict_do_nothing(
        constraint="uq_news_ticker_url_published_at",
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def run_news_crawler(tickers: list[str] | None = None) -> None:
    Base.metadata.create_all(bind=engine)

    targets = tickers or DEFAULT_TICKERS
    db = SessionLocal()
    try:
        for ticker in targets:
            print(f"[News] Fetching {ticker}...")
            try:
                items = fetch_rss_items(ticker)
                inserted = upsert_news_items(db, items)
                print(f"[News] {ticker}: inserted {inserted} rows")
            except Exception as error:
                db.rollback()
                print(f"[News] {ticker}: failed -> {error}")
    finally:
        db.close()


if __name__ == "__main__":
    run_news_crawler()
