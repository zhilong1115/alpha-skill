"""Risk manager: position sizing, limits, and trade approval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yaml
from pathlib import Path


def _load_risk_config() -> dict:
    """Load risk parameters from config.yaml."""
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    defaults = {
        "max_position_pct": 5.0,
        "max_open_positions": 15,
        "min_cash_pct": 20.0,
        "stop_loss_pct": 8.0,
    }
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        return {**defaults, **cfg.get("risk", {})}
    return defaults


@dataclass
class TradeApproval:
    """Result of a trade approval check."""
    approved: bool
    sized_qty: int
    reason: str


def approve_trade(
    ticker: str,
    side: str,
    quantity: int,
    price: float,
    portfolio_value: float,
    positions: list[dict],
) -> tuple[bool, int, str]:
    """Check if a proposed trade passes all risk rules.

    Args:
        ticker: Stock ticker.
        side: "buy" or "sell".
        quantity: Requested number of shares.
        price: Current price per share.
        portfolio_value: Total portfolio value.
        positions: List of current position dicts with keys: ticker, market_value.

    Returns:
        Tuple of (approved, sized_qty, reason).
    """
    cfg = _load_risk_config()
    max_pos_pct = cfg["max_position_pct"] / 100.0
    max_positions = cfg["max_open_positions"]
    min_cash_pct = cfg["min_cash_pct"] / 100.0

    # Sells are always approved
    if side.lower() == "sell":
        return True, quantity, "Sell orders are always approved."

    # Check max open positions
    if len(positions) >= max_positions:
        existing = [p["ticker"] for p in positions]
        if ticker not in existing:
            return False, 0, f"Max open positions ({max_positions}) reached."

    # Check position size limit
    max_trade_value = portfolio_value * max_pos_pct
    trade_value = quantity * price

    # Check existing exposure to this ticker
    existing_value = sum(
        p.get("market_value", 0) for p in positions if p.get("ticker") == ticker
    )
    total_exposure = existing_value + trade_value

    if total_exposure > max_trade_value:
        # Size down to fit within limit
        allowed_value = max_trade_value - existing_value
        if allowed_value <= 0:
            return False, 0, f"Already at max position size for {ticker} ({max_pos_pct*100}%)."
        sized_qty = int(allowed_value / price)
        if sized_qty <= 0:
            return False, 0, f"Position size too small after risk adjustment."
        return True, sized_qty, f"Sized down from {quantity} to {sized_qty} (max {max_pos_pct*100}% per position)."

    # Check cash reserve
    total_positions_value = sum(p.get("market_value", 0) for p in positions)
    cash = portfolio_value - total_positions_value
    cash_after_trade = cash - trade_value
    min_cash = portfolio_value * min_cash_pct

    if cash_after_trade < min_cash:
        allowed_cash = cash - min_cash
        if allowed_cash <= 0:
            return False, 0, f"Insufficient cash. Min {min_cash_pct*100}% cash reserve required."
        sized_qty = int(allowed_cash / price)
        if sized_qty <= 0:
            return False, 0, f"Insufficient cash after maintaining {min_cash_pct*100}% reserve."
        return True, sized_qty, f"Sized down from {quantity} to {sized_qty} (cash reserve constraint)."

    return True, quantity, "Trade approved."


def compute_trailing_stop(entry_price: float, highest_price: float) -> float:
    """Compute trailing stop-loss price.

    Args:
        entry_price: Original entry price.
        highest_price: Highest price since entry.

    Returns:
        Stop-loss trigger price.
    """
    cfg = _load_risk_config()
    stop_pct = cfg["stop_loss_pct"] / 100.0
    return highest_price * (1 - stop_pct)
