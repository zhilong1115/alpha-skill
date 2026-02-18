"""Earnings-driven trading strategy using yfinance data."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from scripts.core.data_pipeline import get_price_data


def get_upcoming_earnings(tickers: list[str], days_ahead: int = 14) -> pd.DataFrame:
    """Find upcoming earnings dates for a list of tickers.

    Args:
        tickers: List of stock ticker symbols.
        days_ahead: Number of days to look ahead.

    Returns:
        DataFrame with columns [ticker, earnings_date].
    """
    results = []
    now = datetime.now()
    cutoff = now + timedelta(days=days_ahead)

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None or cal.empty if isinstance(cal, pd.DataFrame) else not cal:
                continue
            # calendar can be dict or DataFrame depending on yfinance version
            if isinstance(cal, dict):
                earnings_date = cal.get("Earnings Date")
                if isinstance(earnings_date, list) and earnings_date:
                    earnings_date = earnings_date[0]
                if earnings_date is None:
                    continue
                if isinstance(earnings_date, str):
                    earnings_date = pd.Timestamp(earnings_date)
            elif isinstance(cal, pd.DataFrame):
                if "Earnings Date" in cal.index:
                    earnings_date = cal.loc["Earnings Date"].iloc[0]
                elif "Earnings Date" in cal.columns:
                    earnings_date = cal["Earnings Date"].iloc[0]
                else:
                    continue
            else:
                continue

            if isinstance(earnings_date, (pd.Timestamp, datetime)):
                if now <= earnings_date.replace(tzinfo=None) if hasattr(earnings_date, 'tzinfo') and earnings_date.tzinfo else earnings_date <= cutoff:
                    results.append({"ticker": ticker, "earnings_date": earnings_date})
        except Exception:
            continue

    if not results:
        return pd.DataFrame(columns=["ticker", "earnings_date"])
    return pd.DataFrame(results)


def analyze_pre_earnings(ticker: str) -> dict:
    """Analyze a stock's setup before earnings.

    Checks recent price action, volatility, and volume trends.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with keys: ticker, iv_percentile, price_trend, volume_trend, score.
    """
    result = {
        "ticker": ticker,
        "iv_percentile": None,
        "price_trend": 0.0,
        "volume_trend": 0.0,
        "score": 0.0,
    }
    try:
        df = get_price_data(ticker, period="3mo")
        if df is None or df.empty or len(df) < 20:
            return result

        close = df["Close"].values if "Close" in df.columns else df["close"].values
        volume = df["Volume"].values if "Volume" in df.columns else df["volume"].values

        # Price trend: % change over last 20 days
        price_trend = (close[-1] / close[-20] - 1.0) if close[-20] != 0 else 0.0
        result["price_trend"] = round(float(price_trend), 4)

        # Volume trend: recent 5-day avg vs 20-day avg
        vol_recent = np.mean(volume[-5:])
        vol_avg = np.mean(volume[-20:])
        vol_trend = (vol_recent / vol_avg - 1.0) if vol_avg > 0 else 0.0
        result["volume_trend"] = round(float(vol_trend), 4)

        # Realized vol as proxy for IV percentile
        returns = np.diff(np.log(close[-60:])) if len(close) >= 60 else np.diff(np.log(close))
        rv = float(np.std(returns) * np.sqrt(252))
        # Rough IV percentile proxy: compare current 20-day rv to 60-day rv
        if len(close) >= 60:
            rv_20 = float(np.std(np.diff(np.log(close[-20:]))) * np.sqrt(252))
            iv_pct = min(max(rv_20 / rv if rv > 0 else 0.5, 0.0), 1.0)
            result["iv_percentile"] = round(iv_pct, 2)

        # Score: higher volume trend + moderate price trend = good setup
        score = 0.0
        if vol_trend > 0.2:
            score += 0.3
        if -0.05 < price_trend < 0.10:
            score += 0.2
        if result["iv_percentile"] is not None and result["iv_percentile"] < 0.5:
            score += 0.2
        result["score"] = round(min(max(score, -1.0), 1.0), 2)

    except Exception:
        pass
    return result


def score_post_earnings(ticker: str) -> dict:
    """Score a stock after earnings announcement.

    Evaluates gap, volume surge, and price stability.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with keys: ticker, gap_pct, volume_surge, score.
    """
    result = {"ticker": ticker, "gap_pct": 0.0, "volume_surge": 0.0, "score": 0.0}
    try:
        df = get_price_data(ticker, period="1mo")
        if df is None or df.empty or len(df) < 5:
            return result

        close = df["Close"].values if "Close" in df.columns else df["close"].values
        volume = df["Volume"].values if "Volume" in df.columns else df["volume"].values
        op = df["Open"].values if "Open" in df.columns else df["open"].values

        # Most recent day gap
        gap_pct = (op[-1] / close[-2] - 1.0) if close[-2] != 0 else 0.0
        result["gap_pct"] = round(float(gap_pct), 4)

        # Volume surge
        vol_avg = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume[:-1])
        vol_surge = (volume[-1] / vol_avg - 1.0) if vol_avg > 0 else 0.0
        result["volume_surge"] = round(float(vol_surge), 4)

        # Score: strong gap + high volume = conviction
        score = 0.0
        abs_gap = abs(gap_pct)
        if abs_gap > 0.05:
            score += 0.4 * np.sign(gap_pct)
        elif abs_gap > 0.02:
            score += 0.2 * np.sign(gap_pct)
        if vol_surge > 1.0:
            score += 0.3 * np.sign(gap_pct) if gap_pct != 0 else 0.1
        result["score"] = round(float(min(max(score, -1.0), 1.0)), 2)

    except Exception:
        pass
    return result


def generate_earnings_signals(tickers: list[str]) -> pd.DataFrame:
    """Generate earnings-based trading signals for a list of tickers.

    Args:
        tickers: List of stock ticker symbols.

    Returns:
        DataFrame with columns [ticker, signal_name, value, score].
    """
    signals = []
    try:
        upcoming = get_upcoming_earnings(tickers)
        for _, row in upcoming.iterrows():
            t = row["ticker"]
            pre = analyze_pre_earnings(t)
            signals.append({
                "ticker": t,
                "signal_name": "pre_earnings_setup",
                "value": pre.get("iv_percentile", 0.0) or 0.0,
                "score": pre["score"],
            })

        # Check recent post-earnings for all tickers
        for t in tickers:
            post = score_post_earnings(t)
            if abs(post["gap_pct"]) > 0.02:
                signals.append({
                    "ticker": t,
                    "signal_name": "post_earnings_gap",
                    "value": post["gap_pct"],
                    "score": post["score"],
                })
    except Exception:
        pass

    if not signals:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
    return pd.DataFrame(signals)
