"""Signal weight optimization via random search over backtest performance."""

from __future__ import annotations

import random
from typing import Optional

import numpy as np

from scripts.backtest.engine import BacktestEngine


# Signal names that can be weighted
SIGNAL_NAMES = [
    "RSI_14",
    "MACD_12_26_9",
    "BBANDS_20_2",
    "SMA_50_200",
    "VOLUME_ANOMALY",
]


def _random_weights() -> dict[str, float]:
    """Generate a random weight set that sums to 1.0.

    Returns:
        Dict mapping signal name to weight.
    """
    raw = [random.random() for _ in SIGNAL_NAMES]
    total = sum(raw)
    if total == 0:
        return {name: 1.0 / len(SIGNAL_NAMES) for name in SIGNAL_NAMES}
    return {name: w / total for name, w in zip(SIGNAL_NAMES, raw)}


def evaluate_weight_set(
    weights: dict[str, float],
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> float:
    """Run a backtest with given weights and return the Sharpe ratio.

    Args:
        weights: Signal weight dict.
        tickers: Tickers to backtest.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        Sharpe ratio (float). Returns -999.0 on failure.
    """
    try:
        engine = BacktestEngine(tickers, start_date, end_date)
        engine.set_weights(weights)
        result = engine.run()
        sharpe = result.sharpe_ratio
        if not np.isfinite(sharpe):
            return -999.0
        return sharpe
    except Exception:
        return -999.0


def optimize_weights(
    tickers: list[str],
    start_date: str,
    end_date: str,
    iterations: int = 50,
) -> dict[str, float]:
    """Find optimal signal weights via random search.

    Generates random weight sets, evaluates each via backtest Sharpe ratio,
    and returns the best-performing weights.

    Args:
        tickers: Tickers to backtest.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        iterations: Number of random weight sets to try.

    Returns:
        Dict of optimal weights mapping signal_name to weight.
    """
    best_sharpe = -999.0
    best_weights = {name: 1.0 / len(SIGNAL_NAMES) for name in SIGNAL_NAMES}

    for i in range(iterations):
        weights = _random_weights()
        sharpe = evaluate_weight_set(weights, tickers, start_date, end_date)
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_weights = weights
            print(f"  [optimizer] Iteration {i+1}/{iterations}: new best Sharpe={sharpe:.3f}")

    # Round weights for readability
    best_weights = {k: round(v, 4) for k, v in best_weights.items()}
    print(f"  [optimizer] Best Sharpe: {best_sharpe:.3f}")
    print(f"  [optimizer] Best weights: {best_weights}")
    return best_weights
