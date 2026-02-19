"""Intraday signal engine: 5-minute candle technical signals for day trading."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


def get_intraday_data(ticker: str, period: str = "1d", interval: str = "5m") -> pd.DataFrame:
    """Fetch intraday candle data.

    Args:
        ticker: Stock symbol.
        period: Data period (1d, 5d).
        interval: Candle interval (1m, 5m, 15m).

    Returns:
        DataFrame with OHLCV data.
    """
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False)
        if data.empty:
            return pd.DataFrame()
        # Flatten multi-level columns if needed
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except Exception as e:
        logger.error("Failed to fetch intraday data for %s: %s", ticker, e)
        return pd.DataFrame()


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Calculate Volume Weighted Average Price."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    cum_tp_vol = (typical_price * df["Volume"]).cumsum()
    vwap = cum_tp_vol / cum_vol
    return vwap


def compute_opening_range(df: pd.DataFrame, bars: int = 3) -> dict:
    """Calculate Opening Range (first N bars = first 15min for 5min candles).

    Returns:
        Dict with or_high, or_low, or_mid, is_breakout_long, is_breakout_short.
    """
    if len(df) < bars + 1:
        return {"or_high": None, "or_low": None}

    opening_bars = df.iloc[:bars]
    or_high = float(opening_bars["High"].max())
    or_low = float(opening_bars["Low"].min())
    or_mid = (or_high + or_low) / 2
    current = float(df["Close"].iloc[-1])

    return {
        "or_high": or_high,
        "or_low": or_low,
        "or_mid": or_mid,
        "is_breakout_long": current > or_high,
        "is_breakout_short": current < or_low,
        "distance_from_or_high_pct": round((current - or_high) / or_high * 100, 2) if or_high else 0,
    }


def compute_intraday_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI on intraday candles."""
    if len(series) < period + 1:
        return 50.0

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def compute_momentum_slope(series: pd.Series, bars: int = 5) -> float:
    """Linear regression slope of last N bars (normalized)."""
    if len(series) < bars:
        return 0.0

    y = series.iloc[-bars:].values
    x = np.arange(bars)
    if np.std(y) == 0:
        return 0.0

    slope = np.polyfit(x, y, 1)[0]
    # Normalize by price level
    avg_price = np.mean(y)
    return round(slope / avg_price * 100, 4) if avg_price > 0 else 0.0


def compute_relative_volume(df: pd.DataFrame) -> float:
    """Current bar volume vs session average."""
    if len(df) < 2:
        return 1.0
    avg_vol = df["Volume"].iloc[:-1].mean()
    current_vol = float(df["Volume"].iloc[-1])
    return round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0


def compute_intraday_signals(ticker: str, df: pd.DataFrame | None = None) -> dict:
    """Compute all intraday signals for a ticker.

    Args:
        ticker: Stock symbol.
        df: Pre-fetched intraday data. If None, fetches automatically.

    Returns:
        Dict with all signal values and a combined score.
    """
    if df is None:
        df = get_intraday_data(ticker)

    if df.empty or len(df) < 5:
        return {"ticker": ticker, "error": "insufficient data", "score": 0}

    current = float(df["Close"].iloc[-1])

    # 1. VWAP
    vwap = compute_vwap(df)
    vwap_val = float(vwap.iloc[-1])
    vwap_distance_pct = (current - vwap_val) / vwap_val * 100 if vwap_val > 0 else 0

    # 2. Opening Range
    orb = compute_opening_range(df)

    # 3. RSI (14-period on 5min)
    rsi = compute_intraday_rsi(df["Close"])

    # 4. Momentum slope (last 5 bars)
    momentum = compute_momentum_slope(df["Close"])

    # 5. Relative volume
    rel_vol = compute_relative_volume(df)

    # --- Combined Score ---
    score = 0.0
    signals_detail = []

    # VWAP signal: above VWAP = bullish, below = bearish
    if vwap_distance_pct > 0.5:
        score += 0.15
        signals_detail.append("above VWAP (+)")
    elif vwap_distance_pct < -0.5:
        score -= 0.15
        signals_detail.append("below VWAP (-)")

    # ORB breakout
    if orb.get("is_breakout_long"):
        score += 0.25
        signals_detail.append("ORB breakout long (++)")
    elif orb.get("is_breakout_short"):
        score -= 0.25
        signals_detail.append("ORB breakdown (--)")

    # RSI
    if rsi > 70:
        score -= 0.1  # Overbought, caution for longs
        signals_detail.append(f"RSI overbought {rsi:.0f}")
    elif rsi < 30:
        score += 0.1  # Oversold, potential bounce
        signals_detail.append(f"RSI oversold {rsi:.0f}")

    # Momentum
    if momentum > 0.05:
        score += 0.2
        signals_detail.append(f"momentum up {momentum:+.3f}")
    elif momentum < -0.05:
        score -= 0.2
        signals_detail.append(f"momentum down {momentum:+.3f}")

    # Volume confirmation
    if rel_vol > 2.0:
        score += 0.1 * (1 if score > 0 else -1)  # Confirms direction
        signals_detail.append(f"high volume {rel_vol:.1f}x")

    return {
        "ticker": ticker,
        "current": round(current, 2),
        "vwap": round(vwap_val, 2),
        "vwap_distance_pct": round(vwap_distance_pct, 2),
        "orb": orb,
        "rsi": round(rsi, 1),
        "momentum": momentum,
        "relative_volume": rel_vol,
        "score": round(score, 3),
        "direction": "long" if score > 0.1 else "short" if score < -0.1 else "neutral",
        "signals": signals_detail,
    }


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Compute intraday signals for candidates and rank by combined score.

    Args:
        candidates: List from scanner (must have 'ticker' key).

    Returns:
        Candidates enriched with intraday signals, sorted by |score|.
    """
    enriched = []
    tickers = [c["ticker"] for c in candidates if c.get("ticker")]

    for candidate in candidates:
        ticker = candidate.get("ticker")
        if not ticker:
            continue

        signals = compute_intraday_signals(ticker)
        if signals.get("error"):
            continue

        # Combine scanner score with signal score
        scanner_score = candidate.get("intraday_score", 0)
        signal_score = signals.get("score", 0)
        combined = scanner_score * 0.5 + abs(signal_score) * 0.5

        enriched.append({
            **candidate,
            "intraday_signals": signals,
            "signal_score": signal_score,
            "combined_score": round(combined, 3),
            "trade_direction": signals["direction"],
        })

    enriched.sort(key=lambda x: x["combined_score"], reverse=True)
    return enriched
