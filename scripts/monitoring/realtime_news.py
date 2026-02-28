"""Real-time news daemon via Alpaca WebSocket + multi-source RSS polling.

Architecture:
  1. Alpaca News WebSocket (primary) — sub-second latency, Benzinga source
  2. RSS feeds (secondary) — CNBC, Reuters, MarketWatch, polled every 60s
  3. Finnhub REST API (tertiary) — general market news, polled every 120s

When a critical/high-urgency news item is detected:
  1. Classify urgency (critical/high/medium/low)
  2. Match to watched tickers (or detect macro events)
  3. Write alert to /data/alerts/pending.json
  4. The cron-triggered news monitor reads pending alerts and acts on them

Can also run standalone for testing: python -m scripts.monitoring.realtime_news
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser

logger = logging.getLogger("realtime_news")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALERT_PATH = PROJECT_ROOT / "data" / "alerts"
PENDING_FILE = ALERT_PATH / "pending.json"
SEEN_FILE = ALERT_PATH / "seen_ids.json"
DAEMON_PID_FILE = PROJECT_ROOT / "data" / "news_daemon.pid"
DAEMON_LOG_FILE = PROJECT_ROOT / "data" / "news_daemon.log"

# ── Configuration ──────────────────────────────────────────────────

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]


def get_dynamic_watchlist() -> list[str]:
    """Build watchlist dynamically: defaults + current Alpaca positions.

    This ensures any stock we hold is always monitored for breaking news.
    """
    watchlist = set(DEFAULT_WATCHLIST)

    # Add current positions from Alpaca
    try:
        from scripts.core.executor import get_positions
        positions = get_positions()
        for pos in positions:
            tk = pos.get("ticker", "").upper()
            if tk:
                watchlist.add(tk)
    except Exception:
        pass

    # Add virtual (A/B test) positions
    try:
        from scripts.core.ab_tracker import load_state
        ab = load_state()
        for tk in ab.b_positions:
            watchlist.add(tk.upper())
    except Exception:
        pass

    return sorted(watchlist)

# Macro keywords that affect the whole market (not ticker-specific)
MACRO_CRITICAL = [
    "federal reserve", "fed rate", "rate hike", "rate cut", "fomc",
    "tariff", "trade war", "sanction", "war ", "invasion",
    "recession", "default", "debt ceiling", "government shutdown",
    "banking crisis", "bank failure", "emergency",
]
MACRO_HIGH = [
    "inflation", "cpi ", "ppi ", "jobs report", "nonfarm", "unemployment",
    "gdp ", "housing", "consumer confidence", "retail sales",
    "oil price", "crude oil", "opec", "china", "treasury yield",
]

# Crypto-specific urgency keywords
CRYPTO_CRITICAL = [
    "sec sues", "sec charges", "sec lawsuit", "binance shut", "tether depeg",
    "usdt depeg", "usdc depeg", "stablecoin depeg", "exchange hack",
    "rug pull", "exploit", "flash crash", "bitcoin etf reject",
    "bitcoin ban", "crypto ban", "mt. gox", "ftx", "celsius",
    "bitcoin etf approv", "ethereum etf", "solana etf",
]
CRYPTO_HIGH = [
    # Don't include generic terms like "bitcoin", "btc" — they match everything
    "crypto regulation", "crypto bill", "stablecoin bill",
    "whale alert", "whale transfer", "large transfer",
    "defi hack", "bridge hack", "protocol exploit",
    "halving", "hard fork",
    "blackrock crypto", "fidelity crypto", "grayscale",
    "bitcoin liquidation", "crypto liquidation", "mass liquidation",
    "bitcoin plunge", "bitcoin crash", "crypto crash",
    "bitcoin surge", "bitcoin rally", "crypto rally",
]

# Ticker-specific urgency keywords (stocks)
TICKER_CRITICAL = [
    "earnings beat", "earnings miss", "earnings blowout", "earnings shock",
    "guidance raise", "guidance cut", "guidance lower",
    "fda approv", "fda reject", "acquire",
    "merger", "bankrupt", "fraud", "sec investigat", "recall",
    "data breach", "ceo resign", "ceo fired",
]
TICKER_HIGH = [
    "upgrade", "downgrade", "price target", "beat", "miss",
    "revenue", "profit", "contract", "partnership", "dividend",
    "buyback", "stock split", "offering", "dilut", "layoff",
]

RSS_FEEDS = [
    # US Stock / Macro
    ("CNBC_Top", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC_Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Reuters_Biz", "https://feeds.reuters.com/reuters/businessNews"),
    ("Yahoo_Finance", "https://finance.yahoo.com/news/rssindex"),
    # Crypto
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("TheBlock", "https://www.theblock.co/rss.xml"),
    ("Decrypt", "https://decrypt.co/feed"),
]

ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
ALPACA_SANDBOX_WS_URL = "wss://stream.data.sandbox.alpaca.markets/v1beta1/news"

# ── Alert management ───────────────────────────────────────────────


def _ensure_dirs() -> None:
    ALERT_PATH.mkdir(parents=True, exist_ok=True)


def _load_seen() -> set[str]:
    try:
        if SEEN_FILE.exists():
            data = json.loads(SEEN_FILE.read_text())
            # Keep last 5000 IDs to prevent unbounded growth
            return set(data[-5000:])
    except Exception:
        pass
    return set()


def _save_seen(seen: set[str]) -> None:
    try:
        _ensure_dirs()
        # Only keep last 5000
        items = sorted(seen)[-5000:]
        SEEN_FILE.write_text(json.dumps(items))
    except Exception as e:
        logger.warning("Failed to save seen IDs: %s", e)


def _load_pending() -> list[dict]:
    try:
        if PENDING_FILE.exists():
            return json.loads(PENDING_FILE.read_text()) or []
    except Exception:
        pass
    return []


def _save_pending(alerts: list[dict]) -> None:
    try:
        _ensure_dirs()
        # Keep last 100 pending alerts
        PENDING_FILE.write_text(json.dumps(alerts[-100:], indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to save pending alerts: %s", e)


def add_alert(alert: dict) -> None:
    """Add a new alert to the pending queue and notify the agent for critical/high."""
    pending = _load_pending()
    pending.append(alert)
    _save_pending(pending)
    logger.info("🚨 ALERT: [%s] %s — %s", alert.get("urgency", "?"), alert.get("ticker", "MACRO"), alert.get("headline", "")[:80])

    # Notify Alpha agent only for CRITICAL news (not high — too many false positives)
    # On weekends: only notify for crypto or macro — stock news isn't actionable
    if alert.get("urgency") == "critical":
        is_weekend = datetime.now().weekday() >= 5  # Sat=5, Sun=6
        is_crypto_or_macro = alert.get("is_crypto") or alert.get("is_macro")
        if not is_weekend or is_crypto_or_macro:
            _notify_agent(alert)


def _notify_agent(alert: dict) -> None:
    """Send breaking news alert via two channels for reliability:
    
    1. openclaw message send → posts directly to Telegram group (reliable, instant)
    2. openclaw agent → triggers Alpha agent turn to evaluate and act
    
    Both run in a background thread to avoid blocking the async event loop.
    """
    import threading

    def _send():
        import subprocess

        urgency = alert.get("urgency", "unknown").upper()
        headline = alert.get("headline", "")[:200]
        sentiment = alert.get("sentiment", "neutral")
        action = alert.get("action_type", "monitor")
        tickers = ", ".join(alert.get("matched_tickers", [])) or "N/A"
        source = alert.get("source", "unknown")
        is_crypto = alert.get("is_crypto", False)

        # Format alert message
        emoji = "🚨" if urgency == "CRITICAL" else "⚠️"
        market = "CRYPTO" if is_crypto else "STOCK"
        msg = (
            f"{emoji} BREAKING [{urgency}] [{market}]\n"
            f"{headline}\n"
            f"Tickers: {tickers} | Sentiment: {sentiment} | Action: {action}\n"
            f"Source: {source}"
        )

        # Step 1: Post to Telegram group directly (reliable, instant visibility)
        try:
            result = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "telegram",
                    "--target", "-5119023195",
                    "--message", msg,
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info("✅ Alert posted to Telegram: [%s] %s", urgency, alert.get("ticker", "MACRO"))
            else:
                logger.warning("Telegram post failed (rc=%d): %s", result.returncode, result.stderr[:200])
        except Exception as e:
            logger.warning("Failed to post to Telegram: %s", e)

        # Step 2: Trigger Alpha agent turn to evaluate and potentially act
        agent_msg = (
            f"{msg}\n\n"
            f"⚡ This is a real-time news interrupt. Check your positions immediately.\n"
            f"Use agent_tools to assess impact and decide: hold, close, or adjust stops."
        )
        try:
            result = subprocess.run(
                [
                    "openclaw", "agent",
                    "--agent", "alpha",
                    "--session-id", "5c56ab91-c23f-4a8b-8f6f-92f4a6c50cca",
                    "--message", agent_msg,
                    "--deliver",
                ],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                logger.info("✅ Agent turn triggered: [%s] %s", urgency, alert.get("ticker", "MACRO"))
            else:
                logger.warning("Agent turn failed (rc=%d): %s", result.returncode, result.stderr[:200])
        except Exception as e:
            logger.warning("Failed to trigger agent turn: %s", e)

    # Run in thread to not block async loop
    t = threading.Thread(target=_send, daemon=True)
    t.start()


def pop_pending_alerts() -> list[dict]:
    """Pop all pending alerts (consumed by the cron news monitor)."""
    alerts = _load_pending()
    if alerts:
        _save_pending([])
    return alerts


# ── News classification ────────────────────────────────────────────


BULLISH_KEYWORDS = [
    "fda approv", "beat", "beats", "upgrade", "record revenue", "record profit",
    "contract win", "new contract", "awarded contract", "receives contract",
    "secures contract", "wins contract", "partnership",
    "buyback", "dividend hike", "dividend increase",
    "stock split", "raised guidance", "raises guidance", "raise guidance",
    "strong demand", "strong growth", "strong earnings",
    "blowout", "crush", "exceed", "above expectation", "surpass",
    "rate cut", "cuts rate", "cut rate", "cuts interest", "lower rate",
    "stimulus", "easing", "dovish",
    "all-time high", "new high", "soar", "surge", "rally",
    "billion deal", "million deal", "acquisition of",
]
BEARISH_KEYWORDS = [
    "fda reject", "miss", "downgrade", "guidance cut", "lower guidance",
    "recall", "fraud", "sec investigat", "bankrupt", "layoff",
    "data breach", "ceo resign", "ceo fired", "dilut", "offering",
    "rate hike", "tariff", "sanction", "war ", "invasion", "recession",
    "default", "crash",
    # Crypto bearish
    "sec sues", "sec charges", "exchange hack", "rug pull", "exploit",
    "depeg", "flash crash", "crypto ban", "whale dump", "liquidation",
    "mt. gox distribut", "ftx creditor",
]


def classify_news(headline: str, summary: str = "", symbols: list[str] | None = None) -> dict:
    """Classify a news item by urgency, relevance, and sentiment direction.

    Returns:
        Dict with urgency, matched_tickers, is_macro, keywords_hit,
        sentiment ("bullish"/"bearish"/"neutral"), action_type ("buy"/"sell"/"monitor").
    """
    text = f"{headline} {summary}".lower()
    symbols = [s.upper() for s in (symbols or [])]

    result = {
        "urgency": "low",
        "matched_tickers": [],
        "is_macro": False,
        "keywords_hit": [],
        "sentiment": "neutral",
        "action_type": "monitor",  # "buy", "sell", "monitor"
    }

    # Check macro critical
    for kw in MACRO_CRITICAL:
        if kw in text:
            result["urgency"] = "critical"
            result["is_macro"] = True
            result["keywords_hit"].append(kw)

    # Check macro high (only upgrade if not already critical)
    if result["urgency"] != "critical":
        for kw in MACRO_HIGH:
            if kw in text:
                if result["urgency"] != "high":
                    result["urgency"] = "high"
                result["is_macro"] = True
                result["keywords_hit"].append(kw)

    # Check crypto critical
    for kw in CRYPTO_CRITICAL:
        if kw in text:
            result["urgency"] = "critical"
            result["is_crypto"] = True
            result["keywords_hit"].append(kw)

    # Check crypto high
    if result["urgency"] not in ("critical",):
        for kw in CRYPTO_HIGH:
            if kw in text:
                result["urgency"] = "high"
                result["is_crypto"] = True
                result["keywords_hit"].append(kw)

    # Check ticker-specific (stocks)
    for kw in TICKER_CRITICAL:
        if kw in text:
            result["urgency"] = "critical"
            result["keywords_hit"].append(kw)

    if result["urgency"] not in ("critical",):
        for kw in TICKER_HIGH:
            if kw in text:
                result["urgency"] = "high"
                result["keywords_hit"].append(kw)

    # Match tickers (dynamic watchlist includes positions)
    watchlist_set = set(get_dynamic_watchlist())
    result["matched_tickers"] = [s for s in symbols if s in watchlist_set]

    # Also check headline for ticker mentions
    for tk in watchlist_set:
        if tk.lower() in text or tk in headline.upper():
            if tk not in result["matched_tickers"]:
                result["matched_tickers"].append(tk)

    # Company name matching
    company_map = {
        "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
        "amazon": "AMZN", "nvidia": "NVDA", "meta platforms": "META", "facebook": "META",
        "tesla": "TSLA",
    }
    for name, tk in company_map.items():
        if name in text and tk not in result["matched_tickers"]:
            result["matched_tickers"].append(tk)

    # Sentiment direction
    bull_hits = [kw for kw in BULLISH_KEYWORDS if kw in text]
    bear_hits = [kw for kw in BEARISH_KEYWORDS if kw in text]
    bull_score = len(bull_hits)
    bear_score = len(bear_hits)

    if bull_score > bear_score:
        result["sentiment"] = "bullish"
        if result["urgency"] in ("critical", "high"):
            # Buy opportunity — use symbols from news even if not in watchlist
            target_tickers = result["matched_tickers"] or [s for s in symbols if s]
            if target_tickers:
                result["action_type"] = "buy"
                result["matched_tickers"] = target_tickers
    elif bear_score > bull_score:
        result["sentiment"] = "bearish"
        if result["urgency"] in ("critical", "high"):
            result["action_type"] = "sell"
    # else neutral, action_type stays "monitor"

    return result


def _news_id(headline: str, source: str = "") -> str:
    """Generate a dedup ID for a news item."""
    return hashlib.md5(f"{source}:{headline}".encode()).hexdigest()[:16]


# ── Alpaca WebSocket ───────────────────────────────────────────────


async def alpaca_news_stream(
    watchlist: list[str],
    seen: set[str],
    stop_event: asyncio.Event,
) -> None:
    """Connect to Alpaca news WebSocket and process events."""
    try:
        import websockets
    except ImportError:
        logger.error("websockets not installed")
        return

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        return

    # Paper trading uses the live data stream (not sandbox) — Alpaca paper keys work on live data endpoints
    ws_url = ALPACA_WS_URL

    reconnect_delay = 1
    max_reconnect_delay = 60

    while not stop_event.is_set():
        try:
            logger.info("Connecting to Alpaca news WebSocket: %s", ws_url)
            async with websockets.connect(ws_url) as ws:
                # Wait for initial "connected" message
                init_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                logger.info("Init: %s", init_resp[:200])

                # Authenticate
                auth_msg = json.dumps({
                    "action": "auth",
                    "key": api_key,
                    "secret": secret_key,
                })
                await ws.send(auth_msg)
                auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                logger.info("Auth: %s", auth_resp[:200])

                # Subscribe to all news
                sub_msg = json.dumps({"action": "subscribe", "news": ["*"]})
                await ws.send(sub_msg)
                sub_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                logger.info("Subscribe: %s", sub_resp[:200])

                reconnect_delay = 1  # Reset on successful connect
                logger.info("✅ Alpaca news stream connected")

                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        # Send ping to keep alive
                        continue

                    try:
                        messages = json.loads(raw)
                        if not isinstance(messages, list):
                            messages = [messages]

                        for msg in messages:
                            if msg.get("T") != "n":
                                continue

                            headline = msg.get("headline", "")
                            summary = msg.get("summary", "")
                            symbols = msg.get("symbols", [])
                            source = msg.get("source", "alpaca")
                            nid = str(msg.get("id", _news_id(headline, source)))

                            if nid in seen:
                                continue
                            seen.add(nid)

                            # Classify
                            classification = classify_news(headline, summary, symbols)

                            if classification["urgency"] in ("critical", "high"):
                                alert = {
                                    "source": f"alpaca:{source}",
                                    "headline": headline,
                                    "summary": summary[:300],
                                    "symbols": symbols,
                                    "urgency": classification["urgency"],
                                    "is_macro": classification["is_macro"],
                                    "ticker": classification["matched_tickers"][0] if classification["matched_tickers"] else "MACRO",
                                    "matched_tickers": classification["matched_tickers"],
                                    "keywords": classification["keywords_hit"],
                                    "sentiment": classification["sentiment"],
                                    "action_type": classification["action_type"],
                                    "timestamp": msg.get("created_at", datetime.now(timezone.utc).isoformat()),
                                    "url": msg.get("url", ""),
                                }
                                add_alert(alert)

                            # Log all news at debug level
                            logger.debug(
                                "[%s] %s — %s | symbols=%s",
                                classification["urgency"],
                                source,
                                headline[:80],
                                symbols,
                            )

                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON from WebSocket: %s", raw[:200])

        except asyncio.CancelledError:
            break
        except Exception as e:
            if stop_event.is_set():
                break
            logger.warning("WebSocket error: %s. Reconnecting in %ds...", e, reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    _save_seen(seen)
    logger.info("Alpaca news stream stopped")


# ── RSS polling ────────────────────────────────────────────────────


async def rss_poll_loop(
    watchlist: list[str],
    seen: set[str],
    stop_event: asyncio.Event,
    interval: int = 60,
) -> None:
    """Poll RSS feeds every `interval` seconds."""
    logger.info("RSS polling started (interval=%ds, %d feeds)", interval, len(RSS_FEEDS))

    while not stop_event.is_set():
        for feed_name, feed_url in RSS_FEEDS:
            if stop_event.is_set():
                break
            try:
                feed = await asyncio.get_event_loop().run_in_executor(
                    None, feedparser.parse, feed_url
                )
                for entry in feed.entries[:15]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    nid = _news_id(title, feed_name)

                    if nid in seen:
                        continue
                    seen.add(nid)

                    classification = classify_news(title, summary)

                    if classification["urgency"] in ("critical", "high"):
                        alert = {
                            "source": f"rss:{feed_name}",
                            "headline": title,
                            "summary": summary[:300],
                            "symbols": [],
                            "urgency": classification["urgency"],
                            "is_macro": classification["is_macro"],
                            "ticker": classification["matched_tickers"][0] if classification["matched_tickers"] else "MACRO",
                            "matched_tickers": classification["matched_tickers"],
                            "keywords": classification["keywords_hit"],
                            "sentiment": classification["sentiment"],
                            "action_type": classification["action_type"],
                            "timestamp": entry.get("published", datetime.now(timezone.utc).isoformat()),
                            "url": entry.get("link", ""),
                        }
                        add_alert(alert)

            except Exception as e:
                logger.warning("RSS feed %s error: %s", feed_name, e)

        # Save seen IDs periodically
        _save_seen(seen)

        # Wait for next poll
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("RSS polling stopped")


# ── Finnhub REST polling ──────────────────────────────────────────


async def finnhub_poll_loop(
    watchlist: list[str],
    seen: set[str],
    stop_event: asyncio.Event,
    interval: int = 120,
) -> None:
    """Poll Finnhub general news every `interval` seconds."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        logger.info("FINNHUB_API_KEY not set — skipping Finnhub polling")
        return

    import urllib.request

    logger.info("Finnhub polling started (interval=%ds)", interval)

    while not stop_event.is_set():
        try:
            # Fetch both general and crypto news
            for category in ["general", "crypto"]:
                url = f"https://finnhub.io/api/v1/news?category={category}&token={api_key}"
                req = urllib.request.Request(url)
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, urllib.request.urlopen, req
                )
                data = json.loads(resp.read())

                for item in data[:20]:
                    headline = item.get("headline", "")
                    summary = item.get("summary", "")
                    nid = _news_id(headline, f"finnhub:{category}")

                    if nid in seen:
                        continue
                    seen.add(nid)

                    classification = classify_news(headline, summary, item.get("related", "").split(","))

                    if classification["urgency"] in ("critical", "high"):
                        alert = {
                            "source": f"finnhub:{category}",
                            "headline": headline,
                            "summary": summary[:300],
                            "symbols": item.get("related", "").split(","),
                            "urgency": classification["urgency"],
                            "is_macro": classification["is_macro"],
                            "ticker": classification["matched_tickers"][0] if classification["matched_tickers"] else "MACRO",
                            "matched_tickers": classification["matched_tickers"],
                            "keywords": classification["keywords_hit"],
                            "sentiment": classification["sentiment"],
                            "action_type": classification["action_type"],
                            "timestamp": datetime.fromtimestamp(item.get("datetime", 0), tz=timezone.utc).isoformat(),
                            "url": item.get("url", ""),
                        }
                        add_alert(alert)

        except Exception as e:
            logger.warning("Finnhub poll error: %s", e)

        _save_seen(seen)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Finnhub polling stopped")


# ── Daemon management ─────────────────────────────────────────────


def write_pid() -> None:
    """Write current PID to file."""
    _ensure_dirs()
    DAEMON_PID_FILE.write_text(str(os.getpid()))


def read_pid() -> Optional[int]:
    """Read daemon PID from file."""
    try:
        if DAEMON_PID_FILE.exists():
            return int(DAEMON_PID_FILE.read_text().strip())
    except Exception:
        pass
    return None


def is_running() -> bool:
    """Check if the daemon is currently running."""
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 = check if process exists
        return True
    except (OSError, ProcessLookupError):
        return False


def stop_daemon() -> bool:
    """Stop the running daemon."""
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5 seconds
        for _ in range(50):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                DAEMON_PID_FILE.unlink(missing_ok=True)
                return True
        # Force kill
        os.kill(pid, signal.SIGKILL)
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return True
    except (OSError, ProcessLookupError):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return False


# ── Main async runner ─────────────────────────────────────────────


# ── On-chain whale monitoring (free, no API key) ─────────────────


# Thresholds for whale alerts (in USD)
WHALE_THRESHOLD_USD = 50_000_000  # $50M+ transactions trigger alert
BTC_WHALE_THRESHOLD_BTC = 500     # or 500+ BTC


async def whale_monitor_loop(
    seen: set[str],
    stop_event: asyncio.Event,
    interval: int = 120,
) -> None:
    """Monitor BTC mempool for whale-sized transactions.

    Uses blockchain.info free API (no key required).
    Polls every `interval` seconds for large unconfirmed BTC transactions.
    """
    import urllib.request

    logger.info("🐋 Whale monitor started (interval=%ds, threshold=$%dM / %d BTC)",
                interval, WHALE_THRESHOLD_USD // 1_000_000, BTC_WHALE_THRESHOLD_BTC)

    while not stop_event.is_set():
        try:
            # Fetch unconfirmed BTC transactions
            url = "https://blockchain.info/unconfirmed-transactions?format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=15)
            )
            data = json.loads(resp.read())

            for tx in data.get("txs", []):
                tx_hash = tx.get("hash", "")
                nid = f"whale:btc:{tx_hash[:16]}"
                if nid in seen:
                    continue

                # Calculate total output value
                total_sat = sum(o.get("value", 0) for o in tx.get("out", []))
                total_btc = total_sat / 1e8

                if total_btc < BTC_WHALE_THRESHOLD_BTC:
                    continue

                seen.add(nid)

                # Estimate USD value (use a rough price, will be close enough)
                try:
                    price_resp = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: urllib.request.urlopen(
                            urllib.request.Request("https://blockchain.info/ticker",
                                                   headers={"User-Agent": "Mozilla/5.0"}),
                            timeout=5
                        )
                    )
                    price_data = json.loads(price_resp.read())
                    btc_price = price_data.get("USD", {}).get("last", 65000)
                except Exception:
                    btc_price = 65000  # fallback

                total_usd = total_btc * btc_price

                if total_usd < WHALE_THRESHOLD_USD:
                    continue

                # Determine if it's going to/from exchange (simplified)
                headline = f"🐋 BTC Whale: {total_btc:,.1f} BTC (${total_usd/1e6:,.1f}M) transferred"

                alert = {
                    "source": "whale:blockchain.info",
                    "headline": headline,
                    "summary": f"Transaction hash: {tx_hash[:32]}... | {total_btc:,.2f} BTC = ${total_usd:,.0f}",
                    "symbols": ["BTC"],
                    "urgency": "high",
                    "is_macro": False,
                    "is_crypto": True,
                    "ticker": "BTC",
                    "matched_tickers": ["BTC"],
                    "keywords": ["whale transfer"],
                    "sentiment": "neutral",  # Direction unknown without address attribution
                    "action_type": "monitor",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "url": f"https://blockchain.info/tx/{tx_hash}",
                }
                add_alert(alert)
                logger.info("🐋 Whale detected: %s", headline)

        except Exception as e:
            logger.warning("Whale monitor error: %s", e)

        _save_seen(seen)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Whale monitor stopped")


async def run_daemon(watchlist: Optional[list[str]] = None) -> None:
    """Run all news sources concurrently."""
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    watchlist = watchlist or get_dynamic_watchlist()
    seen = _load_seen()
    stop_event = asyncio.Event()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows

    write_pid()
    logger.info("🚀 Real-time news daemon started (PID=%d)", os.getpid())
    logger.info("   Watching: %s", ", ".join(watchlist))

    tasks = [
        asyncio.create_task(alpaca_news_stream(watchlist, seen, stop_event)),
        asyncio.create_task(rss_poll_loop(watchlist, seen, stop_event, interval=60)),
        asyncio.create_task(finnhub_poll_loop(watchlist, seen, stop_event, interval=120)),
        asyncio.create_task(whale_monitor_loop(seen, stop_event, interval=120)),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        _save_seen(seen)
        DAEMON_PID_FILE.unlink(missing_ok=True)
        logger.info("News daemon shut down")


def main() -> None:
    """Entry point for standalone execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(DAEMON_LOG_FILE),
        ],
    )

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "status":
            if is_running():
                print(f"✅ News daemon running (PID={read_pid()})")
            else:
                print("❌ News daemon not running")
            return
        elif cmd == "stop":
            if stop_daemon():
                print("✅ Daemon stopped")
            else:
                print("❌ Daemon not running")
            return
        elif cmd == "alerts":
            alerts = _load_pending()
            if alerts:
                for a in alerts:
                    print(f"  [{a['urgency']}] {a.get('ticker', 'MACRO')}: {a['headline'][:80]}")
            else:
                print("  No pending alerts.")
            return

    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
