"""
OHLCV candle fetcher — downloads historical data from Bitso and stores it locally.
"""
import time
from datetime import datetime, timedelta

from exchange.client import BitsoClient
from data.db import upsert_candles, get_candles


def fetch_and_store(
    client: BitsoClient,
    book: str = "btc_mxn",
    time_bucket: str = "1d",
    days_back: int = 365,
) -> int:
    """
    Fetch up to `days_back` days of candles from Bitso and persist to SQLite.
    Returns total number of new candles stored.
    """
    end_ts = int(time.time())
    start_ts = end_ts - (days_back * 86400)
    total_inserted = 0

    # Bitso limits candle responses; paginate in 90-day windows to be safe
    window_seconds = 90 * 86400
    cursor_start = start_ts

    while cursor_start < end_ts:
        cursor_end = min(cursor_start + window_seconds, end_ts)
        candles = client.get_candles(
            book=book,
            time_bucket=time_bucket,
            start=cursor_start,
            end=cursor_end,
        )
        if candles:
            inserted = upsert_candles(book, time_bucket, candles)
            total_inserted += inserted
            print(f"  Fetched {len(candles)} candles ({inserted} new) "
                  f"[{datetime.utcfromtimestamp(cursor_start).date()} → "
                  f"{datetime.utcfromtimestamp(cursor_end).date()}]")
        cursor_start = cursor_end
        time.sleep(0.2)  # stay within rate limits

    return total_inserted


def load_candles(
    book: str,
    time_bucket: str,
    start: datetime = None,
    end: datetime = None,
) -> list:
    """Load candles from SQLite (no network call)."""
    rows = get_candles(book, time_bucket, start, end)
    return rows
