"""Trading orchestrator: end-to-end integration of all system components."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from scripts.core.data_pipeline import get_price_data
from scripts.core.signal_engine import compute_signals
from scripts.core.conviction import compute_conviction
from scripts.core.risk_manager import approve_trade
from scripts.analysis.regime_detector import detect_regime_detailed, get_adaptive_weights
from scripts.monitoring.alert_system import check_signal_alerts


class TradingOrchestrator:
    """Main orchestration class tying together all trading system components.

    Args:
        config_path: Path to config.yaml.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.config = self._load_config(config_path)

    @staticmethod
    def _load_config(config_path: str) -> dict:
        """Load configuration from YAML file."""
        p = Path(config_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / config_path
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _get_default_tickers(self) -> list[str]:
        """Return default ticker universe from config."""
        custom = self.config.get("universe", {}).get("custom_tickers", [])
        if custom:
            return [str(t).upper() for t in custom]
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

    def run_scan(self, tickers: Optional[list[str]] = None) -> dict:
        """Run a full market scan: signals, conviction, regime, alerts.

        Args:
            tickers: Optional list of tickers. Uses config universe if None.

        Returns:
            Dict with keys: regime, signals, convictions, alerts, tickers.
        """
        tickers = tickers or self._get_default_tickers()

        # 1. Detect regime
        regime_info = detect_regime_detailed()
        regime = regime_info["regime"]
        weights = get_adaptive_weights(regime)

        # 2. Fetch data and compute signals
        all_signals: list[pd.DataFrame] = []
        for ticker in tickers:
            try:
                df = get_price_data(ticker, period="1y")
                sigs = compute_signals(ticker, df)
                if not sigs.empty:
                    all_signals.append(sigs)
            except Exception:
                continue

        # 3. Strategy signals (best-effort)
        try:
            from scripts.strategies.momentum_factor import generate_momentum_signals
            mom = generate_momentum_signals(tickers)
            if not mom.empty:
                all_signals.append(mom)
        except Exception:
            pass

        try:
            from scripts.strategies.mean_reversion import generate_reversion_signals
            rev = generate_reversion_signals(tickers)
            if not rev.empty:
                all_signals.append(rev)
        except Exception:
            pass

        if not all_signals:
            return {"regime": regime_info, "signals": pd.DataFrame(), "convictions": pd.DataFrame(), "alerts": [], "tickers": tickers}

        combined = pd.concat(all_signals, ignore_index=True)

        # 4. Conviction
        convictions = compute_conviction(combined, weights)

        # 5. Alerts
        alerts = check_signal_alerts(combined)

        return {
            "regime": regime_info,
            "signals": combined,
            "convictions": convictions,
            "alerts": alerts,
            "tickers": tickers,
        }

    def run_analysis(self, ticker: str) -> dict:
        """Run deep analysis on a single ticker.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict with signals, news, sentiment, debate, risk_check.
        """
        result: dict = {"ticker": ticker}

        try:
            df = get_price_data(ticker, period="1y")
            signals = compute_signals(ticker, df)
            result["signals"] = signals
            result["price"] = float(df["Close"].iloc[-1])
        except Exception as e:
            result["signals"] = pd.DataFrame()
            result["error"] = str(e)
            return result

        # News
        try:
            from scripts.analysis.news_analyzer import get_recent_news, score_news_sentiment
            news = get_recent_news(ticker)
            result["news"] = news
            result["news_sentiment"] = score_news_sentiment(news)
        except Exception:
            result["news"] = []
            result["news_sentiment"] = 0.0

        # Debate
        try:
            from scripts.analysis.debate import create_bull_case, create_bear_case, resolve_debate
            bull = create_bull_case(ticker, signals, result.get("news", []), result.get("news_sentiment", 0.0))
            bear = create_bear_case(ticker, signals, result.get("news", []), result.get("news_sentiment", 0.0))
            verdict = resolve_debate(bull, bear)
            result["debate"] = {"bull": bull, "bear": bear, "verdict": verdict}
        except Exception:
            result["debate"] = {}

        return result

    def generate_trade_ideas(self, min_conviction: float = 0.3) -> list[dict]:
        """Generate actionable trade ideas from a market scan.

        Args:
            min_conviction: Minimum conviction score to include.

        Returns:
            List of trade recommendation dicts.
        """
        scan = self.run_scan()
        convictions = scan.get("convictions", pd.DataFrame())
        if convictions.empty:
            return []

        ideas: list[dict] = []
        for _, row in convictions.iterrows():
            score = row["conviction_score"]
            if score < min_conviction:
                continue

            ticker = row["ticker"]
            try:
                df = get_price_data(ticker, period="5d")
                price = float(df["Close"].iloc[-1])
            except Exception:
                continue

            # Target: 5% of $100k portfolio
            qty = int(5000 / price) if price > 0 else 0
            if qty <= 0:
                continue

            approved, sized_qty, reason = approve_trade(
                ticker, "buy", qty, price, 100_000, []
            )
            if not approved:
                continue

            ideas.append({
                "ticker": ticker,
                "side": "buy",
                "qty": sized_qty,
                "price": price,
                "conviction": round(score, 3),
                "regime": scan["regime"]["regime"],
                "reason": reason,
            })

        return ideas
