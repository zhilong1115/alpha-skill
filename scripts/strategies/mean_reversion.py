"""Bollinger Band + RSI mean reversion strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.core.data_pipeline import get_price_data


def _compute_rsi(close: np.ndarray, period: int = 14) -> float:
    """Compute RSI for the most recent bar.

    Args:
        close: Array of closing prices.
        period: RSI period.

    Returns:
        RSI value (0-100).
    """
    if len(close) < period + 1:
        return 50.0
    deltas = np.diff(close[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _compute_bollinger(close: np.ndarray, period: int = 20, num_std: float = 2.0) -> dict:
    """Compute Bollinger Bands for the most recent bar.

    Args:
        close: Array of closing prices.
        period: Moving average period.
        num_std: Number of standard deviations.

    Returns:
        Dict with upper, middle, lower band values and z_score.
    """
    if len(close) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "z_score": 0}
    window = close[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window))
    if std == 0:
        return {"upper": middle, "middle": middle, "lower": middle, "z_score": 0}
    return {
        "upper": middle + num_std * std,
        "middle": middle,
        "lower": middle - num_std * std,
        "z_score": (close[-1] - middle) / std,
    }


def find_reversion_candidates(tickers: list[str]) -> pd.DataFrame:
    """Screen for mean reversion candidates: >2σ below 20MA and RSI < 30.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns [ticker, rsi, z_score, bb_lower, price, score].
    """
    results = []
    for ticker in tickers:
        try:
            df = get_price_data(ticker, period="3mo")
            if df is None or df.empty or len(df) < 20:
                continue

            close = df["Close"].values if "Close" in df.columns else df["close"].values
            rsi = _compute_rsi(close)
            bb = _compute_bollinger(close)

            # Check criteria: below lower band (z_score < -2) and RSI < 30
            if bb["z_score"] < -2.0 and rsi < 30:
                # Score based on how extreme the setup is
                z = bb["z_score"]
                rsi_score = (30 - rsi) / 30  # 0-1, higher = more oversold
                z_score_component = min(abs(z) - 2.0, 2.0) / 2.0  # 0-1
                score = round(min((rsi_score * 0.5 + z_score_component * 0.5), 1.0), 2)

                results.append({
                    "ticker": ticker,
                    "rsi": round(rsi, 1),
                    "z_score": round(bb["z_score"], 2),
                    "bb_lower": round(bb["lower"], 2),
                    "price": round(float(close[-1]), 2),
                    "score": score,
                })
        except Exception:
            continue

    if not results:
        return pd.DataFrame(columns=["ticker", "rsi", "z_score", "bb_lower", "price", "score"])
    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)


def generate_reversion_signals(tickers: list[str]) -> pd.DataFrame:
    """Generate mean reversion trading signals.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns [ticker, signal_name, value, score].
    """
    try:
        candidates = find_reversion_candidates(tickers)
        if candidates.empty:
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

        signals = []
        for _, row in candidates.iterrows():
            signals.append({
                "ticker": row["ticker"],
                "signal_name": "mean_reversion_bb_rsi",
                "value": row["z_score"],
                "score": row["score"],
            })
        return pd.DataFrame(signals)
    except Exception:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
