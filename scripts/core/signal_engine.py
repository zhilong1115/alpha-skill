"""Signal engine: compute technical signals using pandas-ta."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta


def _rsi_score(value: float) -> float:
    """Map RSI(14) to score in [-1, 1]. <30 bullish, >70 bearish."""
    if value <= 30:
        return 1.0 - (value / 30)  # 0->1, 30->0
    if value >= 70:
        return -(value - 70) / 30  # 70->0, 100->-1
    return 0.0


def _macd_score(macd_val: float, signal_val: float, hist: float) -> float:
    """Score MACD based on histogram direction and magnitude."""
    if hist == 0:
        return 0.0
    return float(np.clip(hist / abs(macd_val) if macd_val != 0 else np.sign(hist), -1, 1))


def _bollinger_score(close: float, upper: float, lower: float, mid: float) -> float:
    """Score based on position within Bollinger Bands."""
    band_width = upper - lower
    if band_width == 0:
        return 0.0
    position = (close - mid) / (band_width / 2)
    return float(np.clip(-position, -1, 1))  # Above mid = bearish, below = bullish


def _sma_crossover_score(sma50: float, sma200: float) -> float:
    """Score SMA 50/200 crossover. Golden cross = +1, death cross = -1."""
    if pd.isna(sma50) or pd.isna(sma200) or sma200 == 0:
        return 0.0
    ratio = (sma50 - sma200) / sma200
    return float(np.clip(ratio * 10, -1, 1))


def _volume_anomaly_score(current_vol: float, avg_vol: float) -> float:
    """Score volume anomaly. >2x average = strong signal."""
    if avg_vol == 0:
        return 0.0
    ratio = current_vol / avg_vol
    if ratio > 2.0:
        return min((ratio - 1) / 3, 1.0)
    return 0.0


def compute_signals(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    """Compute technical signals for a given ticker's OHLCV data.

    Args:
        ticker: Stock ticker symbol.
        df: DataFrame with Open, High, Low, Close, Volume columns.

    Returns:
        DataFrame with columns: ticker, signal_name, value, score.
        Score is in range [-1, 1] where +1 = strong buy, -1 = strong sell.
    """
    if df.empty or len(df) < 200:
        # Need at least 200 rows for SMA200
        min_rows = len(df)
        if min_rows < 14:
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    last = close.iloc[-1]

    signals: list[dict] = []

    # RSI(14)
    rsi = ta.rsi(close, length=14)
    if rsi is not None and not rsi.empty:
        rsi_val = float(rsi.iloc[-1])
        signals.append({"ticker": ticker, "signal_name": "RSI_14", "value": rsi_val, "score": _rsi_score(rsi_val)})

    # MACD(12, 26, 9)
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        macd_val = float(macd_df.iloc[-1, 0])
        signal_val = float(macd_df.iloc[-1, 1])
        hist_val = float(macd_df.iloc[-1, 2])
        signals.append({
            "ticker": ticker,
            "signal_name": "MACD_12_26_9",
            "value": hist_val,
            "score": _macd_score(macd_val, signal_val, hist_val),
        })

    # Bollinger Bands(20, 2)
    bbands = ta.bbands(close, length=20, std=2)
    if bbands is not None and not bbands.empty:
        upper = float(bbands.iloc[-1, 2])  # BBU
        mid = float(bbands.iloc[-1, 1])    # BBM
        lower = float(bbands.iloc[-1, 0])  # BBL
        signals.append({
            "ticker": ticker,
            "signal_name": "BBANDS_20_2",
            "value": last,
            "score": _bollinger_score(last, upper, lower, mid),
        })

    # SMA Crossover (50/200)
    sma50 = ta.sma(close, length=50)
    sma200 = ta.sma(close, length=200)
    if sma50 is not None and sma200 is not None and len(sma50) > 0 and len(sma200) > 0:
        s50 = float(sma50.iloc[-1])
        s200 = float(sma200.iloc[-1])
        signals.append({
            "ticker": ticker,
            "signal_name": "SMA_50_200",
            "value": s50 - s200,
            "score": _sma_crossover_score(s50, s200),
        })

    # Volume Anomaly
    avg_vol = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
    cur_vol = float(volume.iloc[-1])
    vol_score = _volume_anomaly_score(cur_vol, avg_vol)
    signals.append({
        "ticker": ticker,
        "signal_name": "VOLUME_ANOMALY",
        "value": cur_vol / avg_vol if avg_vol > 0 else 0.0,
        "score": vol_score,
    })

    return pd.DataFrame(signals)
