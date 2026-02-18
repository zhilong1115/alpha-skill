"""Automated trading engine: scan → decide → execute → report."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from scripts.core.orchestrator import TradingOrchestrator
from scripts.core.executor import get_account, get_positions, place_order
from scripts.core.risk_manager import approve_trade, compute_trailing_stop
from scripts.monitoring.alert_system import (
    check_drawdown_alerts,
    check_stop_loss_alerts,
    format_alert,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AutoTrader:
    """Automated trading engine that makes real trade decisions.

    Coordinates scanning, risk checks, and order execution in a single
    trading cycle.  Default mode is dry-run — live execution requires
    explicit opt-in.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.orchestrator = TradingOrchestrator(config_path)
        self.config = self.orchestrator.config
        self.conviction_threshold = 0.3
        self.actions_log: list[dict] = []
        self._trade_log_path = PROJECT_ROOT / "data" / "trades"
        self._trade_log_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    def run_trading_cycle(
        self,
        tickers: Optional[list[str]] = None,
        universe: str = "full",
    ) -> dict:
        """Full trading cycle: scan → decide → execute → report.

        Args:
            tickers: Explicit ticker list. If None, uses *universe*.
            universe: One of "watchlist", "sp500", "full". Only used when
                *tickers* is None.

        Returns:
            dict with keys: actions_taken, signals_count, alerts, positions_updated, ideas
        """
        if tickers is None:
            if universe == "full":
                from scripts.utils.universe import get_full_universe
                u = get_full_universe()
                tickers = u["all_unique"]
                logger.info("Full universe: %d tickers", len(tickers))
            elif universe == "sp500":
                from scripts.utils.universe import get_sp500_tickers
                tickers = get_sp500_tickers()
                logger.info("S&P 500 universe: %d tickers", len(tickers))
            # else watchlist — leave tickers=None for orchestrator default
        self.actions_log = []
        result: dict = {
            "timestamp": datetime.utcnow().isoformat(),
            "actions_taken": [],
            "signals_count": 0,
            "alerts": [],
            "positions_updated": [],
            "ideas": [],
            "errors": [],
        }

        # 1. Get current positions
        try:
            positions = get_positions()
            result["positions_updated"] = positions
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            positions = []
            result["errors"].append(f"Positions fetch failed: {e}")

        # 2. Check stop-losses → execute exits
        try:
            stops = self.check_and_execute_stops(positions)
            if stops:
                result["actions_taken"].extend(stops)
        except Exception as e:
            logger.error("Stop-loss check failed: %s", e)
            result["errors"].append(f"Stop-loss check failed: {e}")

        # 3. Run full signal scan
        try:
            scan = self.orchestrator.run_scan(tickers)
            result["signals_count"] = len(scan.get("signals", []))
            result["alerts"] = scan.get("alerts", [])
        except Exception as e:
            logger.error("Scan failed: %s", e)
            result["errors"].append(f"Scan failed: {e}")
            return result

        # 4. Generate trade ideas
        try:
            ideas = self.orchestrator.generate_trade_ideas(self.conviction_threshold)
            result["ideas"] = ideas
        except Exception as e:
            logger.error("Idea generation failed: %s", e)
            result["errors"].append(f"Idea generation failed: {e}")
            return result

        # 4.5. LLM Judgment Layer — subjective review of trade candidates
        try:
            from scripts.analysis.llm_judge import review_trade_ideas
            regime = scan.get("regime", {}).get("regime", "SIDEWAYS") if isinstance(scan.get("regime"), dict) else "SIDEWAYS"
            original_count = len(ideas)
            ideas = review_trade_ideas(ideas, regime=regime)
            result["ideas"] = ideas
            vetoed = original_count - len(ideas)
            if vetoed > 0:
                logger.info("LLM judgment vetoed %d/%d ideas", vetoed, original_count)
            result["judgment_applied"] = True
            result["ideas_vetoed"] = vetoed
        except Exception as e:
            logger.warning("LLM judgment layer failed (proceeding without): %s", e)
            result["judgment_applied"] = False

        # 5. Evaluate and trade
        try:
            executed = self.evaluate_and_trade(ideas)
            result["actions_taken"].extend(executed)
        except Exception as e:
            logger.error("Trade execution failed: %s", e)
            result["errors"].append(f"Trade execution failed: {e}")

        # 6. Log
        self._log_cycle(result)

        return result

    # ------------------------------------------------------------------
    # Stop-loss management
    # ------------------------------------------------------------------

    def check_and_execute_stops(
        self, positions: Optional[list[dict]] = None
    ) -> list[dict]:
        """Check all positions against trailing stops, execute exits if triggered.

        Returns:
            List of executed stop-loss dicts.
        """
        if positions is None:
            positions = get_positions()

        executed: list[dict] = []
        for pos in positions:
            ticker = pos.get("ticker", "")
            entry = float(pos.get("avg_entry_price", 0))
            current = float(pos.get("current_price", 0))
            qty = int(float(pos.get("qty", 0)))

            if entry <= 0 or current <= 0 or qty <= 0:
                continue

            # Use current price as proxy for highest (Alpaca doesn't track this)
            highest = max(entry, current)
            stop_price = compute_trailing_stop(entry, highest)

            if current <= stop_price:
                logger.warning(
                    "STOP triggered for %s: price $%.2f <= stop $%.2f",
                    ticker, current, stop_price,
                )
                try:
                    order = place_order(ticker, "sell", qty, "market")
                    action = {
                        "type": "stop_loss",
                        "ticker": ticker,
                        "qty": qty,
                        "price": current,
                        "stop_price": stop_price,
                        "order": order,
                    }
                    executed.append(action)
                except Exception as e:
                    logger.error("Failed to execute stop for %s: %s", ticker, e)

        return executed

    # ------------------------------------------------------------------
    # Trade evaluation & execution
    # ------------------------------------------------------------------

    def evaluate_and_trade(self, ideas: list[dict]) -> list[dict]:
        """Evaluate trade ideas and execute approved ones.

        Args:
            ideas: list of dicts with ticker, conviction, side, qty, price, reason.

        Returns:
            List of executed trade dicts.
        """
        executed: list[dict] = []
        account = get_account()
        if not account:
            logger.warning("No Alpaca account — skipping execution.")
            return executed

        positions = get_positions()
        portfolio_value = account["portfolio_value"]

        for idea in ideas:
            conviction = idea.get("conviction", 0)
            if conviction < self.conviction_threshold:
                continue

            ticker = idea["ticker"]
            side = idea.get("side", "buy")
            price = idea.get("price", 0)
            qty = idea.get("qty", 0)

            if price <= 0 or qty <= 0:
                continue

            # Risk check
            approved, sized_qty, reason = approve_trade(
                ticker, side, qty, price, portfolio_value, positions
            )

            if not approved:
                logger.info("Trade rejected for %s: %s", ticker, reason)
                continue

            try:
                order = place_order(ticker, side, sized_qty, "market")
                trade_record = {
                    "type": "trade",
                    "ticker": ticker,
                    "side": side,
                    "qty": sized_qty,
                    "price": price,
                    "conviction": conviction,
                    "reason": reason,
                    "order": order,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                executed.append(trade_record)
                logger.info(
                    "Executed %s %d %s @ $%.2f (conviction=%.3f)",
                    side, sized_qty, ticker, price, conviction,
                )
            except Exception as e:
                logger.error("Order failed for %s: %s", ticker, e)

        return executed

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def monitor_positions(self) -> dict:
        """Quick position health check without full scan.

        Returns:
            Dict with positions, alerts, total_pnl, stop_status.
        """
        result: dict = {
            "timestamp": datetime.utcnow().isoformat(),
            "positions": [],
            "alerts": [],
            "total_pnl": 0.0,
            "stop_status": [],
        }

        try:
            positions = get_positions()
            result["positions"] = positions
        except Exception as e:
            result["error"] = str(e)
            return result

        # P&L
        result["total_pnl"] = sum(
            float(p.get("unrealized_pl", 0)) for p in positions
        )

        # Stop-loss status
        for pos in positions:
            entry = float(pos.get("avg_entry_price", 0))
            current = float(pos.get("current_price", 0))
            if entry <= 0:
                continue
            highest = max(entry, current)
            stop_price = compute_trailing_stop(entry, highest)
            distance_pct = ((current - stop_price) / current * 100) if current > 0 else 0
            result["stop_status"].append({
                "ticker": pos["ticker"],
                "current": current,
                "stop": round(stop_price, 2),
                "distance_pct": round(distance_pct, 1),
            })

        # Alerts
        account = get_account()
        if account:
            pv = account["portfolio_value"]
            daily_pnl = result["total_pnl"]
            result["alerts"].extend(check_drawdown_alerts(positions, pv, daily_pnl))

        result["alerts"].extend(check_stop_loss_alerts(positions))

        return result

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def get_trade_summary(self) -> str:
        """Generate human-readable summary of recent actions."""
        if not self.actions_log:
            return "No actions taken in current session."

        lines = ["📊 Trade Summary", "=" * 40]
        for action in self.actions_log:
            atype = action.get("type", "unknown")
            ticker = action.get("ticker", "?")
            if atype == "stop_loss":
                lines.append(
                    f"  🛑 STOP {ticker}: sold {action.get('qty', 0)} @ ${action.get('price', 0):.2f}"
                )
            elif atype == "trade":
                side = action.get("side", "buy").upper()
                lines.append(
                    f"  {'🟢' if side == 'BUY' else '🔴'} {side} {action.get('qty', 0)} {ticker} "
                    f"@ ${action.get('price', 0):.2f} (conviction={action.get('conviction', 0):.3f})"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_cycle(self, result: dict) -> None:
        """Persist cycle results to disk."""
        self.actions_log.extend(result.get("actions_taken", []))
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        log_file = self._trade_log_path / f"cycle_{ts}.json"
        try:
            # Convert non-serializable items
            serializable = json.loads(json.dumps(result, default=str))
            log_file.write_text(json.dumps(serializable, indent=2))
        except Exception as e:
            logger.error("Failed to log cycle: %s", e)
