from sqlalchemy import text

try:
    from .database import engine
except ImportError:
    from database import engine


def main() -> None:
    with engine.connect() as conn:
        total = conn.execute(text("select count(*) from stock_kline")).scalar()
        tickers = conn.execute(text("select count(distinct ticker) from stock_kline")).scalar()
        unverified = conn.execute(
            text("select count(*) from stock_kline where is_verified is false")
        ).scalar()
        has_crawl_log = conn.execute(
            text(
                "select exists (select 1 from information_schema.tables where table_name='crawl_log')"
            )
        ).scalar()

        print(
            f"stock_kline total={total}, distinct_ticker={tickers}, "
            f"unverified={unverified}, has_crawl_log={has_crawl_log}"
        )

        if has_crawl_log:
            rows = conn.execute(
                text(
                    """
                    select executed_at, success_count, failed_tickers, was_blocked
                    from crawl_log
                    order by executed_at desc
                    limit 5
                    """
                )
            ).fetchall()
            print("crawl_log recent:")
            for row in rows:
                print(row)


if __name__ == "__main__":
    main()
