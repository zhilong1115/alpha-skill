"""Sentiment-driven momentum strategy combining Reddit and news signals."""

from __future__ import annotations

from typing import Optional

import pandas as pd


def generate_sentiment_signals(tickers: list[str]) -> pd.DataFrame:
    """Generate sentiment-based trading signals for a list of tickers.

    Combines Reddit sentiment, news sentiment, and price momentum.
    Applies contrarian logic for extreme sentiment readings.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns: ticker, signal_name, value, score.
    """
    from scripts.analysis.sentiment_scraper import scrape_reddit_mentions, compute_sentiment_score
    from scripts.analysis.news_analyzer import score_news_sentiment, get_recent_news
    from scripts.core.data_pipeline import get_price_data

    rows = []

    # Get Reddit data for all tickers at once
    try:
        reddit_df = scrape_reddit_mentions(tickers)
    except Exception:
        reddit_df = pd.DataFrame(columns=["ticker", "mentions", "avg_sentiment", "momentum"])

    for ticker in tickers:
        try:
            # Reddit sentiment
            reddit_row = reddit_df[reddit_df["ticker"] == ticker]
            if not reddit_row.empty:
                r = reddit_row.iloc[0]
                reddit_score = compute_sentiment_score(
                    int(r["mentions"]), float(r["avg_sentiment"]), float(r["momentum"])
                )
            else:
                reddit_score = 0.0

            # News sentiment
            try:
                news = get_recent_news(ticker)
                news_score = score_news_sentiment(news)
            except Exception:
                news_score = 0.0

            # Combined raw sentiment
            combined = 0.6 * reddit_score + 0.4 * news_score

            # Contrarian adjustment for extremes
            adjusted = _apply_contrarian(combined)

            # Price momentum confirmation
            try:
                df = get_price_data(ticker, period="1mo")
                if len(df) >= 5:
                    price_return = (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1.0)
                    price_direction = 1.0 if price_return > 0 else -1.0
                    # Strengthen signal if sentiment and price agree
                    if (adjusted > 0 and price_direction > 0) or (adjusted < 0 and price_direction < 0):
                        adjusted *= 1.2
                    adjusted = max(-1.0, min(1.0, adjusted))
            except Exception:
                pass

            rows.append({
                "ticker": ticker,
                "signal_name": "sentiment_combined",
                "value": round(combined, 4),
                "score": round(adjusted, 4),
            })

            # Individual component signals
            rows.append({
                "ticker": ticker,
                "signal_name": "reddit_sentiment",
                "value": round(reddit_score, 4),
                "score": round(reddit_score, 4),
            })
            rows.append({
                "ticker": ticker,
                "signal_name": "news_sentiment",
                "value": round(news_score, 4),
                "score": round(news_score, 4),
            })

        except Exception:
            rows.append({
                "ticker": ticker,
                "signal_name": "sentiment_combined",
                "value": 0.0,
                "score": 0.0,
            })

    return pd.DataFrame(rows)


def _apply_contrarian(score: float) -> float:
    """Apply contrarian adjustment to extreme sentiment.

    Extreme bullish (>0.7) gets pulled back slightly; extreme bearish (<-0.7)
    gets pulled back slightly. This reflects the idea that crowd extremes
    often precede reversals.

    Args:
        score: Raw sentiment score.

    Returns:
        Adjusted score between -1 and 1.
    """
    if score > 0.7:
        # Dampen extreme bullishness
        return score * 0.5
    elif score < -0.7:
        # Dampen extreme bearishness
        return score * 0.5
    return score
