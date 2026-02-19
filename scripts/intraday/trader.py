"""Intraday trader: execute day trades on Alpaca with full lifecycle management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from scripts.intraday.scanner import get_intraday_candidates, scan_news_catalysts
from scripts.intraday.signals import compute_intraday_signals, rank_candidates
from scripts.intraday import risk as risk_mgr

logger = logging.getLogger(__name__)


class IntradayTrader:
    """Manages the full intraday trading lifecycle.

    Flow: scan → signal → risk check → execute → monitor → close
    """

    def __init__(self):
        self.state = risk_mgr._load_state()
        self._account = None
        self._portfolio_value = 100_000

    def _get_account(self) -> dict:
        """Fetch Alpaca account info."""
        if self._account is None:
            try:
                from scripts.core.executor import get_account
                self._account = get_account()
                self._portfolio_value = self._account.get("portfolio_value", 100_000)
            except Exception as e:
                logger.warning("Could not fetch account: %s", e)
        return self._account or {}

    def _get_current_price(self, ticker: str) -> float | None:
        """Get latest price for a ticker."""
        try:
            import yfinance as yf
            data = yf.download(ticker, period="1d", interval="1m", progress=False)
            if not data.empty:
                close = data["Close"]
                if hasattr(close, 'columns'):
                    close = close[ticker] if ticker in close.columns else close.iloc[:, 0]
                return float(close.dropna().iloc[-1])
        except Exception:
            pass
        return None

    def run_scan(self) -> dict:
        """Pre-market / intraday scan for candidates.

        Returns:
            Dict with candidates, signals, and recommendations.
        """
        self._get_account()
        candidates = get_intraday_candidates(top_n=15)
        if not candidates:
            return {"candidates": [], "recommendations": []}

        # Enrich with intraday signals
        ranked = rank_candidates(candidates)

        # Filter: only strong setups
        recommendations = []
        for r in ranked:
            score = r.get("combined_score", 0)
            signal_score = r.get("signal_score", 0)

            # Need combined score > 0.3 AND directional signal
            if score < 0.3:
                continue
            if abs(signal_score) < 0.1:
                continue

            direction = r.get("trade_direction", "neutral")
            if direction == "neutral":
                continue

            # Only long for now (short selling is complex)
            if direction == "short":
                continue

            recommendations.append(r)

        return {
            "candidates": ranked,
            "recommendations": recommendations[:5],  # Max 5 recommendations
            "scan_time": datetime.utcnow().isoformat(),
        }

    def execute_trades(self, recommendations: list[dict], dry_run: bool = False) -> list[dict]:
        """Execute trades from recommendations.

        Args:
            recommendations: Ranked trade candidates.
            dry_run: If True, simulate without executing.

        Returns:
            List of executed trade details.
        """
        self._get_account()
        executed = []

        for rec in recommendations:
            ticker = rec["ticker"]

            # Risk check
            allowed, reason = risk_mgr.can_trade(self.state, self._portfolio_value)
            if not allowed:
                logger.info("Trade blocked for %s: %s", ticker, reason)
                break  # Stop trying if daily limit hit

            # Skip if already have position
            if ticker in self.state.get("open_positions", {}):
                logger.info("Already holding %s, skipping", ticker)
                continue

            # Get current price
            price = self._get_current_price(ticker)
            if price is None:
                continue

            # Size position
            qty, stop_price, target_price = risk_mgr.size_position(
                ticker, price, self._portfolio_value, self.state
            )
            if qty <= 0:
                continue

            trade_value = qty * price
            signals = rec.get("intraday_signals", {})
            gap_pct = rec.get("gap_pct", 0)

            trade_info = {
                "ticker": ticker,
                "side": "buy",
                "qty": qty,
                "price": price,
                "value": round(trade_value, 2),
                "stop_loss": stop_price,
                "take_profit": target_price,
                "combined_score": rec.get("combined_score", 0),
                "signal_score": rec.get("signal_score", 0),
                "gap_pct": gap_pct,
                "has_catalyst": rec.get("has_catalyst", False),
                "signals": signals.get("signals", []),
            }

            if dry_run:
                trade_info["status"] = "dry_run"
                executed.append(trade_info)
                continue

            # Execute on Alpaca
            try:
                from scripts.core.executor import place_order
                order = place_order(ticker, "buy", qty)
                trade_info["status"] = "executed"
                trade_info["order_id"] = order.get("id", "")

                # Record in risk state
                risk_mgr.record_open_position(
                    self.state, ticker, qty, price, stop_price, target_price,
                    reason=f"score={rec.get('combined_score', 0):.2f}, gap={gap_pct:+.1f}%"
                )
                risk_mgr.record_trade(self.state, {
                    "ticker": ticker, "side": "buy", "qty": qty,
                    "price": price, "value": trade_value,
                })

                executed.append(trade_info)
                logger.info("Bought %d %s @ $%.2f (stop=%.2f, target=%.2f)",
                           qty, ticker, price, stop_price, target_price)

            except Exception as e:
                trade_info["status"] = "error"
                trade_info["error"] = str(e)
                executed.append(trade_info)
                logger.error("Failed to buy %s: %s", ticker, e)

        return executed

    def manage_positions(self) -> dict:
        """Check open positions for stop-loss, take-profit, and hard close.

        Returns:
            Dict with actions taken.
        """
        self._get_account()
        actions = []

        # Check hard close time
        if risk_mgr.should_hard_close():
            logger.info("Hard close time — closing all positions")
            for ticker in list(self.state.get("open_positions", {}).keys()):
                result = self._close_position(ticker, "hard_close_eod")
                if result:
                    actions.append(result)

            # Archive the day
            risk_mgr.archive_day(self.state)
            return {"actions": actions, "reason": "hard_close"}

        # Check stops and targets
        open_positions = self.state.get("open_positions", {})
        if not open_positions:
            return {"actions": [], "reason": "no_positions"}

        # Get current prices
        current_prices = {}
        for ticker in open_positions:
            price = self._get_current_price(ticker)
            if price:
                current_prices[ticker] = price

        triggered = risk_mgr.check_stops_and_targets(self.state, current_prices)

        for t in triggered:
            result = self._close_position(t["ticker"], t["reason"])
            if result:
                actions.append(result)

        return {"actions": actions, "reason": "monitoring"}

    def _close_position(self, ticker: str, reason: str) -> dict | None:
        """Close a single position."""
        price = self._get_current_price(ticker)
        if price is None:
            # Try to close via Alpaca directly
            try:
                from scripts.core.executor import close_position as alpaca_close
                alpaca_close(ticker)
                return risk_mgr.close_position(self.state, ticker, 0, reason=reason + " (price unknown)")
            except Exception as e:
                logger.error("Failed to close %s: %s", ticker, e)
                return None

        # Close on Alpaca
        pos = self.state.get("open_positions", {}).get(ticker)
        if not pos:
            return None

        try:
            from scripts.core.executor import place_order
            place_order(ticker, "sell", pos["qty"])
            result = risk_mgr.close_position(self.state, ticker, price, reason=reason)
            if result:
                logger.info("Closed %s @ $%.2f, P&L: $%.2f (%s)",
                           ticker, price, result["pnl"], reason)
            return result
        except Exception as e:
            logger.error("Failed to close %s: %s", ticker, e)
            return None

    def run_cycle(self, execute: bool = False) -> dict:
        """Full intraday cycle: scan → signal → trade → manage.

        Args:
            execute: If True, actually place orders.

        Returns:
            Cycle result summary.
        """
        self._get_account()
        self.state = risk_mgr._load_state()

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "portfolio_value": self._portfolio_value,
            "open_positions": len(self.state.get("open_positions", {})),
            "realized_pnl": self.state.get("realized_pnl", 0),
        }

        # 1. Manage existing positions first (stops, targets, hard close)
        manage_result = self.manage_positions()
        result["position_actions"] = manage_result.get("actions", [])

        if manage_result.get("reason") == "hard_close":
            result["phase"] = "hard_close"
            return result

        # 2. Check if we can still trade
        allowed, reason = risk_mgr.can_trade(self.state, self._portfolio_value)
        if not allowed:
            result["phase"] = "blocked"
            result["block_reason"] = reason
            return result

        # 3. Scan for new opportunities
        scan = self.run_scan()
        result["candidates_found"] = len(scan.get("candidates", []))
        result["recommendations"] = len(scan.get("recommendations", []))

        # 4. Execute trades
        if scan.get("recommendations"):
            trades = self.execute_trades(scan["recommendations"], dry_run=not execute)
            result["trades"] = trades
            result["phase"] = "traded"
        else:
            result["trades"] = []
            result["phase"] = "no_setups"

        return result

    def get_status(self) -> dict:
        """Get current intraday status."""
        self.state = risk_mgr._load_state()
        summary = risk_mgr.get_daily_summary(self.state)

        # Get current P&L for open positions
        open_pnl = 0.0
        open_details = []
        for ticker, pos in self.state.get("open_positions", {}).items():
            price = self._get_current_price(ticker)
            if price:
                pnl = (price - pos["entry_price"]) * pos["qty"]
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
                open_pnl += pnl
                open_details.append({
                    "ticker": ticker,
                    "qty": pos["qty"],
                    "entry": pos["entry_price"],
                    "current": price,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "stop": pos["stop_price"],
                    "target": pos["target_price"],
                })

        summary["open_positions"] = open_details
        summary["unrealized_pnl"] = round(open_pnl, 2)
        summary["total_pnl"] = round(summary["realized_pnl"] + open_pnl, 2)
        return summary
