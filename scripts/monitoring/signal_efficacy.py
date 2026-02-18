"""Signal efficacy tracking: log signals and evaluate their accuracy."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

SIGNAL_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "signals" / "signal_log.csv"
COLUMNS = ["ticker", "signal_name", "score", "price_at_signal", "timestamp"]


def _ensure_log_exists() -> None:
    """Create the signal log directory and file if they don't exist."""
    SIGNAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SIGNAL_LOG_PATH.exists():
        pd.DataFrame(columns=COLUMNS).to_csv(SIGNAL_LOG_PATH, index=False)


def log_signal(
    ticker: str,
    signal_name: str,
    score: float,
    price_at_signal: float,
    timestamp: Optional[str] = None,
) -> None:
    """Append a signal to the signal log CSV.

    Args:
        ticker: Stock ticker symbol.
        signal_name: Name of the signal.
        score: Signal score between -1 and 1.
        price_at_signal: Price when signal was generated.
        timestamp: ISO timestamp (defaults to now).
    """
    _ensure_log_exists()
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()

    row = pd.DataFrame([{
        "ticker": ticker,
        "signal_name": signal_name,
        "score": score,
        "price_at_signal": price_at_signal,
        "timestamp": timestamp,
    }])
    row.to_csv(SIGNAL_LOG_PATH, mode="a", header=False, index=False)


def evaluate_efficacy(days: int = 30) -> pd.DataFrame:
    """Evaluate signal accuracy over a time period.

    A signal is 'correct' if its direction (positive/negative score) matches
    the 5-day forward return direction.

    Args:
        days: Lookback period in days.

    Returns:
        DataFrame: signal_name, total_signals, correct, accuracy, avg_return.
    """
    _ensure_log_exists()
    try:
        df = pd.read_csv(SIGNAL_LOG_PATH)
    except Exception:
        return pd.DataFrame(columns=["signal_name", "total_signals", "correct", "accuracy", "avg_return"])

    if df.empty:
        return pd.DataFrame(columns=["signal_name", "total_signals", "correct", "accuracy", "avg_return"])

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    cutoff = datetime.utcnow() - timedelta(days=days)
    df = df[df["timestamp"] >= cutoff]

    # Only evaluate signals old enough (>=5 days) to have forward returns
    eval_cutoff = datetime.utcnow() - timedelta(days=5)
    evaluable = df[df["timestamp"] <= eval_cutoff].copy()

    if evaluable.empty:
        return pd.DataFrame(columns=["signal_name", "total_signals", "correct", "accuracy", "avg_return"])

    # Fetch current prices for forward return calculation
    import yfinance as yf

    results = []
    for signal_name, group in evaluable.groupby("signal_name"):
        correct = 0
        returns = []

        for _, row in group.iterrows():
            try:
                ticker_data = yf.Ticker(row["ticker"])
                hist = ticker_data.history(period="1mo")
                if hist.empty:
                    continue
                current_price = float(hist["Close"].iloc[-1])
                fwd_return = (current_price / row["price_at_signal"]) - 1.0
                returns.append(fwd_return)

                signal_dir = 1 if row["score"] > 0 else -1
                return_dir = 1 if fwd_return > 0 else -1
                if signal_dir == return_dir:
                    correct += 1
            except Exception:
                continue

        total = len(group)
        results.append({
            "signal_name": signal_name,
            "total_signals": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total > 0 else 0.0,
            "avg_return": round(sum(returns) / len(returns), 4) if returns else 0.0,
        })

    return pd.DataFrame(results)


def get_best_signals(days: int = 30) -> list[str]:
    """Get the top performing signal names by accuracy.

    Args:
        days: Lookback period.

    Returns:
        List of signal names sorted by accuracy (best first).
    """
    eff = evaluate_efficacy(days)
    if eff.empty:
        return []
    eff = eff.sort_values("accuracy", ascending=False)
    return eff["signal_name"].tolist()


def get_worst_signals(days: int = 30) -> list[str]:
    """Get the worst performing signal names by accuracy.

    Args:
        days: Lookback period.

    Returns:
        List of signal names sorted by accuracy (worst first).
    """
    eff = evaluate_efficacy(days)
    if eff.empty:
        return []
    eff = eff.sort_values("accuracy", ascending=True)
    return eff["signal_name"].tolist()
