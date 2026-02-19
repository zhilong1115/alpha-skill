"""Intraday trader: execute day trades on Alpaca with full lifecycle management.

V2.1 changes:
- Staged entry: buy 1/2 position, add other 1/2 only if +0.5% in our favor
- Trailing stop: after +1.5% gain, move stop to breakeven
- Time-based exit: close positions open >2 hours with <1% gain
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from scripts.intraday.scanner import get_intraday_candidates, scan_news_catalysts
from scripts.intraday.signals import compute_intraday_signals, rank_candidates
from scripts.intraday import risk as risk_mgr

logger = logging.getLogger(__name__)

# V2.1: Staged entry and trailing stop parameters
STAGED_ENTRY_CONFIRM_PCT = 0.5    # Add 2nd half only if +0.5% in our favor
TRAILING_STOP_TRIGGER_PCT = 1.5   # Move stop to breakeven after +1.5%
TIME_EXIT_HOURS = 2               # Close position if open > 2 hours
TIME_EXIT_MIN_GAIN_PCT = 1.0      # ...and gain < 1%


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

        V2.1: Staged entry — buy 1/2 position first. The other 1/2 is added
        only when manage_positions detects +0.5% gain (see _check_staged_adds).

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

            # V2.1: Check if signals have entry blocked (chasing, no confirmation)
            signals = rec.get("intraday_signals", {})
            if signals.get("entry_blocked"):
                logger.info("Entry blocked for %s: %s", ticker, signals.get("block_reasons", []))
                continue

            # Risk check
            allowed, reason = risk_mgr.can_trade(self.state, self._portfolio_value)
            if not allowed:
                logger.info("Trade blocked for %s: %s", ticker, reason)
                break

            # Skip if already have position
            if ticker in self.state.get("open_positions", {}):
                logger.info("Already holding %s, skipping", ticker)
                continue

            price = self._get_current_price(ticker)
            if price is None:
                continue

            # Size position
            qty, stop_price, target_price = risk_mgr.size_position(
                ticker, price, self._portfolio_value, self.state
            )
            if qty <= 0:
                continue

            # V2.1: Staged entry — buy only 1/2 now
            stage1_qty = max(qty // 2, 1)
            trade_value = stage1_qty * price
            gap_pct = rec.get("gap_pct", 0)

            trade_info = {
                "ticker": ticker,
                "side": "buy",
                "qty": stage1_qty,
                "full_qty": qty,
                "staged": True,
                "stage": 1,
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

            try:
                from scripts.core.executor import place_order
                order = place_order(ticker, "buy", stage1_qty)
                trade_info["status"] = "executed"
                trade_info["order_id"] = order.get("id", "")

                # Record staged position
                risk_mgr.record_open_position(
                    self.state, ticker, stage1_qty, price, stop_price, target_price,
                    reason=f"stage1, score={rec.get('combined_score', 0):.2f}, gap={gap_pct:+.1f}%"
                )
                # V2.1: Track staged entry metadata
                self.state["open_positions"][ticker]["staged"] = True
                self.state["open_positions"][ticker]["stage"] = 1
                self.state["open_positions"][ticker]["full_qty"] = qty
                self.state["open_positions"][ticker]["stage2_qty"] = qty - stage1_qty
                risk_mgr.save_state(self.state)

                risk_mgr.record_trade(self.state, {
                    "ticker": ticker, "side": "buy", "qty": stage1_qty,
                    "price": price, "value": trade_value,
                })

                executed.append(trade_info)
                logger.info("Stage 1: Bought %d/%d %s @ $%.2f (stop=%.2f, target=%.2f)",
                           stage1_qty, qty, ticker, price, stop_price, target_price)

            except Exception as e:
                trade_info["status"] = "error"
                trade_info["error"] = str(e)
                executed.append(trade_info)
                logger.error("Failed to buy %s: %s", ticker, e)

        return executed

    def _check_staged_adds(self, current_prices: dict[str, float]) -> list[dict]:
        """V2.1: Check if any stage-1 positions should get their stage-2 add.

        Add 2nd half only if position is +0.5% in our favor.
        """
        adds = []
        for ticker, pos in list(self.state.get("open_positions", {}).items()):
            if not pos.get("staged") or pos.get("stage", 1) >= 2:
                continue

            price = current_prices.get(ticker)
            if price is None:
                continue

            gain_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
            if gain_pct >= STAGED_ENTRY_CONFIRM_PCT:
                stage2_qty = pos.get("stage2_qty", 0)
                if stage2_qty <= 0:
                    continue

                try:
                    from scripts.core.executor import place_order
                    place_order(ticker, "buy", stage2_qty)

                    # Update position
                    total_qty = pos["qty"] + stage2_qty
                    # Weighted average entry price
                    avg_entry = (pos["entry_price"] * pos["qty"] + price * stage2_qty) / total_qty
                    pos["qty"] = total_qty
                    pos["entry_price"] = round(avg_entry, 2)
                    pos["stage"] = 2
                    pos["staged"] = False
                    risk_mgr.save_state(self.state)

                    adds.append({
                        "ticker": ticker, "action": "stage2_add",
                        "qty_added": stage2_qty, "total_qty": total_qty,
                        "price": price, "gain_pct": round(gain_pct, 2),
                    })
                    logger.info("Stage 2: Added %d %s @ $%.2f (total %d, gain +%.1f%%)",
                               stage2_qty, ticker, price, total_qty, gain_pct)
                except Exception as e:
                    logger.error("Stage 2 add failed for %s: %s", ticker, e)

        return adds

    def _check_trailing_stops(self, current_prices: dict[str, float]) -> None:
        """V2.1: After +1.5% gain, move stop to breakeven."""
        for ticker, pos in self.state.get("open_positions", {}).items():
            price = current_prices.get(ticker)
            if price is None:
                continue

            gain_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
            if gain_pct >= TRAILING_STOP_TRIGGER_PCT and pos["stop_price"] < pos["entry_price"]:
                old_stop = pos["stop_price"]
                pos["stop_price"] = pos["entry_price"]  # Move to breakeven
                logger.info("Trailing stop: %s moved stop $%.2f → $%.2f (breakeven, gain +%.1f%%)",
                           ticker, old_stop, pos["entry_price"], gain_pct)
                risk_mgr.save_state(self.state)

    def _check_time_exits(self, current_prices: dict[str, float]) -> list[dict]:
        """V2.1: Close positions open >2 hours with <1% gain."""
        exits = []
        now = datetime.now(timezone.utc)

        for ticker, pos in list(self.state.get("open_positions", {}).items()):
            entry_time_str = pos.get("entry_time", "")
            if not entry_time_str:
                continue

            try:
                entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            hours_held = (now - entry_time).total_seconds() / 3600
            if hours_held < TIME_EXIT_HOURS:
                continue

            price = current_prices.get(ticker)
            if price is None:
                continue

            gain_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
            if gain_pct < TIME_EXIT_MIN_GAIN_PCT:
                exits.append({"ticker": ticker, "reason": f"time_exit ({hours_held:.1f}h, {gain_pct:+.1f}%)"})
                logger.info("Time exit: %s held %.1fh with only %+.1f%% gain", ticker, hours_held, gain_pct)

        return exits

    def manage_positions(self) -> dict:
        """Check open positions for stop-loss, take-profit, staged adds, trailing stops, and hard close.

        V2.1 additions:
        - Staged entry adds (stage 2 at +0.5%)
        - Trailing stop to breakeven at +1.5%
        - Time-based exit: >2 hours with <1% gain

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

            risk_mgr.archive_day(self.state)
            return {"actions": actions, "reason": "hard_close"}

        open_positions = self.state.get("open_positions", {})
        if not open_positions:
            return {"actions": [], "reason": "no_positions"}

        # Get current prices
        current_prices = {}
        for ticker in open_positions:
            price = self._get_current_price(ticker)
            if price:
                current_prices[ticker] = price

        # V2.1: Check trailing stops (move to breakeven at +1.5%)
        self._check_trailing_stops(current_prices)

        # V2.1: Check staged adds (add 2nd half at +0.5%)
        staged_adds = self._check_staged_adds(current_prices)
        for add in staged_adds:
            actions.append(add)

        # V2.1: Check time-based exits (>2h with <1% gain)
        time_exits = self._check_time_exits(current_prices)
        for te in time_exits:
            result = self._close_position(te["ticker"], te["reason"])
            if result:
                actions.append(result)

        # Check stops and targets
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
