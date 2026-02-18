"""A/B test tracker: compares baseline vs judgment-enhanced trading in real-time.

Both strategies run on the same market data simultaneously.
Strategy A (baseline): pure quant signals → risk → execute on Alpaca
Strategy B (judgment): quant + LLM judgment → risk → virtual portfolio (paper tracking)

We use Alpaca paper trading for A, and a local JSON ledger for B.
This avoids needing two brokerage accounts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AB_STATE_PATH = PROJECT_ROOT / "data" / "ab_test.json"
AB_LOG_PATH = PROJECT_ROOT / "data" / "ab_trades.jsonl"


@dataclass
class ABState:
    """Persistent state for both strategies."""

    started_at: str = ""
    initial_capital: float = 100_000
    # Strategy A: baseline (tracked via Alpaca positions)
    a_label: str = "Baseline (quant-only)"
    a_trades: int = 0
    a_realized_pnl: float = 0.0
    # Strategy B: judgment-enhanced (virtual portfolio)
    b_label: str = "Judgment-enhanced"
    b_cash: float = 100_000
    b_positions: dict = field(default_factory=dict)  # ticker → {qty, entry_price, highest}
    b_trades: int = 0
    b_realized_pnl: float = 0.0
    # Comparison stats
    a_vetoed_count: int = 0  # trades A took that B would have vetoed
    b_boosted_count: int = 0  # trades B boosted


def load_state() -> ABState:
    """Load A/B test state from disk."""
    try:
        if AB_STATE_PATH.exists():
            data = json.loads(AB_STATE_PATH.read_text())
            return ABState(**data)
    except Exception as e:
        logger.warning("Failed to load AB state: %s", e)
    return ABState(started_at=datetime.now(timezone.utc).isoformat())


def save_state(state: ABState) -> None:
    """Persist A/B test state."""
    try:
        AB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        AB_STATE_PATH.write_text(json.dumps(asdict(state), indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to save AB state: %s", e)


def log_ab_trade(strategy: str, trade: dict) -> None:
    """Append a trade event to the A/B log."""
    try:
        AB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            **trade,
        }
        with open(AB_LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.warning("Failed to log AB trade: %s", e)


def process_trade_ideas_ab(
    baseline_ideas: list[dict],
    judgment_ideas: list[dict],
    state: ABState,
) -> dict:
    """Process trade ideas through both strategies and track divergences.

    Args:
        baseline_ideas: Ideas from quant-only (pre-judgment)
        judgment_ideas: Ideas after judgment layer applied

    Returns:
        Dict with divergence analysis.
    """
    baseline_tickers = {i["ticker"] for i in baseline_ideas}
    judgment_tickers = {i["ticker"] for i in judgment_ideas}

    vetoed = baseline_tickers - judgment_tickers
    judgment_map = {i["ticker"]: i for i in judgment_ideas}

    divergences: list[dict] = []

    for idea in baseline_ideas:
        tk = idea["ticker"]
        if tk in vetoed:
            divergences.append({
                "ticker": tk,
                "type": "vetoed_by_judgment",
                "baseline_conviction": idea["conviction"],
                "judgment_conviction": 0,
                "reason": "Judgment layer vetoed this trade",
            })
            state.a_vetoed_count += 1
        elif tk in judgment_map:
            j_idea = judgment_map[tk]
            orig = idea["conviction"]
            adj = j_idea["conviction"]
            if abs(adj - orig) > 0.03:
                divergences.append({
                    "ticker": tk,
                    "type": "boosted" if adj > orig else "reduced",
                    "baseline_conviction": orig,
                    "judgment_conviction": adj,
                    "delta": round(adj - orig, 3),
                })
                if adj > orig:
                    state.b_boosted_count += 1

    return {"divergences": divergences, "vetoed": list(vetoed)}


def execute_virtual_trade(
    state: ABState,
    ticker: str,
    side: str,
    qty: int,
    price: float,
) -> dict:
    """Execute a trade in the virtual (judgment) portfolio."""
    if side == "buy":
        cost = qty * price
        if cost > state.b_cash:
            qty = int(state.b_cash / price)
            if qty <= 0:
                return {"status": "rejected", "reason": "insufficient cash"}
            cost = qty * price

        state.b_cash -= cost
        if ticker in state.b_positions:
            pos = state.b_positions[ticker]
            total_qty = pos["qty"] + qty
            pos["entry_price"] = (pos["entry_price"] * pos["qty"] + price * qty) / total_qty
            pos["qty"] = total_qty
        else:
            state.b_positions[ticker] = {"qty": qty, "entry_price": price, "highest": price}

        state.b_trades += 1
        trade = {"ticker": ticker, "side": "buy", "qty": qty, "price": price}
        log_ab_trade("B_judgment", trade)
        return {"status": "executed", **trade}

    elif side == "sell":
        if ticker not in state.b_positions:
            return {"status": "rejected", "reason": "no position"}
        pos = state.b_positions[ticker]
        sell_qty = min(qty, pos["qty"])
        pnl = (price - pos["entry_price"]) * sell_qty
        state.b_cash += sell_qty * price
        state.b_realized_pnl += pnl

        if sell_qty >= pos["qty"]:
            del state.b_positions[ticker]
        else:
            pos["qty"] -= sell_qty

        state.b_trades += 1
        trade = {"ticker": ticker, "side": "sell", "qty": sell_qty, "price": price, "pnl": round(pnl, 2)}
        log_ab_trade("B_judgment", trade)
        return {"status": "executed", **trade}

    return {"status": "error", "reason": f"unknown side: {side}"}


def get_ab_summary(state: ABState) -> str:
    """Generate A/B test comparison summary."""
    # Get current market values for B positions
    b_market_value = state.b_cash
    b_unrealized = 0.0
    pos_lines: list[str] = []

    for ticker, pos in state.b_positions.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1d")
            if hist is not None and not hist.empty:
                current = float(hist["Close"].iloc[-1])
            else:
                current = pos["entry_price"]
        except Exception:
            current = pos["entry_price"]

        mv = pos["qty"] * current
        pnl = (current - pos["entry_price"]) * pos["qty"]
        b_market_value += mv
        b_unrealized += pnl
        icon = "🟢" if pnl >= 0 else "🔴"
        pos_lines.append(f"    {icon} {ticker}: {pos['qty']} @ ${current:.2f} (P&L: ${pnl:+,.2f})")

    b_total_return = (b_market_value / state.initial_capital - 1) * 100

    lines = [
        "=" * 60,
        "  📊 A/B TEST: Baseline vs Judgment-Enhanced",
        f"  Running since: {state.started_at[:10]}",
        "=" * 60,
        "",
        f"  {'Metric':<30} {'A: Baseline':>12} {'B: Judgment':>12}",
        "-" * 60,
        f"  {'Trades executed':<30} {state.a_trades:>12} {state.b_trades:>12}",
        f"  {'Realized P&L':<30} ${state.a_realized_pnl:>10,.2f} ${state.b_realized_pnl:>10,.2f}",
        f"  {'Unrealized P&L (B)':<30} {'(see Alpaca)':>12} ${b_unrealized:>10,.2f}",
        f"  {'Portfolio Value (B)':<30} {'(see Alpaca)':>12} ${b_market_value:>10,.2f}",
        f"  {'Return (B)':<30} {'(see Alpaca)':>12} {b_total_return:>+11.2f}%",
        "-" * 60,
        f"  Trades A took that B vetoed: {state.a_vetoed_count}",
        f"  Trades B boosted:            {state.b_boosted_count}",
    ]

    if pos_lines:
        lines.append("\n  📋 Strategy B Positions:")
        lines.extend(pos_lines)

    lines.append("=" * 60)
    return "\n".join(lines)
