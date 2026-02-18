"""Data pipeline for fetching and caching market data via yfinance."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
import yaml


def _load_config() -> dict:
    """Load config.yaml from project root."""
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def _cache_dir() -> Path:
    """Return the cache directory path, creating it if needed."""
    cfg = _load_config()
    cache = Path(cfg.get("data", {}).get("cache_dir", "./data/cache"))
    if not cache.is_absolute():
        cache = Path(__file__).resolve().parents[2] / cache
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _cache_path(ticker: str, period: str) -> Path:
    """Return the parquet cache file path for a ticker+period."""
    return _cache_dir() / f"{ticker.upper()}_{period}.parquet"


def _cache_is_fresh(path: Path, ttl_hours: int = 1) -> bool:
    """Check if a cached file is still within TTL."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - mtime < timedelta(hours=ttl_hours)


def get_price_data(ticker: str, period: str = "1y", force_refresh: bool = False) -> pd.DataFrame:
    """Fetch OHLCV data for a single ticker, with parquet caching.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").
        period: yfinance period string (e.g. "1y", "6mo", "5d").
        force_refresh: If True, bypass cache.

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume.
    """
    cfg = _load_config()
    ttl = cfg.get("data", {}).get("cache_ttl_hours", 1)
    cache = _cache_path(ticker, period)

    if not force_refresh and _cache_is_fresh(cache, ttl):
        return pd.read_parquet(cache)

    t = yf.Ticker(ticker)
    df: pd.DataFrame = t.history(period=period)

    if df.empty:
        raise ValueError(f"No data returned for {ticker} with period={period}")

    # Keep standard OHLCV columns
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[cols]
    df.to_parquet(cache)
    return df


def get_bulk_price_data(
    tickers: list[str],
    period: str = "1y",
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data for multiple tickers.

    Args:
        tickers: List of ticker symbols.
        period: yfinance period string.
        force_refresh: Bypass cache if True.

    Returns:
        Dict mapping ticker -> DataFrame.
    """
    results: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            results[ticker] = get_price_data(ticker, period, force_refresh)
        except Exception as e:
            print(f"[data_pipeline] Error fetching {ticker}: {e}")
    return results
