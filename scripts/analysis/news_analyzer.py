"""News sentiment analysis using yfinance news data."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import yfinance as yf

BULLISH_WORDS = {
    "buy", "moon", "calls", "bull", "long", "rocket", "squeeze", "undervalued",
    "upgrade", "beat", "surge", "rally", "growth", "record", "strong", "raise",
    "outperform", "bullish", "positive", "gain", "soar", "boost",
}
BEARISH_WORDS = {
    "sell", "puts", "bear", "short", "crash", "dump", "overvalued", "bubble",
    "downgrade", "miss", "plunge", "decline", "weak", "cut", "underperform",
    "bearish", "negative", "loss", "drop", "slump", "warning",
}


def get_recent_news(ticker: str, count: int = 10) -> list[dict]:
    """Fetch recent news for a ticker via yfinance.

    Args:
        ticker: Stock ticker symbol.
        count: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, link, publisher, date.
    """
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        results = []
        for item in news[:count]:
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "publisher": item.get("publisher", ""),
                "date": item.get("providerPublishTime", ""),
            })
        return results
    except Exception:
        return []


def _score_text(text: str) -> float:
    """Score a text string for sentiment.

    Args:
        text: Text to analyze.

    Returns:
        Score between -1 and 1.
    """
    words = set(text.lower().split())
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / total))


def score_news_sentiment(news_items: list[dict]) -> float:
    """Score overall news sentiment from a list of news items.

    Args:
        news_items: List of news dicts (must have 'title' key).

    Returns:
        Average sentiment score between -1 and 1.
    """
    if not news_items:
        return 0.0
    scores = [_score_text(item.get("title", "")) for item in news_items]
    return round(sum(scores) / len(scores), 4)


def generate_news_signals(tickers: list[str]) -> pd.DataFrame:
    """Generate news-based trading signals for a list of tickers.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns: ticker, signal_name, value, score.
    """
    rows = []
    for ticker in tickers:
        try:
            news = get_recent_news(ticker)
            sentiment = score_news_sentiment(news)
            rows.append({
                "ticker": ticker,
                "signal_name": "news_sentiment",
                "value": len(news),
                "score": sentiment,
            })
        except Exception:
            rows.append({
                "ticker": ticker,
                "signal_name": "news_sentiment",
                "value": 0,
                "score": 0.0,
            })
    return pd.DataFrame(rows)
