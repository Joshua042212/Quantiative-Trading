from FinMind.data import DataLoader

api = DataLoader()
# Test full backfill date range for 2330
try:
    df = api.taiwan_stock_month_revenue(stock_id="2330", start_date="2016-01-01", end_date="2026-04-01")
    print("Full range rows:", len(df))
    print(df.tail(5))
except Exception as e:
    print("Error with full range:", type(e).__name__, e)

# Also try year by year to see if splitting works
try:
    df2 = api.taiwan_stock_month_revenue(stock_id="2330", start_date="2020-01-01", end_date="2026-04-01")
    print("\n2020-2026 rows:", len(df2))
except Exception as e:
    print("Error 2020-2026:", type(e).__name__, e)


db.close()
