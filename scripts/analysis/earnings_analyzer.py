"""Earnings analysis: transcript sentiment, guidance comparison, and surprise detection."""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

import yfinance as yf

POSITIVE_PHRASES = [
    "strong growth", "exceeded expectations", "raising guidance", "record revenue",
    "ahead of plan", "confident", "accelerating", "beat expectations", "strong demand",
    "margin expansion", "robust", "outperformed", "positive momentum", "raising outlook",
]
NEGATIVE_PHRASES = [
    "headwinds", "challenging environment", "below expectations", "lowering guidance",
    "uncertain", "cautious", "decelerating", "missed expectations", "weak demand",
    "margin pressure", "soft", "underperformed", "negative momentum", "lowering outlook",
]
BUSINESS_TERMS = [
    "revenue", "earnings", "margins", "growth", "guidance", "demand", "supply",
    "market share", "innovation", "ai", "cloud", "subscription", "recurring",
    "operating income", "free cash flow", "capital expenditure", "inventory",
    "backlog", "pipeline", "customers", "partnerships", "costs", "pricing",
    "competition", "regulation", "expansion", "restructuring", "acquisition",
]


def analyze_earnings_transcript(text: str) -> dict:
    """Analyze an earnings call transcript for sentiment and key topics.

    Uses keyword/phrase analysis to determine tone, sentiment, and guidance changes.

    Args:
        text: Full text of earnings call transcript.

    Returns:
        Dict with keys: tone, sentiment, key_topics, guidance_change, confidence, summary.
    """
    if not text or not text.strip():
        return {
            "tone": "neutral",
            "sentiment": 0.0,
            "key_topics": [],
            "guidance_change": "unchanged",
            "confidence": 0.0,
            "summary": "No transcript text provided.",
        }

    text_lower = text.lower()

    # Count positive and negative phrases
    pos_count = sum(text_lower.count(phrase) for phrase in POSITIVE_PHRASES)
    neg_count = sum(text_lower.count(phrase) for phrase in NEGATIVE_PHRASES)
    total = pos_count + neg_count

    # Sentiment score
    if total == 0:
        sentiment = 0.0
    else:
        sentiment = round((pos_count - neg_count) / total, 4)

    # Tone
    if sentiment > 0.3:
        tone = "bullish"
    elif sentiment > 0.1:
        tone = "slightly_bullish"
    elif sentiment < -0.3:
        tone = "bearish"
    elif sentiment < -0.1:
        tone = "slightly_bearish"
    else:
        tone = "neutral"

    # Key topics by frequency
    topic_counts = Counter()
    for term in BUSINESS_TERMS:
        count = text_lower.count(term)
        if count > 0:
            topic_counts[term] = count
    key_topics = [t for t, _ in topic_counts.most_common(5)]

    # Guidance change detection
    guidance_change = "unchanged"
    if any(phrase in text_lower for phrase in ["raising guidance", "raising outlook", "raised guidance"]):
        guidance_change = "raised"
    elif any(phrase in text_lower for phrase in ["lowering guidance", "lowering outlook", "lowered guidance"]):
        guidance_change = "lowered"
    elif any(phrase in text_lower for phrase in ["withdrawing guidance", "withdrawn guidance", "suspending guidance"]):
        guidance_change = "withdrawn"

    # Confidence based on how much signal we found
    confidence = min(1.0, total / 20.0)

    summary_parts = [f"Tone: {tone}", f"Sentiment: {sentiment:+.2f}"]
    if key_topics:
        summary_parts.append(f"Key topics: {', '.join(key_topics[:3])}")
    if guidance_change != "unchanged":
        summary_parts.append(f"Guidance: {guidance_change}")

    return {
        "tone": tone,
        "sentiment": sentiment,
        "key_topics": key_topics,
        "guidance_change": guidance_change,
        "confidence": round(confidence, 4),
        "summary": ". ".join(summary_parts) + ".",
    }


def compare_guidance(
    current: dict, previous: Optional[dict] = None
) -> dict:
    """Compare current guidance with previous quarter.

    Args:
        current: Current guidance dict with keys like revenue_low, revenue_high, eps_low, eps_high.
        previous: Previous guidance dict with same structure.

    Returns:
        Dict with changes and direction.
    """
    if previous is None:
        return {
            "direction": "neutral",
            "revenue_change": None,
            "eps_change": None,
            "details": "No previous guidance available for comparison.",
        }

    result = {"direction": "neutral", "details": ""}
    changes = []

    for metric in ["revenue", "eps"]:
        curr_mid = None
        prev_mid = None
        low_key = f"{metric}_low"
        high_key = f"{metric}_high"

        if low_key in current and high_key in current:
            curr_mid = (current[low_key] + current[high_key]) / 2
        if low_key in previous and high_key in previous:
            prev_mid = (previous[low_key] + previous[high_key]) / 2

        if curr_mid is not None and prev_mid is not None and prev_mid != 0:
            pct = (curr_mid / prev_mid - 1.0) * 100
            result[f"{metric}_change"] = round(pct, 2)
            if pct > 1:
                changes.append(f"{metric} raised")
            elif pct < -1:
                changes.append(f"{metric} lowered")
        else:
            result[f"{metric}_change"] = None

    if any("raised" in c for c in changes):
        result["direction"] = "positive"
    elif any("lowered" in c for c in changes):
        result["direction"] = "negative"

    result["details"] = "; ".join(changes) if changes else "No significant changes."
    return result


def analyze_earnings_surprise(ticker: str) -> dict:
    """Analyze earnings surprise for a ticker using yfinance data.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with surprise_pct, beat (bool), magnitude, or empty on failure.
    """
    try:
        t = yf.Ticker(ticker)
        dates = t.earnings_dates
        if dates is None or dates.empty:
            return {"surprise_pct": None, "beat": None, "magnitude": None}

        # Find the most recent row with both actual and estimate
        for _, row in dates.iterrows():
            actual = row.get("Reported EPS")
            estimate = row.get("EPS Estimate")
            if actual is not None and estimate is not None:
                try:
                    actual = float(actual)
                    estimate = float(estimate)
                except (ValueError, TypeError):
                    continue
                if estimate != 0:
                    surprise_pct = round((actual - estimate) / abs(estimate) * 100, 2)
                else:
                    surprise_pct = 0.0
                return {
                    "surprise_pct": surprise_pct,
                    "beat": actual > estimate,
                    "magnitude": round(actual - estimate, 4),
                }

        return {"surprise_pct": None, "beat": None, "magnitude": None}
    except Exception:
        return {"surprise_pct": None, "beat": None, "magnitude": None}
