"""Real-time news and event monitoring for trading signals."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NEWS_STATE_PATH = PROJECT_ROOT / "data" / "news_state.json"
SENTIMENT_STATE_PATH = PROJECT_ROOT / "data" / "sentiment_state.json"

CRITICAL_KEYWORDS = [
    "earnings", "fda", "acquire", "merger", "bankrupt", "recall",
    "fraud", "sec investigation", "guidance", "restructur",
]
HIGH_KEYWORDS = [
    "upgrade", "downgrade", "target price", "beat", "miss",
    "revenue", "contract", "partnership", "dividend",
]
MEDIUM_KEYWORDS = [
    "launch", "ceo", "cfo", "lawsuit", "patent", "expansion",
    "hire", "layoff", "restructure",
]

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]


class NewsMonitor:
    """Monitors news sources for breaking events that could trigger trades."""

    def __init__(self, watchlist: Optional[list[str]] = None) -> None:
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self._news_state = self._load_state(NEWS_STATE_PATH)
        self._sentiment_state = self._load_state(SENTIMENT_STATE_PATH)

    @staticmethod
    def _load_state(path: Path) -> dict:
        """Load persisted state from JSON file."""
        try:
            if path.exists():
                return json.loads(path.read_text()) or {}
        except Exception:
            pass
        return {}

    @staticmethod
    def _save_state(path: Path, data: dict) -> None:
        """Save state to JSON file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.error("Failed to save state to %s: %s", path, e)

    # ------------------------------------------------------------------
    # Breaking news
    # ------------------------------------------------------------------

    def check_breaking_news(self, tickers: Optional[list[str]] = None) -> list[dict]:
        """Check for new/breaking news on watched tickers.

        Returns:
            List of news_event dicts with ticker, headline, source,
            timestamp, sentiment, urgency, action_needed.
        """
        tickers = tickers or self.watchlist
        events: list[dict] = []

        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                news_items = t.news or []
            except Exception as e:
                logger.warning("Failed to fetch news for %s: %s", ticker, e)
                continue

            last_check = self._news_state.get(ticker, {}).get("last_id", "")

            for item in news_items[:10]:
                # Handle both old and new yfinance news format
                content = item.get("content", item)
                item_id = item.get("id", item.get("uuid", content.get("id", "")))
                if item_id == last_check:
                    break

                title = content.get("title", item.get("title", ""))
                provider = content.get("provider", {})
                publisher = provider.get("displayName", "") if isinstance(provider, dict) else item.get("publisher", "")
                pub_time = content.get("pubDate", item.get("providerPublishTime", ""))

                urgency = self._classify_urgency(title)
                sentiment = self._quick_sentiment(title)

                events.append({
                    "ticker": ticker,
                    "headline": title,
                    "source": publisher,
                    "timestamp": pub_time,
                    "sentiment": sentiment,
                    "urgency": urgency,
                    "action_needed": urgency in ("critical", "high"),
                })

            # Update state
            if news_items:
                first_id = news_items[0].get("id", news_items[0].get("uuid", ""))
                self._news_state[ticker] = {
                    "last_id": first_id,
                    "last_check": datetime.now(timezone.utc).isoformat(),
                }

        self._save_state(NEWS_STATE_PATH, self._news_state)
        return events

    # ------------------------------------------------------------------
    # Reddit sentiment shifts
    # ------------------------------------------------------------------

    def check_reddit_sentiment_shift(
        self, tickers: Optional[list[str]] = None
    ) -> list[dict]:
        """Detect sudden sentiment shifts on Reddit.

        Returns:
            List of shift_event dicts.
        """
        tickers = tickers or self.watchlist
        shifts: list[dict] = []

        try:
            from scripts.analysis.sentiment_scraper import scrape_reddit_mentions
        except ImportError:
            logger.warning("sentiment_scraper not available")
            return shifts

        try:
            df = scrape_reddit_mentions(tickers)
        except Exception as e:
            logger.warning("Reddit scrape failed: %s", e)
            return shifts

        for _, row in df.iterrows():
            ticker = row["ticker"]
            new_sentiment = row["avg_sentiment"]
            new_mentions = row["mentions"]

            old = self._sentiment_state.get(ticker, {})
            old_sentiment = old.get("avg_sentiment", 0.0)
            old_mentions = old.get("mentions", 0)

            shift_mag = abs(new_sentiment - old_sentiment)
            mentions_ratio = (new_mentions / old_mentions) if old_mentions > 0 else 0

            if shift_mag > 0.3 or mentions_ratio > 2.0:
                shifts.append({
                    "ticker": ticker,
                    "old_sentiment": round(old_sentiment, 4),
                    "new_sentiment": round(new_sentiment, 4),
                    "shift_magnitude": round(shift_mag, 4),
                    "mentions_change": f"{old_mentions} → {new_mentions}",
                    "subreddit": "wallstreetbets/stocks",
                })

            # Update state
            self._sentiment_state[ticker] = {
                "avg_sentiment": new_sentiment,
                "mentions": new_mentions,
                "last_check": datetime.now(timezone.utc).isoformat(),
            }

        self._save_state(SENTIMENT_STATE_PATH, self._sentiment_state)
        return shifts

    # ------------------------------------------------------------------
    # Unusual volume
    # ------------------------------------------------------------------

    def check_unusual_volume(self, tickers: Optional[list[str]] = None) -> list[dict]:
        """Detect unusual intraday volume spikes.

        Returns:
            List of volume_event dicts.
        """
        tickers = tickers or self.watchlist
        events: list[dict] = []

        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                # Today's data
                hist_1d = t.history(period="1d", interval="5m")
                # 20-day avg volume
                hist_20d = t.history(period="1mo")

                if hist_1d is None or hist_1d.empty or hist_20d is None or hist_20d.empty:
                    continue

                current_volume = int(hist_1d["Volume"].sum())
                avg_daily_volume = int(hist_20d["Volume"].mean()) if len(hist_20d) > 0 else 1

                if avg_daily_volume <= 0:
                    continue

                ratio = current_volume / avg_daily_volume

                # Price change
                if len(hist_1d) >= 2:
                    open_price = float(hist_1d["Open"].iloc[0])
                    last_price = float(hist_1d["Close"].iloc[-1])
                    price_change_pct = ((last_price - open_price) / open_price * 100) if open_price > 0 else 0
                else:
                    price_change_pct = 0.0

                if ratio > 2.0:
                    events.append({
                        "ticker": ticker,
                        "current_volume": current_volume,
                        "avg_volume": avg_daily_volume,
                        "ratio": round(ratio, 2),
                        "price_change_pct": round(price_change_pct, 2),
                    })
            except Exception as e:
                logger.warning("Volume check failed for %s: %s", ticker, e)

        return events

    # ------------------------------------------------------------------
    # Event → signal conversion
    # ------------------------------------------------------------------

    def generate_event_signals(self, events: list[dict]) -> list[dict]:
        """Convert detected events into actionable trade signals.

        Returns:
            List of signal dicts with ticker, action, conviction, reason.
        """
        signals: list[dict] = []
        for event in events:
            ticker = event.get("ticker", "")
            urgency = event.get("urgency", "low")
            sentiment = event.get("sentiment", 0)

            if urgency == "critical":
                conviction = 0.5 * (1 if sentiment > 0 else -1)
                action = "buy" if sentiment > 0 else "sell"
            elif urgency == "high":
                conviction = 0.3 * (1 if sentiment > 0 else -1)
                action = "buy" if sentiment > 0 else "sell"
            else:
                continue  # low/medium → no immediate action

            signals.append({
                "ticker": ticker,
                "action": action,
                "conviction": round(abs(conviction), 3),
                "reason": f"{urgency} news: {event.get('headline', '')[:60]}",
            })
        return signals

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def check_realtime_alerts(self) -> list[dict]:
        """Check for pending alerts from the real-time news daemon.

        Returns:
            List of alert dicts from the daemon's pending queue.
        """
        try:
            from scripts.monitoring.realtime_news import pop_pending_alerts
            return pop_pending_alerts()
        except Exception as e:
            logger.warning("Failed to check realtime alerts: %s", e)
            return []

    def get_monitoring_summary(self) -> str:
        """Human-readable summary of all monitored events."""
        # Check real-time daemon alerts first
        rt_alerts = self.check_realtime_alerts()
        news = self.check_breaking_news()
        volume = self.check_unusual_volume()

        lines = ["📰 News Monitor Summary", "=" * 40]

        if rt_alerts:
            lines.append(f"\n⚡ REAL-TIME ALERTS ({len(rt_alerts)} items):")
            for a in rt_alerts:
                icon = "🔴" if a["urgency"] == "critical" else "🟠"
                lines.append(f"  {icon} [{a.get('ticker', 'MACRO')}] {a['headline'][:70]}")
                lines.append(f"      src={a['source']}  keywords={a.get('keywords', [])}")

        if news:
            lines.append(f"\n🗞 Breaking News ({len(news)} items):")
            for ev in news[:10]:
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(ev["urgency"], "⚪")
                lines.append(f"  {icon} [{ev['ticker']}] {ev['headline'][:70]}")
        else:
            lines.append("\n  No breaking news.")

        if volume:
            lines.append(f"\n📊 Unusual Volume ({len(volume)} tickers):")
            for ev in volume:
                lines.append(
                    f"  ⚡ {ev['ticker']}: {ev['ratio']:.1f}x avg volume, "
                    f"price {ev['price_change_pct']:+.1f}%"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_urgency(headline: str) -> str:
        """Classify headline urgency level."""
        lower = headline.lower()
        for kw in CRITICAL_KEYWORDS:
            if kw in lower:
                return "critical"
        for kw in HIGH_KEYWORDS:
            if kw in lower:
                return "high"
        for kw in MEDIUM_KEYWORDS:
            if kw in lower:
                return "medium"
        return "low"

    @staticmethod
    def _quick_sentiment(headline: str) -> float:
        """Quick keyword-based sentiment score for a headline."""
        positive = ["surge", "beat", "upgrade", "record", "growth", "rally", "strong", "gain", "soar", "boost", "buy"]
        negative = ["crash", "miss", "downgrade", "decline", "weak", "cut", "loss", "drop", "slump", "sell", "fraud", "bankrupt"]

        lower = headline.lower()
        pos = sum(1 for w in positive if w in lower)
        neg = sum(1 for w in negative if w in lower)
        total = pos + neg
        if total == 0:
            return 0.0
        return round((pos - neg) / total, 3)
