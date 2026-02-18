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


def discover_trending_tickers(
    subreddits: list[str] | None = None,
    min_mentions: int = 3,
) -> list[dict]:
    """Discover which tickers are trending on Reddit right now.

    Unlike scrape_reddit_mentions (which scores known tickers), this
    DISCOVERS new tickers from post titles.

    Args:
        subreddits: Subreddits to scan.
        min_mentions: Minimum mentions to include.

    Returns:
        List of dicts sorted by mention count:
        {ticker, mentions, subreddits_found_in, sample_titles, sentiment_hint}
    """
    import re
    from collections import defaultdict

    if subreddits is None:
        subreddits = ["wallstreetbets", "stocks", "pennystocks", "shortsqueeze"]

    # Common words to filter out
    STOP_WORDS = {
        "I", "A", "AM", "PM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
        "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO", "UP",
        "US", "WE", "CEO", "CFO", "CTO", "COO", "IPO", "ETF", "SEC", "FDA", "EPS",
        "ATH", "DD", "YOLO", "FOMO", "IMO", "TIL", "PSA", "FYI", "LOL", "OMG",
        "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAD", "HER",
        "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW",
        "OLD", "SEE", "WAY", "WHO", "DID", "GET", "GOT", "LET", "SAY", "SHE", "TOO",
        "USE", "RUN", "WIN", "BIG", "TOP", "LOW", "HIGH", "JUST", "VERY",
        "EDIT", "TLDR", "LMAO", "WHAT", "WITH", "THIS", "THAT", "FROM", "THEM",
        "THEN", "THAN", "WHEN", "WILL", "MORE", "MOST", "SOME", "BEEN", "HAVE",
        "EACH", "MAKE", "LIKE", "LONG", "LOOK", "MANY", "OVER", "SUCH", "TAKE",
        "ONLY", "ALSO", "BACK", "YEAR", "INTO", "YOUR", "NEXT", "FREE", "BEST",
        "GOOD", "WELL", "EVEN", "HERE", "MUCH", "STILL", "KEEP", "HOLD",
        "SELL", "BUY", "CALL", "PUTS", "PUT", "GAIN", "LOSS", "MOVE", "PLAY",
        "RIP", "PUMP", "DUMP", "MOON", "BEAR", "BULL", "OPEN", "DOWN",
    }

    ticker_re = re.compile(r'\$([A-Z]{2,5})\b|(?<![a-zA-Z])([A-Z]{2,5})(?![a-zA-Z])')

    # {ticker: {mentions, subreddits, titles}}
    ticker_data: dict[str, dict] = defaultdict(lambda: {
        "mentions": 0, "subreddits": set(), "titles": []
    })

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json"
        try:
            resp = requests.get(url, headers=HEADERS, params={"limit": 50}, timeout=10)
            if resp.status_code != 200:
                time.sleep(REQUEST_DELAY)
                continue
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                title = child.get("data", {}).get("title", "")
                matches = ticker_re.findall(title)
                for dollar, bare in matches:
                    t = dollar or bare
                    if t in STOP_WORDS:
                        continue
                    td = ticker_data[t]
                    td["mentions"] += 1
                    td["subreddits"].add(sub)
                    if len(td["titles"]) < 3:
                        td["titles"].append(title[:100])
        except Exception:
            pass
        time.sleep(REQUEST_DELAY)

    results = []
    for ticker, info in ticker_data.items():
        if info["mentions"] >= min_mentions:
            # Quick sentiment hint from titles
            combined = " ".join(info["titles"])
            hint = _score_text(combined)
            results.append({
                "ticker": ticker,
                "mentions": info["mentions"],
                "subreddits_found_in": sorted(info["subreddits"]),
                "sample_titles": info["titles"],
                "sentiment_hint": round(hint, 3),
            })

    results.sort(key=lambda x: x["mentions"], reverse=True)
    return results


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
