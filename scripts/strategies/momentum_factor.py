"""Monthly momentum factor strategy (12-1 month momentum)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.core.data_pipeline import get_price_data


def compute_momentum_scores(
    tickers: list[str],
    lookback_months: int = 12,
    skip_months: int = 1,
) -> pd.DataFrame:
    """Compute 12-1 month momentum scores for a list of tickers.

    Uses total return over [lookback_months, skip_months] window,
    skipping the most recent month to avoid short-term reversal.

    Args:
        tickers: List of stock ticker symbols.
        lookback_months: Total lookback period in months.
        skip_months: Recent months to skip.

    Returns:
        DataFrame with columns [ticker, momentum, rank, score].
    """
    results = []
    period = f"{lookback_months + 1}mo"

    for ticker in tickers:
        try:
            df = get_price_data(ticker, period=period)
            if df is None or df.empty:
                continue

            close = df["Close"].values if "Close" in df.columns else df["close"].values
            if len(close) < 60:
                continue

            # Skip most recent skip_months (~21 trading days per month)
            skip_days = skip_months * 21
            if skip_days >= len(close):
                continue

            end_price = close[-(skip_days + 1)]
            start_price = close[0]

            if start_price <= 0:
                continue

            momentum = (end_price / start_price) - 1.0
            results.append({
                "ticker": ticker,
                "momentum": round(float(momentum), 4),
            })
        except Exception:
            continue

    if not results:
        return pd.DataFrame(columns=["ticker", "momentum", "rank", "score"])

    df_out = pd.DataFrame(results)
    df_out = df_out.sort_values("momentum", ascending=False).reset_index(drop=True)
    df_out["rank"] = range(1, len(df_out) + 1)

    # Normalize to [-1, 1] score
    n = len(df_out)
    if n > 1:
        df_out["score"] = df_out["momentum"].apply(
            lambda m: round(min(max(m * 2, -1.0), 1.0), 2)
        )
    else:
        df_out["score"] = 0.0

    return df_out


def get_momentum_portfolio(
    tickers: list[str], top_n: int = 20
) -> pd.DataFrame:
    """Select top N momentum stocks.

    Args:
        tickers: Universe of ticker symbols.
        top_n: Number of top momentum stocks to select.

    Returns:
        DataFrame of top momentum stocks with scores.
    """
    scores = compute_momentum_scores(tickers)
    if scores.empty:
        return scores
    return scores.head(top_n).reset_index(drop=True)


def generate_momentum_signals(tickers: list[str]) -> pd.DataFrame:
    """Generate momentum-based trading signals.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns [ticker, signal_name, value, score].
    """
    try:
        scores = compute_momentum_scores(tickers)
        if scores.empty:
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

        signals = []
        for _, row in scores.iterrows():
            signals.append({
                "ticker": row["ticker"],
                "signal_name": "momentum_12_1",
                "value": row["momentum"],
                "score": row["score"],
            })
        return pd.DataFrame(signals)
    except Exception:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
