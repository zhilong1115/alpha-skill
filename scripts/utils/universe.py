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



# ---------------------------------------------------------------------------
# Broad market scanner: Reddit trending + volume spikes
# ---------------------------------------------------------------------------

import re
import time as _time
import requests as _requests
from functools import lru_cache
from datetime import datetime as _dt

_REDDIT_UA = {"User-Agent": "us-stock-trading-scanner/1.0 (educational)"}

# Comprehensive common-word filter (60+)
_COMMON_WORDS = {
    "I", "A", "AM", "PM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO", "UP",
    "US", "WE", "CEO", "CFO", "CTO", "COO", "IPO", "ETF", "SEC", "FDA", "EPS",
    "ATH", "DD", "YOLO", "FOMO", "IMO", "TIL", "PSA", "FYI", "LOL", "OMG",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAD", "HER",
    "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW",
    "OLD", "SEE", "WAY", "WHO", "DID", "GET", "GOT", "LET", "SAY", "SHE", "TOO",
    "USE", "DAD", "MOM", "RUN", "WIN", "BIG", "TOP", "LOW", "HIGH", "JUST", "VERY",
    "EDIT", "TLDR", "LMAO", "WHAT", "WITH", "THIS", "THAT", "FROM", "THEM",
    "THEN", "THAN", "WHEN", "WILL", "MORE", "MOST", "SOME", "BEEN", "HAVE",
    "EACH", "MAKE", "LIKE", "LONG", "LOOK", "MANY", "OVER", "SUCH", "TAKE",
    "ONLY", "ALSO", "BACK", "AFTER", "YEAR", "INTO", "YOUR", "JUST", "NEXT",
    "FREE", "BEST", "GOOD", "WELL", "EVEN", "HERE", "MUCH", "STILL", "KEEP",
    "HOLD", "SELL", "BUY", "CALL", "PUTS", "PUT", "PAYS", "GAIN", "LOSS",
    "MOVE", "PLAY", "RIP", "ROPE", "PUMP", "DUMP", "MOON", "BEAR", "BULL",
    "OPEN", "YEAH", "REAL", "TRUE", "EVER", "SURE", "SAME", "DOWN",
}

_TICKER_RE = re.compile(r'\$([A-Z]{2,5})\b|(?<![a-zA-Z])([A-Z]{2,5})(?![a-zA-Z])')


def _extract_tickers_from_text(text: str) -> list[str]:
    """Extract potential ticker symbols from text."""
    matches = _TICKER_RE.findall(text)
    tickers = []
    for dollar_match, bare_match in matches:
        t = dollar_match or bare_match
        if t and t not in _COMMON_WORDS:
            tickers.append(t)
    return tickers


def _fetch_subreddit_posts(subreddit: str, limit: int = 50) -> list[dict]:
    """Fetch hot posts from a subreddit using public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    params = {"limit": limit}
    try:
        resp = _requests.get(url, headers=_REDDIT_UA, params=params, timeout=10)
        if resp.status_code == 429:
            return []
        resp.raise_for_status()
        data = resp.json()
        posts = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            posts.append({
                "title": d.get("title", ""),
                "selftext": d.get("selftext", "")[:500],
                "created_utc": d.get("created_utc", 0),
                "score": d.get("score", 0),
                "subreddit": subreddit,
            })
        return posts
    except Exception:
        return []


def get_reddit_trending_tickers(
    subreddits: list[str] | None = None,
    limit: int = 100,
) -> list[str]:
    """Scrape Reddit for currently trending stock tickers.

    Scans hot posts in given subreddits, extracts ticker-like patterns,
    filters common words, returns top tickers by mention count.

    Args:
        subreddits: List of subreddits to scan.
        limit: Max tickers to return.

    Returns:
        List of ticker symbols sorted by mention frequency.
    """
    if subreddits is None:
        subreddits = ["wallstreetbets", "stocks", "pennystocks", "shortsqueeze"]

    from collections import Counter
    ticker_counts: Counter = Counter()

    for sub in subreddits:
        posts = _fetch_subreddit_posts(sub, limit=50)
        for post in posts:
            text = post["title"] + " " + post.get("selftext", "")
            tickers = _extract_tickers_from_text(text)
            ticker_counts.update(tickers)
        _time.sleep(2)  # Rate limit

    # Return top tickers
    return [t for t, _ in ticker_counts.most_common(limit)]


# Volume screener watchlist
HOT_WATCHLIST = [
    # Past/current meme stocks
    "GME", "AMC", "BBBY", "SPRT", "SOC", "WISH", "CLOV", "BB", "NOK", "PLTR",
    "SOFI", "LCID", "RIVN", "NIO", "MARA", "RIOT", "COIN", "HOOD", "RBLX",
    # Popular small/mid caps
    "SNAP", "PINS", "ROKU", "DKNG", "PENN", "CHWY", "ETSY", "ABNB", "DASH",
    "CRWD", "NET", "SNOW", "DDOG", "ZS", "OKTA", "TWLO", "SQ", "AFRM",
    "UPST", "PATH", "IONQ", "RGTI", "QUBT", "SMCI", "ARM", "CELH",
    # Biotech/pharma (volatile)
    "MRNA", "BNTX", "NVAX", "SAVA", "PTON",
    # Energy/commodities
    "FCEL", "PLUG", "ENPH", "SEDG", "RUN",
    # Chinese ADRs
    "BABA", "JD", "PDD", "BIDU", "LI", "XPEV",
    # Crypto-adjacent
    "MSTR", "CLSK", "BITF", "HUT",
    # Recent IPOs / SPACs
    "RDDT", "CART", "BIRK", "VRT", "APP",
]


def get_volume_screener_tickers(min_volume_ratio: float = 3.0, top_n: int = 20) -> list[str]:
    """Find stocks with unusual volume spikes using yfinance.

    Compares latest volume to 20-day average for a curated watchlist.
    Returns tickers where volume ratio exceeds *min_volume_ratio*.

    Args:
        min_volume_ratio: Minimum volume/avg ratio to qualify.
        top_n: Max tickers to return.

    Returns:
        List of ticker symbols with unusual volume.
    """
    import yfinance as yf

    results: list[tuple[str, float]] = []

    # Process in batches for efficiency
    batch_size = 20
    for i in range(0, len(HOT_WATCHLIST), batch_size):
        batch = HOT_WATCHLIST[i:i + batch_size]
        tickers_str = " ".join(batch)
        try:
            data = yf.download(tickers_str, period="1mo", progress=False, threads=True)
            if data.empty:
                continue
            vol = data.get("Volume")
            if vol is None:
                continue
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        series = vol
                    else:
                        if ticker not in vol.columns:
                            continue
                        series = vol[ticker]
                    series = series.dropna()
                    if len(series) < 5:
                        continue
                    latest = float(series.iloc[-1])
                    avg_20 = float(series.iloc[-21:-1].mean()) if len(series) >= 21 else float(series.iloc[:-1].mean())
                    if avg_20 > 0:
                        ratio = latest / avg_20
                        if ratio >= min_volume_ratio:
                            results.append((ticker, ratio))
                except Exception:
                    continue
        except Exception:
            continue

    results.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in results[:top_n]]


# Cache for full universe (1 hour TTL)
_universe_cache: dict = {"data": None, "timestamp": 0.0}
_CACHE_TTL = 3600  # 1 hour


def get_full_universe() -> dict:
    """Get the complete scanning universe with caching (1 hour TTL).

    Returns:
        Dict with keys: sp500, reddit_trending, volume_spikes, all_unique.
    """
    now = _time.time()
    if _universe_cache["data"] and (now - _universe_cache["timestamp"]) < _CACHE_TTL:
        return _universe_cache["data"]

    print("[universe] Building full universe...")

    sp500 = get_sp500_tickers()
    print(f"[universe] S&P 500: {len(sp500)} tickers")

    reddit = get_reddit_trending_tickers()
    print(f"[universe] Reddit trending: {len(reddit)} tickers")

    volume = get_volume_screener_tickers()
    print(f"[universe] Volume spikes: {len(volume)} tickers")

    all_unique = sorted(set(sp500 + reddit + volume))

    result = {
        "sp500": sp500,
        "reddit_trending": reddit,
        "volume_spikes": volume,
        "all_unique": all_unique,
    }

    _universe_cache["data"] = result
    _universe_cache["timestamp"] = now
    return result


def save_premarket_picks(tickers: list[str]) -> None:
    """Save pre-market scan top picks for intraday smart universe."""
    import json
    picks_path = Path(__file__).resolve().parents[2] / "data" / "premarket_picks.json"
    picks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(picks_path, "w") as f:
        json.dump({"tickers": tickers, "date": _dt.now().strftime("%Y-%m-%d"), "updated": _dt.now().isoformat()}, f)
    print(f"[universe] Saved {len(tickers)} pre-market picks")


def get_smart_universe() -> dict:
    """Build a focused intraday universe (~30-80 tickers) from 4 sources:

    1. Current Alpaca positions (may sell)
    2. News alert tickers (breaking news)
    3. Pre-market scan top picks (saved from morning scan)
    4. Reddit trending (catch GME-style YOLO plays)

    Returns:
        Dict with keys: positions, news_tickers, premarket_picks, reddit_hot, all_unique
    """
    import json

    data_dir = Path(__file__).resolve().parents[2] / "data"

    # 1. Current positions
    position_tickers = []
    try:
        from scripts.core.executor import get_positions
        positions = get_positions()
        position_tickers = [p["ticker"] for p in positions]
        print(f"[smart] Positions: {len(position_tickers)} tickers")
    except Exception as e:
        print(f"[smart] Could not fetch positions: {e}")

    # 2. News alert tickers (from daemon)
    news_tickers = []
    try:
        alerts_path = data_dir / "alerts" / "pending.json"
        if alerts_path.exists():
            alerts = json.loads(alerts_path.read_text())
            # Only recent alerts (last 2 hours) with actionable sentiment
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
            for a in alerts:
                try:
                    ts = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
                    if ts > cutoff and a.get("action_type") in ("buy", "sell"):
                        syms = a.get("symbols", [])
                        if a.get("ticker") and a["ticker"] != "MACRO":
                            syms.append(a["ticker"])
                        news_tickers.extend(syms)
                except Exception:
                    continue
            news_tickers = list(set(news_tickers))
            print(f"[smart] News alerts: {len(news_tickers)} tickers")
    except Exception as e:
        print(f"[smart] Could not read news alerts: {e}")

    # 3. Pre-market picks (saved from morning scan)
    premarket_picks = []
    try:
        picks_path = data_dir / "premarket_picks.json"
        if picks_path.exists():
            picks_data = json.loads(picks_path.read_text())
            if picks_data.get("date") == _dt.now().strftime("%Y-%m-%d"):
                premarket_picks = picks_data.get("tickers", [])
                print(f"[smart] Pre-market picks: {len(premarket_picks)} tickers")
            else:
                print("[smart] Pre-market picks stale (different date), skipping")
    except Exception as e:
        print(f"[smart] Could not read premarket picks: {e}")

    # 4. Reddit trending (top 20, quick scan)
    reddit_hot = []
    try:
        reddit_hot = get_reddit_trending_tickers(limit=20)
        print(f"[smart] Reddit hot: {len(reddit_hot)} tickers")
    except Exception as e:
        print(f"[smart] Reddit scan failed: {e}")

    all_unique = sorted(set(position_tickers + news_tickers + premarket_picks + reddit_hot))
    print(f"[smart] Total smart universe: {len(all_unique)} tickers")

    return {
        "positions": position_tickers,
        "news_tickers": news_tickers,
        "premarket_picks": premarket_picks,
        "reddit_hot": reddit_hot,
        "all_unique": all_unique,
    }


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
