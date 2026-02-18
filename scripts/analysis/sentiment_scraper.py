"""Reddit and web sentiment scraper for stock tickers."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

BULLISH_WORDS = {"buy", "moon", "calls", "bull", "long", "rocket", "squeeze", "undervalued"}
BEARISH_WORDS = {"sell", "puts", "bear", "short", "crash", "dump", "overvalued", "bubble"}

HEADERS = {"User-Agent": "us-stock-trading-bot/1.0 (educational project)"}
REQUEST_DELAY = 2.0  # seconds between Reddit requests


def _score_text(text: str) -> float:
    """Score a text string for sentiment using keyword matching.

    Args:
        text: Text to analyze.

    Returns:
        Sentiment score between -1 and 1.
    """
    words = set(text.lower().split())
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / total))


def _fetch_reddit_posts(
    ticker: str, subreddit: str, limit: int = 100
) -> list[dict]:
    """Fetch posts mentioning a ticker from a subreddit.

    Args:
        ticker: Stock ticker symbol.
        subreddit: Subreddit name.
        limit: Max posts to fetch.

    Returns:
        List of post dicts with title, created_utc, score.
    """
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    params = {"q": ticker, "sort": "new", "t": "week", "limit": limit, "restrict_sr": "on"}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 429:
            return []
        resp.raise_for_status()
        data = resp.json()
        posts = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            posts.append({
                "title": d.get("title", ""),
                "created_utc": d.get("created_utc", 0),
                "score": d.get("score", 0),
            })
        return posts
    except Exception:
        return []


def scrape_reddit_mentions(
    tickers: list[str],
    subreddits: Optional[list[str]] = None,
    days: int = 7,
) -> pd.DataFrame:
    """Scrape Reddit for ticker mentions and sentiment.

    Args:
        tickers: List of stock ticker symbols.
        subreddits: Subreddits to search (default: wallstreetbets, stocks).
        days: Lookback period in days.

    Returns:
        DataFrame with columns: ticker, mentions, avg_sentiment, momentum.
    """
    if subreddits is None:
        subreddits = ["wallstreetbets", "stocks"]

    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    mid_ts = (datetime.utcnow() - timedelta(days=3)).timestamp()

    results = []
    for ticker in tickers:
        all_posts: list[dict] = []
        for sub in subreddits:
            posts = _fetch_reddit_posts(ticker, sub)
            all_posts.extend(posts)
            time.sleep(REQUEST_DELAY)

        # Filter to date range
        all_posts = [p for p in all_posts if p["created_utc"] >= cutoff_ts]

        if not all_posts:
            results.append({
                "ticker": ticker,
                "mentions": 0,
                "avg_sentiment": 0.0,
                "momentum": 0.0,
            })
            continue

        sentiments = [_score_text(p["title"]) for p in all_posts]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        # Momentum: compare recent (last 3 days) vs older (prior 4 days)
        recent = [_score_text(p["title"]) for p in all_posts if p["created_utc"] >= mid_ts]
        older = [_score_text(p["title"]) for p in all_posts if p["created_utc"] < mid_ts]
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        older_avg = sum(older) / len(older) if older else 0.0
        momentum = recent_avg - older_avg

        results.append({
            "ticker": ticker,
            "mentions": len(all_posts),
            "avg_sentiment": round(avg_sentiment, 4),
            "momentum": round(momentum, 4),
        })

    return pd.DataFrame(results)


def compute_sentiment_score(
    mentions: int, avg_sentiment: float, momentum: float
) -> float:
    """Compute a combined sentiment score.

    Args:
        mentions: Number of mentions.
        avg_sentiment: Average sentiment score.
        momentum: Sentiment momentum (change rate).

    Returns:
        Combined score between -1 and 1.
    """
    # Weight: 50% avg_sentiment, 30% momentum, 20% mention volume signal
    volume_signal = min(mentions / 50.0, 1.0) * (1.0 if avg_sentiment >= 0 else -1.0) * 0.5
    raw = 0.5 * avg_sentiment + 0.3 * momentum + 0.2 * volume_signal
    return max(-1.0, min(1.0, round(raw, 4)))
