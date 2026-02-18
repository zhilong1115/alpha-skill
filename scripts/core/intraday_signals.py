"""Intraday signal engine: 5min/15min candle-based signals for active trading.

Supplements the daily signal engine with faster-moving indicators that
change throughout the trading day, enabling 30-min trading cycles.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta
import yfinance as yf

logger = logging.getLogger(__name__)


# ── VWAP ──────────────────────────────────────────────────────────


def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP from intraday OHLCV data."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    cum_tp_vol = (typical_price * df["Volume"]).cumsum()
    vwap = cum_tp_vol / cum_vol
    return vwap


def _vwap_score(close: float, vwap: float) -> float:
    """Score based on price vs VWAP. Above VWAP = bullish momentum."""
    if vwap <= 0:
        return 0.0
    deviation_pct = (close - vwap) / vwap * 100
    # +1% above VWAP = strong buy, -1% below = strong sell
    return float(np.clip(deviation_pct / 1.5, -1, 1))


# ── Opening Range Breakout ────────────────────────────────────────


def _orb_score(close: float, open_high: float, open_low: float) -> float:
    """Score based on Opening Range Breakout (first 30 min high/low).

    Breakout above range = bullish, below = bearish.
    """
    range_size = open_high - open_low
    if range_size <= 0:
        return 0.0
    if close > open_high:
        return float(np.clip((close - open_high) / range_size, 0, 1))
    elif close < open_low:
        return float(np.clip((open_low - close) / range_size, -1, 0) * -1)
    # Inside range
    mid = (open_high + open_low) / 2
    return float(np.clip((close - mid) / (range_size / 2) * 0.3, -0.3, 0.3))


# ── Intraday Momentum ────────────────────────────────────────────


def _intraday_momentum_score(df_5m: pd.DataFrame) -> float:
    """Score based on intraday price trajectory.

    Uses the slope of 5-min closes over the last hour.
    """
    if len(df_5m) < 12:  # Need ~1 hour of 5-min data
        return 0.0
    recent = df_5m["Close"].iloc[-12:]  # Last hour
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    avg_price = recent.mean()
    if avg_price <= 0:
        return 0.0
    # Normalize slope as % per 5-min bar
    norm_slope = (slope / avg_price) * 100
    return float(np.clip(norm_slope * 5, -1, 1))


# ── RSI on 5-min ─────────────────────────────────────────────────


def _intraday_rsi_score(df_5m: pd.DataFrame) -> tuple[float, float]:
    """Compute RSI on 5-min data. Returns (rsi_value, score)."""
    if len(df_5m) < 20:
        return 50.0, 0.0
    rsi = ta.rsi(df_5m["Close"], length=14)
    if rsi is None or rsi.empty:
        return 50.0, 0.0
    val = float(rsi.iloc[-1])
    # More aggressive thresholds for intraday
    if val <= 25:
        score = 0.8
    elif val <= 35:
        score = 0.4
    elif val >= 75:
        score = -0.8
    elif val >= 65:
        score = -0.4
    else:
        score = 0.0
    return val, score


# ── Volume Profile ────────────────────────────────────────────────


def _volume_profile_score(df_5m: pd.DataFrame) -> float:
    """Score based on current volume vs historical same-time-of-day volume.

    High relative volume during a move = confirmation.
    """
    if len(df_5m) < 6:
        return 0.0
    recent_vol = df_5m["Volume"].iloc[-6:].mean()  # Last 30 min avg
    overall_vol = df_5m["Volume"].mean()
    if overall_vol <= 0:
        return 0.0
    ratio = recent_vol / overall_vol
    # Recent vol > 1.5x day avg = something happening
    if ratio > 2.0:
        # Direction matters: check if price is moving with volume
        price_change = (float(df_5m["Close"].iloc[-1]) / float(df_5m["Close"].iloc[-6]) - 1) * 100
        if price_change > 0:
            return min(ratio / 3, 1.0)  # Bullish volume
        else:
            return max(-ratio / 3, -1.0)  # Bearish volume
    return 0.0


# ── Main intraday signal computation ─────────────────────────────


def compute_intraday_signals(
    ticker: str,
    df_5m: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Compute intraday signals for a ticker.

    Args:
        ticker: Stock symbol.
        df_5m: Pre-fetched 5-min data. If None, downloads from yfinance.

    Returns:
        DataFrame with columns: ticker, signal_name, value, score.
    """
    if df_5m is None:
        try:
            t = yf.Ticker(ticker)
            df_5m = t.history(period="1d", interval="5m")
            if df_5m is None or df_5m.empty:
                return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
            # Flatten MultiIndex if present
            if isinstance(df_5m.columns, pd.MultiIndex):
                df_5m.columns = df_5m.columns.get_level_values(0)
        except Exception as e:
            logger.warning("Failed to get intraday data for %s: %s", ticker, e)
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

    if len(df_5m) < 6:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

    signals: list[dict] = []
    last_close = float(df_5m["Close"].iloc[-1])

    # 1. VWAP
    try:
        vwap = _compute_vwap(df_5m)
        vwap_val = float(vwap.iloc[-1])
        signals.append({
            "ticker": ticker,
            "signal_name": "INTRA_VWAP",
            "value": round((last_close - vwap_val) / vwap_val * 100, 3),
            "score": _vwap_score(last_close, vwap_val),
        })
    except Exception:
        pass

    # 2. Opening Range Breakout (first 30 min = 6 bars of 5-min)
    try:
        if len(df_5m) >= 6:
            open_range = df_5m.iloc[:6]
            orb_high = float(open_range["High"].max())
            orb_low = float(open_range["Low"].min())
            signals.append({
                "ticker": ticker,
                "signal_name": "INTRA_ORB",
                "value": round(last_close - (orb_high + orb_low) / 2, 2),
                "score": _orb_score(last_close, orb_high, orb_low),
            })
    except Exception:
        pass

    # 3. Intraday Momentum (1-hour slope)
    try:
        mom_score = _intraday_momentum_score(df_5m)
        signals.append({
            "ticker": ticker,
            "signal_name": "INTRA_MOMENTUM",
            "value": round(mom_score, 3),
            "score": mom_score,
        })
    except Exception:
        pass

    # 4. Intraday RSI(14) on 5-min
    try:
        rsi_val, rsi_score = _intraday_rsi_score(df_5m)
        signals.append({
            "ticker": ticker,
            "signal_name": "INTRA_RSI",
            "value": round(rsi_val, 1),
            "score": rsi_score,
        })
    except Exception:
        pass

    # 5. Volume Profile
    try:
        vp_score = _volume_profile_score(df_5m)
        signals.append({
            "ticker": ticker,
            "signal_name": "INTRA_VOLUME",
            "value": round(vp_score, 3),
            "score": vp_score,
        })
    except Exception:
        pass

    return pd.DataFrame(signals)


# ── Batch computation ─────────────────────────────────────────────


def compute_intraday_batch(
    tickers: list[str],
    top_n: int = 50,
) -> pd.DataFrame:
    """Compute intraday signals for multiple tickers.

    For efficiency, only processes top_n tickers (caller should pre-filter
    by daily conviction).

    Args:
        tickers: List of ticker symbols.
        top_n: Max tickers to process.

    Returns:
        Combined DataFrame of all intraday signals.
    """
    all_signals: list[pd.DataFrame] = []
    for ticker in tickers[:top_n]:
        try:
            sigs = compute_intraday_signals(ticker)
            if not sigs.empty:
                all_signals.append(sigs)
        except Exception as e:
            logger.warning("Intraday signals failed for %s: %s", ticker, e)

    if not all_signals:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
    return pd.concat(all_signals, ignore_index=True)


def compute_combined_conviction(
    daily_conviction: float,
    intraday_signals: pd.DataFrame,
    ticker: str,
) -> tuple[float, str]:
    """Combine daily conviction with intraday signals.

    Daily conviction provides direction (buy/sell/hold).
    Intraday signals provide timing (enter now / wait / exit now).

    Args:
        daily_conviction: Score from daily signal engine [-1, 1].
        intraday_signals: DataFrame of intraday signals for this ticker.
        ticker: Ticker symbol.

    Returns:
        Tuple of (combined_score, timing_action).
        timing_action: "enter_now", "wait", "exit_now", "hold"
    """
    ticker_sigs = intraday_signals[intraday_signals["ticker"] == ticker]
    if ticker_sigs.empty:
        return daily_conviction, "hold"

    # Compute intraday average
    intra_weights = {
        "INTRA_VWAP": 0.30,
        "INTRA_ORB": 0.20,
        "INTRA_MOMENTUM": 0.25,
        "INTRA_RSI": 0.15,
        "INTRA_VOLUME": 0.10,
    }

    weighted_sum = 0.0
    total_weight = 0.0
    for _, row in ticker_sigs.iterrows():
        w = intra_weights.get(row["signal_name"], 0.1)
        weighted_sum += row["score"] * w
        total_weight += w

    intra_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Combine: 60% daily (direction) + 40% intraday (timing)
    combined = daily_conviction * 0.6 + intra_score * 0.4

    # Determine timing action
    if daily_conviction > 0.25 and intra_score > 0.2:
        timing = "enter_now"  # Daily says buy + intraday confirms
    elif daily_conviction > 0.25 and intra_score < -0.2:
        timing = "wait"  # Daily says buy but intraday says wait
    elif daily_conviction < -0.1 and intra_score < -0.3:
        timing = "exit_now"  # Daily bearish + intraday confirms
    elif daily_conviction > 0 and intra_score > 0:
        timing = "hold"  # Both mildly positive
    else:
        timing = "hold"

    return round(combined, 3), timing
