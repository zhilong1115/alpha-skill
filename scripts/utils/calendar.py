"""Market calendar and earnings date utilities."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import pytz


NYSE_TZ = pytz.timezone("America/New_York")

# NYSE holidays 2024-2026 (simplified — major holidays only)
_NYSE_HOLIDAYS = {
    # 2025
    datetime(2025, 1, 1), datetime(2025, 1, 20), datetime(2025, 2, 17),
    datetime(2025, 4, 18), datetime(2025, 5, 26), datetime(2025, 6, 19),
    datetime(2025, 7, 4), datetime(2025, 9, 1), datetime(2025, 11, 27),
    datetime(2025, 12, 25),
    # 2026
    datetime(2026, 1, 1), datetime(2026, 1, 19), datetime(2026, 2, 16),
    datetime(2026, 4, 3), datetime(2026, 5, 25), datetime(2026, 6, 19),
    datetime(2026, 7, 3), datetime(2026, 9, 7), datetime(2026, 11, 26),
    datetime(2026, 12, 25),
}


def is_market_open(now: Optional[datetime] = None) -> bool:
    """Check if NYSE is currently open.

    Args:
        now: Optional datetime to check. Defaults to current time.

    Returns:
        True if market is open (weekday, 9:30-16:00 ET, not a holiday).
    """
    if now is None:
        now = datetime.now(NYSE_TZ)
    elif now.tzinfo is None:
        now = NYSE_TZ.localize(now)
    else:
        now = now.astimezone(NYSE_TZ)

    # Weekend check
    if now.weekday() >= 5:
        return False

    # Holiday check
    date_only = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if date_only in _NYSE_HOLIDAYS:
        return False

    # Hours check: 9:30 AM - 4:00 PM ET
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def next_market_open(now: Optional[datetime] = None) -> datetime:
    """Get the next market open datetime.

    Args:
        now: Optional datetime. Defaults to current time.

    Returns:
        Datetime of next market open in ET.
    """
    if now is None:
        now = datetime.now(NYSE_TZ)
    elif now.tzinfo is None:
        now = NYSE_TZ.localize(now)
    else:
        now = now.astimezone(NYSE_TZ)

    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)

    # If already past today's open, start from tomorrow
    if now >= candidate:
        candidate += timedelta(days=1)

    # Skip weekends and holidays
    for _ in range(10):
        if candidate.weekday() < 5:
            date_only = candidate.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            if date_only not in _NYSE_HOLIDAYS:
                return candidate
        candidate += timedelta(days=1)

    return candidate


def get_earnings_calendar(
    tickers: list[str], days_ahead: int = 14
) -> pd.DataFrame:
    """Get upcoming earnings dates for tickers.

    Args:
        tickers: List of stock ticker symbols.
        days_ahead: Number of days to look ahead.

    Returns:
        DataFrame with columns [ticker, earnings_date].
    """
    from scripts.strategies.earnings_event import get_upcoming_earnings
    return get_upcoming_earnings(tickers, days_ahead=days_ahead)
