"""Multi-agent debate framework for stock analysis.

Provides bull/bear case construction and resolution. Sprint 4 will add LLM reasoning.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def create_bull_case(
    ticker: str,
    signals_df: pd.DataFrame,
    news: list[dict],
    sentiment: float,
) -> dict:
    """Construct a bullish thesis from available data.

    Args:
        ticker: Stock ticker symbol.
        signals_df: DataFrame of signals (ticker, signal_name, value, score).
        news: List of news dicts with 'title' key.
        sentiment: Overall sentiment score.

    Returns:
        Dict with thesis, evidence (list), and confidence (0-1).
    """
    evidence = []

    # Bullish signals
    if signals_df is not None and not signals_df.empty:
        ticker_signals = signals_df[signals_df["ticker"] == ticker]
        bullish = ticker_signals[ticker_signals["score"] > 0]
        for _, row in bullish.iterrows():
            evidence.append(f"{row['signal_name']}: score={row['score']:+.3f}")

    # Positive news
    for item in (news or []):
        title = item.get("title", "").lower()
        if any(w in title for w in ["beat", "surge", "rally", "growth", "upgrade", "strong"]):
            evidence.append(f"News: {item.get('title', '')[:80]}")

    # Positive sentiment
    if sentiment > 0.1:
        evidence.append(f"Market sentiment: {sentiment:+.3f}")

    confidence = min(1.0, len(evidence) / 5.0) if evidence else 0.0

    thesis = f"Bullish on {ticker}: {len(evidence)} supporting factors."
    if not evidence:
        thesis = f"Weak bull case for {ticker}: no strong supporting evidence."

    return {
        "thesis": thesis,
        "evidence": evidence,
        "confidence": round(confidence, 4),
    }


def create_bear_case(
    ticker: str,
    signals_df: pd.DataFrame,
    news: list[dict],
    sentiment: float,
) -> dict:
    """Construct a bearish thesis from available data.

    Args:
        ticker: Stock ticker symbol.
        signals_df: DataFrame of signals (ticker, signal_name, value, score).
        news: List of news dicts with 'title' key.
        sentiment: Overall sentiment score.

    Returns:
        Dict with thesis, evidence (list), and confidence (0-1).
    """
    evidence = []

    # Bearish signals
    if signals_df is not None and not signals_df.empty:
        ticker_signals = signals_df[signals_df["ticker"] == ticker]
        bearish = ticker_signals[ticker_signals["score"] < 0]
        for _, row in bearish.iterrows():
            evidence.append(f"{row['signal_name']}: score={row['score']:+.3f}")

    # Negative news
    for item in (news or []):
        title = item.get("title", "").lower()
        if any(w in title for w in ["miss", "plunge", "decline", "weak", "downgrade", "crash"]):
            evidence.append(f"News: {item.get('title', '')[:80]}")

    # Negative sentiment
    if sentiment < -0.1:
        evidence.append(f"Market sentiment: {sentiment:+.3f}")

    confidence = min(1.0, len(evidence) / 5.0) if evidence else 0.0

    thesis = f"Bearish on {ticker}: {len(evidence)} risk factors."
    if not evidence:
        thesis = f"Weak bear case for {ticker}: no strong risk evidence."

    return {
        "thesis": thesis,
        "evidence": evidence,
        "confidence": round(confidence, 4),
    }


def resolve_debate(bull_case: dict, bear_case: dict) -> dict:
    """Resolve a bull vs bear debate to produce a trading verdict.

    Requires >60% confidence differential for a buy/sell verdict; otherwise hold.

    Args:
        bull_case: Bull case dict from create_bull_case.
        bear_case: Bear case dict from create_bear_case.

    Returns:
        Dict with verdict ('buy'|'sell'|'hold'), confidence, reasoning.
    """
    bull_conf = bull_case.get("confidence", 0.0)
    bear_conf = bear_case.get("confidence", 0.0)
    bull_evidence = len(bull_case.get("evidence", []))
    bear_evidence = len(bear_case.get("evidence", []))

    # Weighted score: 70% confidence, 30% evidence count
    max_evidence = max(bull_evidence + bear_evidence, 1)
    bull_score = 0.7 * bull_conf + 0.3 * (bull_evidence / max_evidence)
    bear_score = 0.7 * bear_conf + 0.3 * (bear_evidence / max_evidence)

    differential = bull_score - bear_score

    if differential > 0.6:
        verdict = "buy"
        reasoning = f"Strong bull case ({bull_evidence} factors, {bull_conf:.0%} confidence) outweighs bear case."
    elif differential < -0.6:
        verdict = "sell"
        reasoning = f"Strong bear case ({bear_evidence} factors, {bear_conf:.0%} confidence) outweighs bull case."
    else:
        verdict = "hold"
        reasoning = (
            f"Insufficient differential ({differential:+.2f}). "
            f"Bull: {bull_conf:.0%} ({bull_evidence} factors) vs "
            f"Bear: {bear_conf:.0%} ({bear_evidence} factors)."
        )

    return {
        "verdict": verdict,
        "confidence": round(abs(differential), 4),
        "reasoning": reasoning,
    }
