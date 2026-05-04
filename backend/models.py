from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, text

try:
    from .database import Base
except ImportError:
    from database import Base


class StockKline(Base):
    __tablename__ = "stock_kline"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_ticker_date"),
        Index(
            "ix_stock_kline_unverified_date",
            "date",
            postgresql_where=text("is_verified IS NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), index=True, nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    adj_open = Column(Float, nullable=True)
    adj_high = Column(Float, nullable=True)
    adj_low = Column(Float, nullable=True)
    adj_close = Column(Float, nullable=True)
    volume = Column(Float, nullable=False)
    is_verified = Column(Boolean, nullable=True)
    data_source = Column(String(64), nullable=True)
    error_tag = Column(String(255), nullable=True)
    verification_error_pct = Column(Float, nullable=True)
    has_anomaly = Column(Boolean, nullable=False, default=False)
    anomaly_reason = Column(String(512), nullable=True)


class CrawlLog(Base):
    __tablename__ = "crawl_log"

    id = Column(Integer, primary_key=True, index=True)
    executed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    success_count = Column(Integer, nullable=False, default=0)
    failed_tickers = Column(Text, nullable=True)
    was_blocked = Column(Boolean, nullable=False, default=False)


class NewsArticle(Base):
    __tablename__ = "news_article"
    __table_args__ = (
        UniqueConstraint("ticker", "url", "published_at", name="uq_news_ticker_url_published_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), index=True, nullable=False)
    title = Column(String(512), nullable=False)
    url = Column(String(1024), nullable=False)
    source = Column(String(128), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)


class EtfPremiumDiscount(Base):
    __tablename__ = "etf_premium_discount"
    __table_args__ = (UniqueConstraint("date", "ticker", name="uq_etf_pd_date_ticker"),)

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    nav = Column(Float, nullable=False)
    market_price = Column(Float, nullable=False)
    premium_discount_pct = Column(Float, nullable=False)


class MonthlyRevenue(Base):
    __tablename__ = "monthly_revenue"
    __table_args__ = (
        UniqueConstraint("ticker", "revenue_year", "revenue_month", name="uq_monthly_revenue_ticker_period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    company_name = Column(String(255), nullable=True)
    revenue_year = Column(Integer, nullable=False, index=True)
    revenue_month = Column(Integer, nullable=False, index=True)
    revenue_amount = Column(Float, nullable=False)
    previous_month_revenue = Column(Float, nullable=True)
    last_year_revenue = Column(Float, nullable=True)
    mom_pct = Column(Float, nullable=True)
    yoy_pct = Column(Float, nullable=True)
    source = Column(String(32), nullable=True)


class UnsupportedTicker(Base):
    __tablename__ = "unsupported_ticker"
    __table_args__ = (UniqueConstraint("ticker", name="uq_unsupported_ticker"),)

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    reason = Column(String(255), nullable=True)
    source = Column(String(32), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)


class CompanyInfo(Base):
    __tablename__ = "company_info"

    ticker = Column(String(32), primary_key=True, index=True)
    short_name = Column(String(255), nullable=True)
    company_name = Column(String(255), nullable=True)
    sector = Column(String(64), nullable=True)
    capital = Column(BigInteger, nullable=True)
    market = Column(String(32), nullable=True)
    website = Column(String(1024), nullable=True)


class CompanyFinancials(Base):
    __tablename__ = "company_financials"
    __table_args__ = (
        UniqueConstraint("ticker", "report_date", "metric", name="uq_company_financials_ticker_date_metric"),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    metric = Column(String(128), nullable=False)
    value = Column(Float, nullable=True)
    source = Column(String(32), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, index=True)


class RawKlineYFinance(Base):
    """yfinance 原始 K 線資料，保留未還原價格、Adj Close、除息與分割事件。

    與 stock_kline（FinMind 來源）並存，供日後交叉比對驗證使用。
    """

    __tablename__ = "raw_kline_yfinance"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_raw_yf_ticker_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    adj_close = Column(Float, nullable=True)
    volume = Column(BigInteger, nullable=True)
    dividends = Column(Float, nullable=True)
    stock_splits = Column(Float, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    sma_2 = Column(Float, nullable=True)
    sma_5 = Column(Float, nullable=True)
    sma_10 = Column(Float, nullable=True)
    sma_20 = Column(Float, nullable=True)
    sma_30 = Column(Float, nullable=True)
    sma_60 = Column(Float, nullable=True)
    sma_120 = Column(Float, nullable=True)
    sma_240 = Column(Float, nullable=True)
