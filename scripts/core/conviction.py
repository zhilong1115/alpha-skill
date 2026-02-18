"""Conviction score engine: weighted synthesis of signal matrix."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# Default signal weights
DEFAULT_WEIGHTS: dict[str, float] = {
    "RSI_14": 0.20,
    "MACD_12_26_9": 0.25,
    "BBANDS_20_2": 0.15,
    "SMA_50_200": 0.25,
    "VOLUME_ANOMALY": 0.15,
}


def compute_conviction(
    signals_df: pd.DataFrame,
    weights: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    """Compute weighted conviction score per ticker from signal matrix.

    Args:
        signals_df: DataFrame with columns: ticker, signal_name, value, score.
        weights: Optional dict mapping signal_name -> weight. Defaults to DEFAULT_WEIGHTS.

    Returns:
        DataFrame with columns: ticker, conviction_score (in [-1, 1]).
    """
    if signals_df.empty:
        return pd.DataFrame(columns=["ticker", "conviction_score"])

    w = weights or DEFAULT_WEIGHTS

    results: list[dict] = []
    for ticker, group in signals_df.groupby("ticker"):
        total_weight = 0.0
        weighted_score = 0.0
        for _, row in group.iterrows():
            signal = row["signal_name"]
            score = row["score"]
            wt = w.get(signal, 0.1)  # Default weight for unknown signals
            weighted_score += score * wt
            total_weight += wt

        conviction = weighted_score / total_weight if total_weight > 0 else 0.0
        conviction = float(np.clip(conviction, -1, 1))
        results.append({"ticker": ticker, "conviction_score": conviction})

    return pd.DataFrame(results).sort_values("conviction_score", ascending=False).reset_index(drop=True)
