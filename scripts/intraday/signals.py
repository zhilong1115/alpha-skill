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


def compute_opening_range(df: pd.DataFrame, bars: int = 1) -> dict:
    """Calculate Opening Range Breakout (ORB) on first N bars.

    V2.1: Changed to 1 bar (5-min opening range) for tighter breakout detection.
    Only signals breakout when price closes above range high (not just touches).

    Returns:
        Dict with or_high, or_low, or_mid, is_breakout_long, is_breakout_short.
    """
    if len(df) < bars + 1:
        return {"or_high": None, "or_low": None, "or_complete": False}

    opening_bars = df.iloc[:bars]
    or_high = float(opening_bars["High"].max())
    or_low = float(opening_bars["Low"].min())
    or_mid = (or_high + or_low) / 2
    current = float(df["Close"].iloc[-1])

    return {
        "or_high": or_high,
        "or_low": or_low,
        "or_mid": or_mid,
        "or_complete": len(df) > bars,  # V2.1: opening range must be complete before trading
        "is_breakout_long": current > or_high and len(df) > bars,
        "is_breakout_short": current < or_low and len(df) > bars,
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


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average True Range for position sizing and stops.

    Args:
        df: OHLCV DataFrame.
        period: ATR lookback period (default 14).

    Returns:
        ATR value in dollars. Returns 0.0 if insufficient data.
    """
    if len(df) < period + 1:
        return 0.0

    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0


def compute_relative_volume(df: pd.DataFrame) -> float:
    """Current bar volume vs session average."""
    if len(df) < 2:
        return 1.0
    avg_vol = df["Volume"].iloc[:-1].mean()
    current_vol = float(df["Volume"].iloc[-1])
    return round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0


def compute_volume_confirmation(df: pd.DataFrame, lookback: int = 5, threshold: float = 1.5) -> bool:
    """V2.1: Check if entry bar volume > threshold × average of last N bars.

    Args:
        df: OHLCV DataFrame.
        lookback: Number of bars to average.
        threshold: Multiplier (1.5x = confirmation).

    Returns:
        True if current bar volume confirms the move.
    """
    if len(df) < lookback + 1:
        return False
    avg_vol = df["Volume"].iloc[-(lookback + 1):-1].mean()
    current_vol = float(df["Volume"].iloc[-1])
    return current_vol > avg_vol * threshold if avg_vol > 0 else False


def is_chasing_vwap(current: float, vwap_val: float, max_distance_pct: float = 2.0) -> bool:
    """V2.1: Check if price is too far above VWAP (chasing).

    Don't buy if price already >2% above VWAP.
    """
    if vwap_val <= 0:
        return False
    distance_pct = (current - vwap_val) / vwap_val * 100
    return distance_pct > max_distance_pct


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

    # 6. ATR (V2.2: for dynamic stop-loss/take-profit)
    atr = compute_atr(df, period=14)

    # 7. V2.1: Volume confirmation (entry bar > 1.5x 5-bar avg)
    vol_confirmed = compute_volume_confirmation(df, lookback=5, threshold=1.5)

    # 8. V2.1: Chasing check (don't buy if >2% above VWAP)
    chasing = is_chasing_vwap(current, vwap_val, max_distance_pct=2.0)

    # --- Combined Score (V2.1: stricter entry requirements) ---
    score = 0.0
    signals_detail = []
    entry_blocked = False
    block_reasons = []

    # V2.1 HARD REQUIREMENTS for long entry:
    # 1. Price must be above VWAP
    # 2. ORB must be complete and broken out
    # 3. Volume must confirm
    # 4. Must not be chasing

    # VWAP signal: above VWAP = bullish (REQUIRED for longs)
    if vwap_distance_pct > 0.3:
        score += 0.2
        signals_detail.append("above VWAP (+)")
    elif vwap_distance_pct < -0.3:
        score -= 0.2
        signals_detail.append("below VWAP (-)")
        block_reasons.append("below VWAP")

    # ORB breakout (V2.1: must wait for opening range to complete)
    if orb.get("or_complete"):
        if orb.get("is_breakout_long"):
            score += 0.3
            signals_detail.append("ORB breakout long (++)")
        elif orb.get("is_breakout_short"):
            score -= 0.3
            signals_detail.append("ORB breakdown (--)")
            block_reasons.append("ORB breakdown")
        else:
            signals_detail.append("within opening range (neutral)")
            block_reasons.append("no ORB breakout")
    else:
        block_reasons.append("opening range not complete")

    # RSI
    if rsi > 70:
        score -= 0.1
        signals_detail.append(f"RSI overbought {rsi:.0f}")
    elif rsi < 30:
        score += 0.1
        signals_detail.append(f"RSI oversold {rsi:.0f}")

    # Momentum
    if momentum > 0.05:
        score += 0.2
        signals_detail.append(f"momentum up {momentum:+.3f}")
    elif momentum < -0.05:
        score -= 0.2
        signals_detail.append(f"momentum down {momentum:+.3f}")

    # V2.1: Volume confirmation (REQUIRED)
    if vol_confirmed:
        score += 0.15 * (1 if score > 0 else -1)
        signals_detail.append(f"volume confirmed {rel_vol:.1f}x ✓")
    else:
        block_reasons.append("low volume (no confirmation)")
        signals_detail.append(f"volume weak {rel_vol:.1f}x ✗")

    # V2.1: No chasing check
    if chasing:
        entry_blocked = True
        block_reasons.append(f"chasing: {vwap_distance_pct:+.1f}% above VWAP")
        signals_detail.append(f"CHASING blocked ({vwap_distance_pct:+.1f}% > VWAP)")
        score = min(score, 0)  # Cap score at 0 if chasing

    # V2.1: For long signals, require VWAP + ORB + volume
    direction = "long" if score > 0.1 else "short" if score < -0.1 else "neutral"
    if direction == "long" and (vwap_distance_pct < 0 or not vol_confirmed or not orb.get("is_breakout_long")):
        # Demote to neutral if missing confirmations
        if not orb.get("is_breakout_long") or vwap_distance_pct < 0:
            direction = "neutral"
            score = score * 0.3  # Heavily penalize unconfirmed signals

    return {
        "ticker": ticker,
        "current": round(current, 2),
        "vwap": round(vwap_val, 2),
        "vwap_distance_pct": round(vwap_distance_pct, 2),
        "orb": orb,
        "rsi": round(rsi, 1),
        "momentum": momentum,
        "relative_volume": rel_vol,
        "volume_confirmed": vol_confirmed,
        "chasing": chasing,
        "atr": round(atr, 4),
        "entry_blocked": entry_blocked,
        "block_reasons": block_reasons,
        "score": round(score, 3),
        "direction": direction,
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
