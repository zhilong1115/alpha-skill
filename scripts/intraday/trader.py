"""Intraday trader: execute day trades on Alpaca with full lifecycle management.

V2.2 changes:
- ATR-based stops/targets from signal engine
- Partial take-profit at 1R (sell half, move stop to breakeven)
- Trailing stop for remaining position after partial TP
- Per-symbol re-entry limits and consecutive loss circuit breaker
- Bid-ask spread filter before entry
- Hard close at 15:45 ET
- Staged entry retained from V2.1
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
        """Pre-market / intraday scan for candidates."""
        self._get_account()
        candidates = get_intraday_candidates(top_n=15)
        if not candidates:
            return {"candidates": [], "recommendations": []}

        ranked = rank_candidates(candidates)

        recommendations = []
        for r in ranked:
            score = r.get("combined_score", 0)
            signal_score = r.get("signal_score", 0)

            if score < 0.3 or abs(signal_score) < 0.1:
                continue

            direction = r.get("trade_direction", "neutral")
            if direction in ("neutral", "short"):
                continue

            recommendations.append(r)

        return {
            "candidates": ranked,
            "recommendations": recommendations[:5],
            "scan_time": datetime.utcnow().isoformat(),
        }

    def execute_trades(self, recommendations: list[dict], dry_run: bool = False) -> list[dict]:
        """Execute trades from recommendations.

        V2.2: ATR-based stops, spread filter, per-symbol limits.
        """
        self._get_account()
        executed = []

        for rec in recommendations:
            ticker = rec["ticker"]

            # V2.1: Check if signals have entry blocked
            signals = rec.get("intraday_signals", {})
            if signals.get("entry_blocked"):
                logger.info("Entry blocked for %s: %s", ticker, signals.get("block_reasons", []))
                continue

            # V2.2: Bid-ask spread filter
            spread_ok, spread_pct = risk_mgr.check_spread(ticker)
            if not spread_ok:
                logger.info("Spread too wide for %s: %.2f%% > %.2f%%", ticker, spread_pct, risk_mgr.MAX_SPREAD_PCT)
                continue

            # Risk check (V2.2: pass ticker for per-symbol limits)
            allowed, reason = risk_mgr.can_trade(self.state, self._portfolio_value, ticker=ticker)
            if not allowed:
                logger.info("Trade blocked for %s: %s", ticker, reason)
                if "Daily loss" in reason or "Trading stopped" in reason:
                    break  # Stop all trading
                continue  # Skip this ticker only

            # Skip if already have position
            if ticker in self.state.get("open_positions", {}):
                logger.info("Already holding %s, skipping", ticker)
                continue

            price = self._get_current_price(ticker)
            if price is None:
                continue

            # V2.2: Get ATR from signals for dynamic stops
            atr = signals.get("atr", 0.0)

            # Size position (V2.2: pass ATR)
            qty, stop_price, target_price = risk_mgr.size_position(
                ticker, price, self._portfolio_value, self.state, atr=atr
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
                "atr": atr,
                "spread_pct": spread_pct,
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

                # Record position (V2.2: include ATR)
                risk_mgr.record_open_position(
                    self.state, ticker, stage1_qty, price, stop_price, target_price,
                    reason=f"stage1, score={rec.get('combined_score', 0):.2f}, gap={gap_pct:+.1f}%",
                    atr=atr,
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
                logger.info("Stage 1: Bought %d/%d %s @ $%.2f (stop=%.2f, target=%.2f, ATR=%.2f)",
                           stage1_qty, qty, ticker, price, stop_price, target_price, atr)

            except Exception as e:
                trade_info["status"] = "error"
                trade_info["error"] = str(e)
                executed.append(trade_info)
                logger.error("Failed to buy %s: %s", ticker, e)

        return executed

    def _check_staged_adds(self, current_prices: dict[str, float]) -> list[dict]:
        """V2.1: Check if any stage-1 positions should get their stage-2 add."""
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

                    total_qty = pos["qty"] + stage2_qty
                    avg_entry = (pos["entry_price"] * pos["qty"] + price * stage2_qty) / total_qty
                    pos["qty"] = total_qty
                    pos["original_qty"] = total_qty  # V2.2: update for partial TP
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
        """V2.1: After +1.5% gain, move stop to breakeven (for non-partial-TP positions)."""
        for ticker, pos in self.state.get("open_positions", {}).items():
            if pos.get("partial_tp_done"):
                continue  # V2.2: handled by trailing stop in risk_mgr

            price = current_prices.get(ticker)
            if price is None:
                continue

            gain_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
            if gain_pct >= TRAILING_STOP_TRIGGER_PCT and pos["stop_price"] < pos["entry_price"]:
                old_stop = pos["stop_price"]
                pos["stop_price"] = pos["entry_price"]
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

    def _check_partial_tp(self, current_prices: dict[str, float]) -> list[dict]:
        """V2.2: Execute partial take-profit at 1R."""
        actions = risk_mgr.check_partial_tp(self.state, current_prices)
        executed = []

        for action in actions:
            ticker = action["ticker"]
            sell_qty = action["sell_qty"]

            try:
                from scripts.core.executor import place_order
                order = place_order(ticker, "sell", sell_qty)

                # Use actual fill price if available, fall back to snapshot
                fill_price = None
                if order and order.get("filled_avg_price"):
                    fill_price = order["filled_avg_price"]
                price = fill_price or current_prices[ticker]
                risk_mgr.apply_partial_tp(self.state, ticker, sell_qty, price)
                executed.append(action)
                logger.info("Partial TP: sold %d %s @ $%.2f (+%.1f%%)",
                           sell_qty, ticker, price, action["gain_pct"])
            except Exception as e:
                logger.error("Partial TP failed for %s: %s", ticker, e)

        return executed

    def manage_positions(self) -> dict:
        """Check open positions: stops, targets, partial TP, trailing stops, hard close.

        V2.2 additions:
        - Partial take-profit at 1R (sell half, stop to breakeven)
        - ATR trailing stop for remaining after partial TP
        """
        self._get_account()
        actions = []

        # Check hard close time (V2.2: 15:45 ET)
        if risk_mgr.should_hard_close():
            logger.info("Hard close time (15:45 ET) — closing all positions")
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

        # V2.2: Check partial take-profit (at 1R)
        partial_tps = self._check_partial_tp(current_prices)
        for pt in partial_tps:
            actions.append(pt)

        # V2.2: Update trailing stops for positions after partial TP
        risk_mgr.update_trailing_stops(self.state, current_prices)

        # V2.1: Check trailing stops (move to breakeven at +1.5% for non-partial-TP)
        self._check_trailing_stops(current_prices)

        # V2.1: Check staged adds
        staged_adds = self._check_staged_adds(current_prices)
        for add in staged_adds:
            actions.append(add)

        # V2.1: Check time-based exits
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
        """Close a single position.

        V2.3: Uses Alpaca's close_position API for hard closes (more reliable than
        place_order sell). Also verifies the position is actually closed by polling
        Alpaca, fixing a bug where local state was cleared but position persisted.
        """
        pos = self.state.get("open_positions", {}).get(ticker)
        if not pos:
            return None

        price = self._get_current_price(ticker) or 0

        try:
            # V2.3: Prefer close_position API (liquidates entire position atomically)
            # over place_order("sell", qty) which can fail if qty doesn't match Alpaca's
            from scripts.core.executor import _get_client
            client = _get_client()
            if client is not None:
                try:
                    client.close_position(ticker)
                    logger.info("Alpaca close_position API called for %s", ticker)
                except Exception as e:
                    # Fallback to sell order if close_position fails
                    logger.warning("close_position API failed for %s, trying sell order: %s", ticker, e)
                    from scripts.core.executor import place_order
                    place_order(ticker, "sell", pos["qty"])
            else:
                from scripts.core.executor import place_order
                place_order(ticker, "sell", pos["qty"])

            # V2.3: Wait briefly and verify position is actually closed
            import time as _time
            _time.sleep(1)

            # V2.4: Get actual fill price from Alpaca for accurate P&L
            actual_price = price  # fallback to snapshot
            try:
                from scripts.core.executor import get_positions
                remaining = [p for p in get_positions() if p["ticker"] == ticker and p["qty"] > 0]
                if remaining:
                    logger.warning("Position %s still open after close attempt! qty=%s. "
                                   "NOT removing from local state.", ticker, remaining[0]["qty"])
                    return None  # Don't clear local state — position still exists
            except Exception as verify_err:
                logger.warning("Could not verify close for %s: %s", ticker, verify_err)

            # Try to get the actual close price from Alpaca's recent orders
            try:
                from scripts.core.executor import _get_client
                ac = _get_client()
                if ac:
                    from alpaca.trading.requests import GetOrdersRequest
                    from alpaca.trading.enums import QueryOrderStatus
                    req = GetOrdersRequest(
                        status=QueryOrderStatus.CLOSED,
                        symbols=[ticker],
                        limit=5,
                    )
                    recent_orders = ac.get_orders(req)
                    for o in recent_orders:
                        if o.side.value == "sell" and o.status.value == "filled" and o.filled_avg_price:
                            actual_price = float(o.filled_avg_price)
                            logger.info("Using Alpaca fill price for %s: $%.2f (snapshot was $%.2f)",
                                       ticker, actual_price, price)
                            break
            except Exception as fill_err:
                logger.warning("Could not get fill price for %s, using snapshot: %s", ticker, fill_err)

            result = risk_mgr.close_position(self.state, ticker, actual_price, reason=reason)
            if result:
                logger.info("Closed %s @ $%.2f, P&L: $%.2f (%s)",
                           ticker, price, result["pnl"], reason)
            return result
        except Exception as e:
            logger.error("Failed to close %s: %s", ticker, e)
            return None

    def run_cycle(self, execute: bool = False) -> dict:
        """Full intraday cycle: scan → signal → trade → manage."""
        self._get_account()
        self.state = risk_mgr._load_state()

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "portfolio_value": self._portfolio_value,
            "open_positions": len(self.state.get("open_positions", {})),
            "realized_pnl": self.state.get("realized_pnl", 0),
        }

        # 1. Manage existing positions first
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
                    "partial_tp_done": pos.get("partial_tp_done", False),
                })

        summary["open_positions"] = open_details
        summary["unrealized_pnl"] = round(open_pnl, 2)
        summary["total_pnl"] = round(summary["realized_pnl"] + open_pnl, 2)
        summary["consecutive_losses"] = self.state.get("consecutive_losses", 0)
        summary["time_weight"] = risk_mgr.get_time_weight()
        return summary
