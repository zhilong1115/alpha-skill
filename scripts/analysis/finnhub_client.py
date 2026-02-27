"""Finnhub API client — real-time quotes, news, earnings calendar.

Provides a lightweight wrapper around the finnhub-python SDK
for use across the intraday scanner, news analyzer, and pre-market scan.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Get or create a cached Finnhub client."""
    global _client
    if _client is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[2] / ".env")
            import finnhub
            api_key = os.getenv("FINNHUB_API_KEY", "")
            if not api_key:
                raise ValueError("FINNHUB_API_KEY not set in .env")
            _client = finnhub.Client(api_key=api_key)
        except Exception as e:
            logger.error("Failed to init Finnhub client: %s", e)
            return None
    return _client


def get_quote(ticker: str) -> dict | None:
    """Get real-time quote for a ticker.

    Returns dict with: c (current), o (open), h (high), l (low),
    pc (prev close), dp (% change), d (change $).
    """
    client = get_client()
    if not client:
        return None
    try:
        q = client.quote(ticker)
        if q.get("c", 0) > 0:
            return q
    except Exception as e:
        logger.warning("Finnhub quote failed for %s: %s", ticker, e)
    return None


def get_gap_pct(ticker: str) -> float:
    """Get gap % from previous close to current price."""
    q = get_quote(ticker)
    if not q:
        return 0.0
    pc = q.get("pc", 0)
    c = q.get("c", 0)
    if pc > 0 and c > 0:
        return round((c - pc) / pc * 100, 2)
    return 0.0


def get_company_news(ticker: str, days_back: int = 1) -> list[dict]:
    """Get recent news for a company.

    Args:
        ticker: Stock symbol.
        days_back: How many days of news to fetch.

    Returns:
        List of news items with keys: headline, summary, sentiment, datetime, url.
    """
    client = get_client()
    if not client:
        return []
    try:
        today = datetime.now()
        from_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        news = client.company_news(ticker, _from=from_date, to=to_date)
        return news or []
    except Exception as e:
        logger.warning("Finnhub news failed for %s: %s", ticker, e)
        return []


def get_news_sentiment(ticker: str) -> dict:
    """Get aggregated news sentiment from Finnhub.

    Returns:
        Dict with bullishPercent, bearishPercent, buzz (volume), score.
    """
    client = get_client()
    if not client:
        return {}
    try:
        s = client.news_sentiment(ticker)
        return {
            "bullish_pct": s.get("sentiment", {}).get("bullishPercent", 0.5),
            "bearish_pct": s.get("sentiment", {}).get("bearishPercent", 0.5),
            "buzz": s.get("buzz", {}).get("buzz", 0),
            "articles_in_week": s.get("buzz", {}).get("weeklyAverage", 0),
            "score": s.get("companyNewsScore", 0.5),
        }
    except Exception as e:
        logger.warning("Finnhub sentiment failed for %s: %s", ticker, e)
        return {}


def get_todays_earnings() -> list[str]:
    """Get list of tickers reporting earnings today.

    Returns:
        List of ticker symbols reporting today.
    """
    client = get_client()
    if not client:
        return []
    try:
        import socket
        socket.setdefaulttimeout(5)  # 5s max for earnings calendar
        today = datetime.now().strftime("%Y-%m-%d")
        cal = client.earnings_calendar(_from=today, to=today, symbol="")
        entries = cal.get("earningsCalendar", [])
        return [e["symbol"] for e in entries if e.get("symbol")]
    except Exception as e:
        logger.warning("Finnhub earnings calendar failed: %s", e)
        return []
    finally:
        import socket
        socket.setdefaulttimeout(None)


def get_recommendation_trends(ticker: str) -> dict | None:
    """Get analyst recommendation trends (buy/sell/hold counts).

    Returns latest period recommendation data.
    """
    client = get_client()
    if not client:
        return None
    try:
        trends = client.recommendation_trends(ticker)
        if trends:
            latest = trends[0]
            total = latest.get("buy", 0) + latest.get("hold", 0) + latest.get("sell", 0)
            if total > 0:
                buy_pct = latest.get("buy", 0) / total
                return {
                    "buy": latest.get("buy", 0),
                    "hold": latest.get("hold", 0),
                    "sell": latest.get("sell", 0),
                    "strong_buy": latest.get("strongBuy", 0),
                    "strong_sell": latest.get("strongSell", 0),
                    "buy_pct": round(buy_pct, 2),
                    "period": latest.get("period", ""),
                }
    except Exception as e:
        logger.warning("Finnhub recommendation failed for %s: %s", ticker, e)
    return None


def enrich_candidate(ticker: str, include_sentiment: bool = True) -> dict:
    """Enrich a candidate with Finnhub data: quote, news sentiment, earnings flag.

    Args:
        ticker: Stock symbol.
        include_sentiment: Whether to fetch sentiment (slower, uses more API calls).

    Returns:
        Dict with finnhub_quote, gap_pct, has_earnings_today, news_sentiment, recent_headlines.
    """
    result = {
        "ticker": ticker,
        "finnhub_quote": None,
        "gap_pct": 0.0,
        "has_earnings_today": False,
        "news_sentiment": {},
        "recent_headlines": [],
    }

    # Quote + gap
    q = get_quote(ticker)
    if q:
        result["finnhub_quote"] = q
        pc = q.get("pc", 0)
        c = q.get("c", 0)
        if pc > 0 and c > 0:
            result["gap_pct"] = round((c - pc) / pc * 100, 2)

    # Recent news headlines (fast, always fetch)
    news = get_company_news(ticker, days_back=1)
    result["recent_headlines"] = [n.get("headline", "") for n in news[:5]]
    result["news_count_today"] = len(news)

    # Sentiment (optional)
    if include_sentiment:
        result["news_sentiment"] = get_news_sentiment(ticker)

    return result
