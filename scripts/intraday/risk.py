"""Intraday risk manager: position limits, daily loss cap, hard close."""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_FILE = DATA_DIR / "intraday_state.json"

# Risk parameters
MAX_POSITIONS = 5
MAX_POSITION_PCT = 10.0        # % of portfolio per trade
MAX_DAILY_LOSS_PCT = 1.0       # % of portfolio — stop trading if hit
STOP_LOSS_PCT = 2.0            # per-trade stop loss
TAKE_PROFIT_PCT = 4.0          # per-trade take profit (2:1 R/R)
HARD_CLOSE_HOUR = 12           # PT hour to start closing (12:45 PM)
HARD_CLOSE_MINUTE = 45


def _load_state() -> dict:
    """Load today's intraday state."""
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") == date.today().isoformat():
                return state
        except Exception:
            pass

    # Fresh day
    return {
        "date": date.today().isoformat(),
        "trades": [],
        "open_positions": {},
        "realized_pnl": 0.0,
        "total_trades": 0,
        "stopped_trading": False,
        "stop_reason": None,
    }


def save_state(state: dict) -> None:
    """Persist intraday state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def can_trade(state: dict, portfolio_value: float) -> tuple[bool, str]:
    """Check if we're allowed to take new trades.

    Returns:
        (allowed, reason)
    """
    if state.get("stopped_trading"):
        return False, f"Trading stopped: {state.get('stop_reason', 'daily loss limit')}"

    # Daily loss check
    max_loss = portfolio_value * MAX_DAILY_LOSS_PCT / 100
    if state["realized_pnl"] < -max_loss:
        state["stopped_trading"] = True
        state["stop_reason"] = f"Daily loss limit hit: ${state['realized_pnl']:.2f} (max -${max_loss:.2f})"
        save_state(state)
        return False, state["stop_reason"]

    # Position limit
    open_count = len(state.get("open_positions", {}))
    if open_count >= MAX_POSITIONS:
        return False, f"Max positions reached ({open_count}/{MAX_POSITIONS})"

    return True, "OK"


def size_position(ticker: str, price: float, portfolio_value: float,
                  state: dict) -> tuple[int, float, float]:
    """Calculate position size with stop-loss and take-profit.

    Returns:
        (quantity, stop_price, target_price)
    """
    max_value = portfolio_value * MAX_POSITION_PCT / 100
    qty = int(max_value / price) if price > 0 else 0

    # Ensure we don't exceed remaining daily risk budget
    max_loss = portfolio_value * MAX_DAILY_LOSS_PCT / 100
    remaining_risk = max_loss + state["realized_pnl"]  # How much more we can lose
    max_risk_per_trade = price * qty * STOP_LOSS_PCT / 100

    if max_risk_per_trade > remaining_risk and remaining_risk > 0:
        # Size down to fit remaining risk budget
        qty = int(remaining_risk / (price * STOP_LOSS_PCT / 100))

    stop_price = round(price * (1 - STOP_LOSS_PCT / 100), 2)
    target_price = round(price * (1 + TAKE_PROFIT_PCT / 100), 2)

    return max(qty, 0), stop_price, target_price


def should_hard_close() -> bool:
    """Check if it's time to force-close all positions (12:45 PM PT)."""
    from datetime import timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=-8)))  # PT
    return now.hour > HARD_CLOSE_HOUR or (now.hour == HARD_CLOSE_HOUR and now.minute >= HARD_CLOSE_MINUTE)


def record_trade(state: dict, trade: dict) -> None:
    """Record a completed trade (entry or exit)."""
    state["trades"].append({
        **trade,
        "timestamp": datetime.utcnow().isoformat(),
    })
    state["total_trades"] += 1

    if trade.get("side") == "sell" and "pnl" in trade:
        state["realized_pnl"] += trade["pnl"]

    save_state(state)


def record_open_position(state: dict, ticker: str, qty: int, entry_price: float,
                         stop_price: float, target_price: float, reason: str = "") -> None:
    """Track an open intraday position."""
    state["open_positions"][ticker] = {
        "qty": qty,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "entry_time": datetime.utcnow().isoformat(),
        "reason": reason,
    }
    save_state(state)


def close_position(state: dict, ticker: str, exit_price: float, reason: str = "") -> dict | None:
    """Close an open position and record P&L."""
    pos = state["open_positions"].pop(ticker, None)
    if not pos:
        return None

    pnl = (exit_price - pos["entry_price"]) * pos["qty"]
    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100

    trade = {
        "ticker": ticker,
        "side": "sell",
        "qty": pos["qty"],
        "entry_price": pos["entry_price"],
        "exit_price": exit_price,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "reason": reason,
        "hold_time": pos.get("entry_time", ""),
    }
    record_trade(state, trade)
    return trade


def check_stops_and_targets(state: dict, current_prices: dict[str, float]) -> list[dict]:
    """Check all open positions against stop-loss and take-profit levels.

    Returns:
        List of positions that hit stop or target.
    """
    triggered = []
    for ticker, pos in list(state["open_positions"].items()):
        price = current_prices.get(ticker)
        if price is None:
            continue

        if price <= pos["stop_price"]:
            triggered.append({"ticker": ticker, "price": price, "reason": "stop_loss",
                            "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})
        elif price >= pos["target_price"]:
            triggered.append({"ticker": ticker, "price": price, "reason": "take_profit",
                            "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})

    return triggered


def get_daily_summary(state: dict) -> dict:
    """Generate end-of-day summary."""
    trades = state.get("trades", [])
    sells = [t for t in trades if t.get("side") == "sell"]

    winners = [t for t in sells if t.get("pnl", 0) > 0]
    losers = [t for t in sells if t.get("pnl", 0) < 0]

    return {
        "date": state.get("date"),
        "total_trades": len(trades),
        "round_trips": len(sells),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(sells) * 100, 1) if sells else 0,
        "realized_pnl": round(state.get("realized_pnl", 0), 2),
        "best_trade": max(sells, key=lambda t: t.get("pnl", 0)) if sells else None,
        "worst_trade": min(sells, key=lambda t: t.get("pnl", 0)) if sells else None,
        "stopped_early": state.get("stopped_trading", False),
    }


def archive_day(state: dict) -> None:
    """Archive today's state to history."""
    history_dir = DATA_DIR / "intraday_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    archive_path = history_dir / f"{state['date']}.json"
    archive_path.write_text(json.dumps(state, indent=2, default=str))
    logger.info("Archived intraday state to %s", archive_path)
