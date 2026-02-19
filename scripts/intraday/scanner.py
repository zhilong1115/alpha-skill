"""Pre-market scanner: identify day-trade candidates based on gaps, volume, catalysts.

Uses Alpaca Screener API for real-time market-wide scanning (most actives, gainers/losers)
instead of scanning a fixed ticker list.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf
import pandas as pd
import requests as _requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Minimum requirements for intraday candidates
MIN_AVG_VOLUME = 500_000   # shares/day
MIN_PRICE = 5.0
MIN_GAP_PCT = 2.0          # % gap from previous close
MIN_VOLUME_RATIO = 2.0     # vs 20-day average


def _alpaca_headers() -> dict:
    """Get Alpaca API headers."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return {
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", ""),
    }


def scan_market_movers(top_n: int = 20) -> dict:
    """Scan entire market for top gainers, losers, and most active using Alpaca Screener API.

    Returns:
        Dict with gainers, losers, most_active lists.
    """
    headers = _alpaca_headers()
    base = "https://data.alpaca.markets/v1beta1/screener/stocks"
    result = {"gainers": [], "losers": [], "most_active": []}

    # Most actives
    try:
        r = _requests.get(f"{base}/most-actives", params={"top": top_n}, headers=headers, timeout=10)
        if r.ok:
            for s in r.json().get("most_actives", []):
                sym = s.get("symbol", "")
                if sym and "." not in sym and "W" not in sym[-1:]:  # Skip warrants
                    result["most_active"].append({
                        "ticker": sym,
                        "trade_count": s.get("trade_count", 0),
                        "volume": s.get("volume", 0),
                    })
    except Exception as e:
        logger.warning("Most actives scan failed: %s", e)

    # Market movers (gainers/losers)
    try:
        r = _requests.get(f"{base}/movers", params={"top": top_n}, headers=headers, timeout=10)
        if r.ok:
            data = r.json()
            for s in data.get("gainers", []):
                sym = s.get("symbol", "")
                price = s.get("price", 0)
                pct = s.get("percent_change", 0)
                if sym and price >= MIN_PRICE and "." not in sym:
                    result["gainers"].append({
                        "ticker": sym,
                        "percent_change": pct,
                        "price": price,
                        "volume": s.get("volume", 0),
                    })
            for s in data.get("losers", []):
                sym = s.get("symbol", "")
                price = s.get("price", 0)
                pct = s.get("percent_change", 0)
                if sym and price >= MIN_PRICE and "." not in sym:
                    result["losers"].append({
                        "ticker": sym,
                        "percent_change": pct,
                        "price": price,
                        "volume": s.get("volume", 0),
                    })
    except Exception as e:
        logger.warning("Market movers scan failed: %s", e)

    logger.info("Alpaca screener: %d actives, %d gainers, %d losers",
                len(result["most_active"]), len(result["gainers"]), len(result["losers"]))
    return result


def scan_premarket_gaps(tickers: list[str] | None = None, top_n: int = 20) -> list[dict]:
    """Scan for stocks gapping up/down significantly from previous close.

    Args:
        tickers: Tickers to scan. If None, uses a broad liquid universe.
        top_n: Max candidates to return.

    Returns:
        List of dicts with ticker, gap_pct, prev_close, current, avg_volume, direction.
    """
    if tickers is None:
        tickers = _get_liquid_universe()

    results = []
    # Batch download for speed
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            data = yf.download(batch, period="5d", progress=False, threads=True)
            if data.empty:
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        close_series = data["Close"]
                        vol_series = data["Volume"]
                        open_series = data["Open"]
                    else:
                        if ticker not in data["Close"].columns:
                            continue
                        close_series = data["Close"][ticker]
                        vol_series = data["Volume"][ticker]
                        open_series = data["Open"][ticker]

                    close_series = close_series.dropna()
                    vol_series = vol_series.dropna()

                    if len(close_series) < 2:
                        continue

                    prev_close = float(close_series.iloc[-2])
                    current = float(close_series.iloc[-1])
                    today_open = float(open_series.dropna().iloc[-1])
                    avg_vol = float(vol_series.iloc[-21:-1].mean()) if len(vol_series) >= 21 else float(vol_series.mean())
                    latest_vol = float(vol_series.iloc[-1])

                    if prev_close <= 0 or current < MIN_PRICE:
                        continue
                    if avg_vol < MIN_AVG_VOLUME:
                        continue

                    gap_pct = (today_open - prev_close) / prev_close * 100
                    day_change_pct = (current - prev_close) / prev_close * 100
                    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 0

                    if abs(gap_pct) >= MIN_GAP_PCT or vol_ratio >= MIN_VOLUME_RATIO * 1.5:
                        results.append({
                            "ticker": ticker,
                            "gap_pct": round(gap_pct, 2),
                            "day_change_pct": round(day_change_pct, 2),
                            "prev_close": round(prev_close, 2),
                            "open": round(today_open, 2),
                            "current": round(current, 2),
                            "avg_volume": int(avg_vol),
                            "latest_volume": int(latest_vol),
                            "volume_ratio": round(vol_ratio, 2),
                            "direction": "long" if gap_pct > 0 else "short",
                        })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Batch download failed: %s", e)
            continue

    # Sort by absolute gap
    results.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
    return results[:top_n]


def scan_news_catalysts() -> list[dict]:
    """Check news daemon alerts for actionable intraday catalysts.

    Returns:
        List of dicts with ticker, headline, sentiment, action_type, urgency.
    """
    alerts_path = DATA_DIR / "alerts" / "pending.json"
    if not alerts_path.exists():
        return []

    try:
        alerts = json.loads(alerts_path.read_text())
    except Exception:
        return []

    # Only recent alerts (last 2 hours)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    catalysts = []

    for a in alerts:
        try:
            ts = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                continue

            if a.get("action_type") in ("buy", "sell"):
                symbols = a.get("symbols", [])
                ticker = a.get("ticker", "")
                if ticker and ticker != "MACRO":
                    symbols = list(set(symbols + [ticker]))

                for sym in symbols:
                    catalysts.append({
                        "ticker": sym,
                        "headline": a.get("headline", "")[:120],
                        "sentiment": a.get("sentiment", "neutral"),
                        "action_type": a.get("action_type", "monitor"),
                        "urgency": a.get("urgency", "normal"),
                        "source": a.get("source", "unknown"),
                        "timestamp": a["timestamp"],
                    })
        except Exception:
            continue

    return catalysts


def get_intraday_candidates(top_n: int = 10) -> list[dict]:
    """Combine Alpaca screener + gap scan + news catalysts into ranked candidate list.

    Uses 3 sources:
    1. Alpaca Screener API — real-time market-wide gainers/losers/most-active
    2. Gap scanner — from our liquid universe (backup if Alpaca fails)
    3. News daemon alerts — catalyst-driven opportunities

    Returns:
        Ranked list of intraday trade candidates with scores.
    """
    candidates = {}

    # 1. Alpaca Screener (real-time, market-wide — the best source)
    movers = scan_market_movers(top_n=20)

    for g in movers.get("gainers", []):
        t = g["ticker"]
        pct = abs(g.get("percent_change", 0))
        score = min(pct / 15.0, 1.0) * 0.5  # % change component (0-0.5)
        candidates[t] = {
            "ticker": t,
            "gap_pct": round(g.get("percent_change", 0), 2),
            "day_change_pct": round(g.get("percent_change", 0), 2),
            "current": g.get("price", 0),
            "volume": g.get("volume", 0),
            "volume_ratio": 0,
            "source": "alpaca_gainer",
            "direction": "long",
            "has_catalyst": False,
            "intraday_score": round(score, 3),
        }

    for g in movers.get("losers", []):
        t = g["ticker"]
        pct = abs(g.get("percent_change", 0))
        # Losers can be bounce plays
        score = min(pct / 20.0, 1.0) * 0.3
        if t not in candidates:
            candidates[t] = {
                "ticker": t,
                "gap_pct": round(g.get("percent_change", 0), 2),
                "day_change_pct": round(g.get("percent_change", 0), 2),
                "current": g.get("price", 0),
                "volume": g.get("volume", 0),
                "volume_ratio": 0,
                "source": "alpaca_loser",
                "direction": "short",
                "has_catalyst": False,
                "intraday_score": round(score, 3),
            }

    for g in movers.get("most_active", []):
        t = g["ticker"]
        if t not in candidates:
            candidates[t] = {
                "ticker": t,
                "gap_pct": 0,
                "day_change_pct": 0,
                "volume": g.get("volume", 0),
                "trade_count": g.get("trade_count", 0),
                "volume_ratio": 0,
                "source": "alpaca_active",
                "direction": "long",
                "has_catalyst": False,
                "intraday_score": 0.2,  # base score for high activity
            }

    logger.info("Alpaca screener candidates: %d", len(candidates))

    # 2. Gap scanner (backup / supplement from our liquid universe)
    gaps = scan_premarket_gaps(top_n=10)
    for g in gaps:
        t = g["ticker"]
        if t not in candidates:
            score = min(abs(g["gap_pct"]) / 10.0, 1.0) * 0.4
            score += min(g.get("volume_ratio", 0) / 5.0, 1.0) * 0.3
            candidates[t] = {
                **g,
                "source": "gap_scan",
                "has_catalyst": False,
                "intraday_score": round(score, 3),
            }
    logger.info("After gap scan: %d total candidates", len(candidates))

    # 3. News catalysts
    catalysts = scan_news_catalysts()
    news_tickers = {c["ticker"] for c in catalysts}
    logger.info("News catalysts: %d tickers", len(news_tickers))

    # Boost score for candidates with news catalysts
    for t in news_tickers:
        if t in candidates:
            candidates[t]["has_catalyst"] = True
            candidates[t]["intraday_score"] = round(candidates[t]["intraday_score"] + 0.3, 3)
        else:
            # News-only candidate
            cat = next((c for c in catalysts if c["ticker"] == t), {})
            candidates[t] = {
                "ticker": t,
                "gap_pct": 0,
                "day_change_pct": 0,
                "volume_ratio": 0,
                "source": "news",
                "has_catalyst": True,
                "headline": cat.get("headline", ""),
                "sentiment": cat.get("sentiment", "neutral"),
                "intraday_score": 0.3,
                "direction": "long" if cat.get("sentiment") == "bullish" else "short",
            }

    # Sort by score
    ranked = sorted(candidates.values(), key=lambda x: x["intraday_score"], reverse=True)
    return ranked[:top_n]


def _get_liquid_universe() -> list[str]:
    """Get a liquid universe for gap scanning (~200 most traded stocks)."""
    # Top liquid tickers by sector — known high-volume names
    liquid = [
        # Mega tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "CRM",
        "ORCL", "ADBE", "NFLX", "AVGO", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "MRVL",
        # Financials
        "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "USB", "PNC", "TFC",
        "COF", "AXP", "BK", "FITB", "RF", "KEY", "HBAN", "CFG", "ZION", "CMA",
        # Healthcare
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT", "BMY", "AMGN",
        "GILD", "ISRG", "VRTX", "REGN", "MRNA", "BNTX", "BIIB",
        # Consumer
        "WMT", "COST", "HD", "TGT", "LOW", "SBUX", "MCD", "NKE", "DIS", "ABNB",
        # Energy
        "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "VLO", "PSX", "DVN",
        # Industrials
        "BA", "CAT", "DE", "GE", "HON", "UPS", "FDX", "RTX", "LMT", "NOC",
        # Meme / High-vol
        "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "NIO", "HOOD", "COIN", "MSTR",
        "RDDT", "APP", "SMCI", "ARM", "IONQ", "RGTI", "QUBT",
        # Crypto-adjacent
        "MARA", "RIOT", "CLSK", "BITF", "HUT",
        # Chinese ADRs
        "BABA", "JD", "PDD", "BIDU", "LI", "XPEV",
        # ETFs (for gap/momentum reference)
        "SPY", "QQQ", "IWM", "DIA",
    ]
    return liquid
