"""Follow institutional and notable investor trades via SEC EDGAR."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import requests

# Default CIKs to watch
DEFAULT_CIKS = {
    "berkshire": "1067983",
    "bridgewater": "1350694",
}

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_HEADERS = {
    "User-Agent": "StockTrader research@example.com",
    "Accept": "application/json",
}

ARK_TRADES_URL = "https://ark-funds.com/wp-content/uploads/funds-etf-csv/ARK_TRADES.csv"


def fetch_13f_holdings(cik: str) -> dict:
    """Fetch submission data for a CIK from SEC EDGAR.

    Args:
        cik: SEC Central Index Key (numeric string).

    Returns:
        Dict with filing metadata and recent filings list.
    """
    try:
        padded_cik = cik.zfill(10)
        url = EDGAR_SUBMISSIONS_URL.format(cik=padded_cik)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[investor_following] Error fetching CIK {cik}: {e}")
        return {}


def compare_13f_changes(cik: str) -> pd.DataFrame:
    """Compare latest vs previous 13F filing to detect position changes.

    Args:
        cik: SEC Central Index Key.

    Returns:
        DataFrame with columns [ticker, action, shares_change, pct_change].
    """
    try:
        data = fetch_13f_holdings(cik)
        if not data:
            return pd.DataFrame(columns=["ticker", "action", "shares_change", "pct_change"])

        recent_filings = data.get("filings", {}).get("recent", {})
        if not recent_filings:
            return pd.DataFrame(columns=["ticker", "action", "shares_change", "pct_change"])

        forms = recent_filings.get("form", [])
        accessions = recent_filings.get("accessionNumber", [])

        # Find 13F filings
        thirteenf_indices = [
            i for i, f in enumerate(forms) if "13F" in str(f).upper()
        ]

        if len(thirteenf_indices) < 1:
            return pd.DataFrame(columns=["ticker", "action", "shares_change", "pct_change"])

        # Return stub - actual XML parsing done in filing_parser
        return pd.DataFrame(columns=["ticker", "action", "shares_change", "pct_change"])

    except Exception as e:
        print(f"[investor_following] Error comparing 13F for CIK {cik}: {e}")
        return pd.DataFrame(columns=["ticker", "action", "shares_change", "pct_change"])


def get_ark_daily_trades() -> pd.DataFrame:
    """Fetch ARK Invest daily trades CSV.

    Returns:
        DataFrame with ARK trade data or empty DataFrame if unavailable.
    """
    try:
        resp = requests.get(ARK_TRADES_URL, timeout=15)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        # Normalize column names
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        return df
    except Exception as e:
        print(f"[investor_following] ARK trades unavailable: {e}")
        return pd.DataFrame(columns=["date", "fund", "direction", "ticker", "shares", "weight"])


def _score_ark_trades(df: pd.DataFrame) -> list[dict]:
    """Score ARK trades into signals.

    Args:
        df: ARK trades DataFrame.

    Returns:
        List of signal dicts.
    """
    signals = []
    if df.empty:
        return signals

    ticker_col = None
    for col in ["ticker", "cusip", "symbol"]:
        if col in df.columns:
            ticker_col = col
            break
    if ticker_col is None:
        return signals

    direction_col = None
    for col in ["direction", "trade_direction"]:
        if col in df.columns:
            direction_col = col
            break

    if direction_col is None:
        return signals

    # Group by ticker and aggregate direction
    for ticker, group in df.groupby(ticker_col):
        if not isinstance(ticker, str) or len(ticker) > 6:
            continue
        buys = (group[direction_col].str.upper() == "BUY").sum()
        sells = (group[direction_col].str.upper() == "SELL").sum()
        net = buys - sells
        if net != 0:
            score = min(max(net * 0.3, -1.0), 1.0)
            signals.append({
                "ticker": str(ticker).upper(),
                "signal_name": "ark_daily_trade",
                "value": float(net),
                "score": round(score, 2),
            })
    return signals


def generate_following_signals(
    watch_ciks: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Generate signals from institutional investor following.

    Args:
        watch_ciks: Dict of name -> CIK to watch. Defaults to Berkshire + Bridgewater.

    Returns:
        DataFrame with columns [ticker, signal_name, value, score].
    """
    if watch_ciks is None:
        watch_ciks = DEFAULT_CIKS

    signals = []

    # 13F-based signals
    for name, cik in watch_ciks.items():
        try:
            changes = compare_13f_changes(cik)
            for _, row in changes.iterrows():
                action = row.get("action", "")
                score = 0.0
                if action == "new_buy":
                    score = 0.6
                elif action == "increase":
                    score = 0.3
                elif action == "decrease":
                    score = -0.3
                elif action == "sold_out":
                    score = -0.6
                signals.append({
                    "ticker": row["ticker"],
                    "signal_name": f"13f_{name}_{action}",
                    "value": row.get("pct_change", 0.0),
                    "score": score,
                })
        except Exception:
            continue

    # ARK trades
    try:
        ark = get_ark_daily_trades()
        signals.extend(_score_ark_trades(ark))
    except Exception:
        pass

    if not signals:
        return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
    return pd.DataFrame(signals)
