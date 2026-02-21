#!/usr/bin/env python3
"""Hyperliquid trader — connects Conservative signals to Hyperliquid execution.

Analyzes BTC, ETH, SOL using the existing Conservative signal system,
then executes leveraged perp trades on Hyperliquid with exchange-level stop-losses.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
logger = logging.getLogger(__name__)

# Add crypto skill to path
_SKILL_SCRIPTS = "/Users/zhilongzheng/Projects/alpha-crypto-skill/scripts"
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.crypto.hyperliquid import (
    connect, get_account_info, get_positions, get_price,
    place_order, close_position, set_stop_loss, set_leverage,
    SUPPORTED_SYMBOLS, MAX_LEVERAGE,
)

# Map Hyperliquid symbols to Alpaca-style for data fetching
HL_TO_ALPACA = {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD"}

STOP_LOSS_PCT = 0.05       # 5% stop-loss from entry
TAKE_PROFIT_PCT = 0.15     # 15% take-profit
DEFAULT_LEVERAGE = 3
MAX_MARGIN_PER_POSITION = 0.30  # 30% of account as margin


def _get_signal(symbol: str) -> dict:
    """Get Conservative signal for a symbol using existing analyze_symbol."""
    from scripts.crypto.crypto_trader import analyze_symbol
    alpaca_sym = HL_TO_ALPACA.get(symbol, f"{symbol}/USD")
    return analyze_symbol(alpaca_sym)


def analyze_and_trade(dry_run: bool = True, testnet: bool = True) -> list[dict]:
    """Analyze all symbols and execute trades based on Conservative signals.

    For each symbol:
    1. Get Conservative signal
    2. Determine target position (long/short/flat)
    3. Size based on signal strength + leverage
    4. Execute via Hyperliquid
    5. Set exchange-level stop-loss
    
    Args:
        dry_run: If True, don't execute trades.
        testnet: Use testnet (default True).
    
    Returns:
        List of actions taken.
    """
    actions = []

    # Connect
    try:
        connect(testnet=testnet)
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        return [{"action": "error", "reason": f"Connection failed: {e}"}]

    account = get_account_info()
    account_value = account["account_value"]
    if account_value <= 0:
        return [{"action": "error", "reason": "Account has no value"}]

    current_positions = get_positions()
    pos_map = {p["symbol"]: p for p in current_positions}

    for symbol in SUPPORTED_SYMBOLS:
        try:
            action = _process_symbol(symbol, account_value, pos_map, dry_run)
            actions.append(action)
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            actions.append({"symbol": symbol, "action": "error", "reason": str(e)})

    return actions


def _process_symbol(
    symbol: str, account_value: float, pos_map: dict, dry_run: bool
) -> dict:
    """Process a single symbol: analyze signal, determine action, execute."""
    # Get signal
    analysis = _get_signal(symbol)
    if analysis.get("error"):
        return {"symbol": symbol, "action": "skip", "reason": analysis["error"]}

    signal = analysis["signal"]
    target_pct = analysis.get("target_position_pct", 0)
    current_price = analysis["price"]

    # Current position
    pos = pos_map.get(symbol)
    current_side = pos["side"] if pos else None
    current_size = pos["size"] if pos else 0

    # Determine target position
    target_side, target_margin_pct = _signal_to_position(signal, target_pct)

    # Cap margin at MAX_MARGIN_PER_POSITION
    target_margin_pct = min(target_margin_pct, MAX_MARGIN_PER_POSITION)
    target_margin = account_value * target_margin_pct
    target_notional = target_margin * DEFAULT_LEVERAGE
    target_size = target_notional / current_price if current_price > 0 else 0

    # Current notional
    current_notional = current_size * current_price if current_price > 0 else 0

    result = {
        "symbol": symbol,
        "signal": signal,
        "target_pct": target_pct,
        "target_side": target_side,
        "target_margin_pct": target_margin_pct,
        "target_notional": target_notional,
        "current_side": current_side,
        "current_notional": current_notional,
        "price": current_price,
        "leverage": DEFAULT_LEVERAGE,
        "dry_run": dry_run,
    }

    # Determine action
    if target_side is None and pos:
        # Close position
        result["action"] = "CLOSE"
        if not dry_run:
            close_result = close_position(symbol)
            result["execution"] = close_result
    elif target_side and not pos:
        # Open new position
        if target_size > 0:
            side = "buy" if target_side == "long" else "sell"
            result["action"] = f"OPEN_{target_side.upper()}"
            if not dry_run:
                order_result = place_order(
                    symbol, side, target_size, leverage=DEFAULT_LEVERAGE
                )
                result["execution"] = order_result
                # Set stop-loss
                _set_position_stop_loss(symbol, target_side, current_price)
        else:
            result["action"] = "skip"
            result["reason"] = "Target size too small"
    elif target_side and pos:
        # Position exists — check if we need to adjust
        if target_side != current_side:
            # Flip direction: close then open
            result["action"] = f"FLIP_{current_side.upper()}_TO_{target_side.upper()}"
            if not dry_run:
                close_position(symbol)
                side = "buy" if target_side == "long" else "sell"
                order_result = place_order(
                    symbol, side, target_size, leverage=DEFAULT_LEVERAGE
                )
                result["execution"] = order_result
                _set_position_stop_loss(symbol, target_side, current_price)
        else:
            # Same direction — check size difference
            size_diff = target_size - current_size
            pct_diff = abs(size_diff) / current_size if current_size > 0 else 1.0

            if pct_diff < 0.1:  # Within 10%, skip
                result["action"] = "hold"
            elif size_diff > 0:
                result["action"] = f"INCREASE_{target_side.upper()}"
                if not dry_run:
                    side = "buy" if target_side == "long" else "sell"
                    order_result = place_order(
                        symbol, side, abs(size_diff), leverage=DEFAULT_LEVERAGE
                    )
                    result["execution"] = order_result
                    _set_position_stop_loss(symbol, target_side, current_price)
            else:
                result["action"] = f"DECREASE_{target_side.upper()}"
                if not dry_run:
                    # Partial close — reduce only
                    side = "sell" if target_side == "long" else "buy"
                    order_result = place_order(
                        symbol, side, abs(size_diff), leverage=DEFAULT_LEVERAGE
                    )
                    result["execution"] = order_result
    else:
        result["action"] = "flat"

    return result


def _signal_to_position(signal: str, target_pct: float) -> tuple[str | None, float]:
    """Convert Conservative signal to position direction and margin %.

    Returns:
        (side, margin_pct) where side is "long", "short", or None
    """
    if signal == "BUY" and target_pct > 0:
        return "long", target_pct
    elif signal == "SELL":
        # Conservative SELL = close long, don't short (too risky for Conservative mode)
        return None, 0
    elif signal == "HOLD" and target_pct > 0:
        return "long", target_pct
    else:
        return None, 0


def _set_position_stop_loss(symbol: str, side: str, entry_price: float) -> None:
    """Set exchange-level stop-loss 5% from entry."""
    try:
        if side == "long":
            sl_price = entry_price * (1 - STOP_LOSS_PCT)
        else:
            sl_price = entry_price * (1 + STOP_LOSS_PCT)

        set_stop_loss(symbol, sl_price)
        logger.info(f"Stop-loss set for {symbol} at ${sl_price:,.2f} ({STOP_LOSS_PCT*100:.0f}% from ${entry_price:,.2f})")
    except Exception as e:
        logger.error(f"Failed to set stop-loss for {symbol}: {e}")


def format_trade_results(actions: list[dict]) -> str:
    """Format trade results for display."""
    lines = [
        "=" * 55,
        "⚡ HYPERLIQUID CONSERVATIVE TRADER",
        "=" * 55,
    ]

    for a in actions:
        if a.get("error") or a.get("reason"):
            lines.append(f"  ❓ {a.get('symbol', '?')}: {a.get('reason', a.get('error', '?'))}")
            continue

        action = a.get("action", "?")
        emoji = {
            "hold": "🟡", "flat": "⚪", "skip": "⚪",
        }.get(action, "🟢" if "OPEN" in action or "INCREASE" in action else "🔴")

        symbol = a.get("symbol", "?")
        signal = a.get("signal", "?")
        price = a.get("price", 0)

        lines.append(f"\n  {emoji} {symbol} — {action}")
        lines.append(f"    Signal: {signal} | Target: {a.get('target_margin_pct', 0)*100:.0f}% margin")
        lines.append(f"    Price: ${price:,.2f} | Leverage: {a.get('leverage', '?')}x")
        lines.append(f"    Target notional: ${a.get('target_notional', 0):,.0f} | Current: ${a.get('current_notional', 0):,.0f}")
        if a.get("dry_run"):
            lines.append(f"    [DRY RUN — no orders placed]")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Hyperliquid Conservative Trader")
    parser.add_argument("--execute", action="store_true", help="Execute real trades")
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet (default: testnet)")
    args = parser.parse_args()

    actions = analyze_and_trade(dry_run=not args.execute, testnet=not args.mainnet)
    print(format_trade_results(actions))
