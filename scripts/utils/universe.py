"""Stock universe management."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


def _load_config() -> dict:
    """Load config.yaml from project root."""
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def get_sp500_tickers() -> list[str]:
    """Fetch S&P 500 ticker list from Wikipedia.

    Returns:
        List of ticker symbols.
    """
    try:
        import io
        import requests as _req
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # Clean up tickers (e.g., BRK.B -> BRK-B for yfinance)
        tickers = [t.replace(".", "-") for t in tickers]
        return sorted(tickers)
    except Exception as e:
        print(f"[universe] Error fetching S&P 500 list: {e}")
        return []


def get_custom_universe() -> list[str]:
    """Read custom ticker universe from config.yaml.

    Returns:
        List of custom ticker symbols.
    """
    cfg = _load_config()
    tickers = cfg.get("universe", {}).get("custom_tickers", [])
    return [str(t).upper() for t in tickers] if tickers else []


def get_universe(universe_type: Optional[str] = None) -> list[str]:
    """Return configured stock universe.

    Args:
        universe_type: Override universe type ("sp500" or "custom").
            If None, reads from config.yaml.

    Returns:
        List of ticker symbols.
    """
    if universe_type is None:
        cfg = _load_config()
        universe_type = cfg.get("universe", {}).get("type", "sp500")

    if universe_type == "custom":
        tickers = get_custom_universe()
        if not tickers:
            print("[universe] Custom universe empty, falling back to S&P 500")
            tickers = get_sp500_tickers()
    else:
        tickers = get_sp500_tickers()

    # Apply exclusions
    cfg = _load_config()
    exclude = cfg.get("universe", {}).get("exclude", [])
    if exclude:
        exclude_set = {str(t).upper() for t in exclude}
        tickers = [t for t in tickers if t.upper() not in exclude_set]

    return tickers
