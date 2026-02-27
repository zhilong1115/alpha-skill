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
    cancel_all_orders,
    SUPPORTED_SYMBOLS, MAX_LEVERAGE,
)

# Map Hyperliquid symbols to Alpaca-style for data fetching
HL_TO_ALPACA = {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD"}

STOP_LOSS_PCT = 0.05       # 5% stop-loss from entry (legacy, used as fallback)
STRUCTURAL_SL_MAX_PCT = 0.15  # Max stop-loss distance (safety floor)
STRUCTURAL_SL_MIN_PCT = 0.05  # Min stop-loss distance

# Key support levels for structural stop-loss
KEY_LEVELS = {
    "ETH": [2500, 2400, 2300, 2200, 2100, 2000, 1900, 1800, 1700, 1600, 1500],
    "SOL": [100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50],
    "BTC": [100000, 95000, 90000, 85000, 80000, 75000, 70000, 65000, 60000],
}
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
    # Use withdrawable (real USDC) for sizing — account_value is inflated by leverage
    withdrawable = account.get("withdrawable", account_value)
    margin_used = account.get("total_margin_used", 0)
    sizing_base = withdrawable  # Size based on real USDC, not leverage-inflated account_value
    # Hyperliquid reserves part of withdrawable for maintenance margin on losing positions.
    # If margin_used > withdrawable, no room for new orders (exchange will reject).
    can_open_new = withdrawable > margin_used
    if account_value <= 0:
        return [{"action": "error", "reason": "Account has no value"}]

    current_positions = get_positions()
    pos_map = {p["symbol"]: p for p in current_positions}

    for symbol in SUPPORTED_SYMBOLS:
        try:
            action = _process_symbol(symbol, sizing_base, pos_map, dry_run, can_open_new=can_open_new)
            actions.append(action)
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            actions.append({"symbol": symbol, "action": "error", "reason": str(e)})

    return actions


def _process_symbol(
    symbol: str, account_value: float, pos_map: dict, dry_run: bool, can_open_new: bool = True
) -> dict:
    """Process a single symbol: analyze signal, determine action, execute.

    account_value = withdrawable (real free USDC). Used for target position sizing.
    can_open_new = False when margin_used > withdrawable (Hyperliquid will reject new orders).
    """
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
        if not can_open_new:
            result["action"] = "skip"
            result["reason"] = f"No margin (margin_used > withdrawable)"
        elif target_size > 0:
            side = "buy" if target_side == "long" else "sell"
            result["action"] = f"OPEN_{target_side.upper()}"
            if not dry_run:
                order_result = place_order(
                    symbol, side, target_size, leverage=DEFAULT_LEVERAGE
                )
                statuses = (order_result.get("result", {})
                            .get("response", {})
                            .get("data", {})
                            .get("statuses", [{}]))
                err = statuses[0].get("error", "") if statuses else ""
                if err:
                    result["action"] = "skip"
                    result["reason"] = f"Order rejected: {err}"
                    logger.warning(f"{symbol}: OPEN rejected — {err}")
                else:
                    result["execution"] = order_result
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
            # Same direction — compare current vs target as % of withdrawable
            # account_value here = withdrawable (real USDC)
            current_pct = current_notional / account_value if account_value > 0 else 0
            target_pct_notional = target_margin_pct * DEFAULT_LEVERAGE  # e.g., 0.225 * 3 = 0.675
            pct_gap = target_pct_notional - current_pct  # positive = under target, need to add

            result["current_alloc_pct"] = round(current_pct * 100, 1)
            result["target_alloc_pct"] = round(target_pct_notional * 100, 1)

            if pct_gap >= 0.10 and can_open_new:
                # Under target by ≥10 percentage points AND signal got stronger → add
                additional_notional = pct_gap * account_value
                add_size = round(additional_notional / current_price, 6)
                required_margin = additional_notional / DEFAULT_LEVERAGE
                if account_value >= required_margin * 1.5:
                    result["action"] = f"INCREASE_{target_side.upper()}"
                    if not dry_run:
                        side = "buy" if target_side == "long" else "sell"
                        order_result = place_order(
                            symbol, side, add_size, leverage=DEFAULT_LEVERAGE
                        )
                        statuses = (order_result.get("result", {})
                                    .get("response", {})
                                    .get("data", {})
                                    .get("statuses", [{}]))
                        err = statuses[0].get("error", "") if statuses else ""
                        if err:
                            result["action"] = "hold"
                            result["reason"] = f"Increase rejected: {err}"
                            logger.warning(f"{symbol}: INCREASE rejected — {err}")
                        else:
                            result["execution"] = order_result
                            _set_position_stop_loss(symbol, target_side, current_price)
                else:
                    result["action"] = "hold"
                    result["reason"] = f"Insufficient withdrawable for add (need ${required_margin*1.5:.1f})"
            else:
                # At or above target % → hold, never force-reduce
                result["action"] = "hold"
                if pct_gap < 0:
                    result["reason"] = f"Over target ({current_pct*100:.0f}% > {target_pct_notional*100:.0f}% of withdrawable)"
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
        # TODO: re-enable shorting when trend is clear
        # return "short", MAX_MARGIN_PER_POSITION * 0.5
        return None, 0
    elif signal == "HOLD" and target_pct > 0:
        return "long", target_pct
    else:
        return None, 0


def _find_swing_low(symbol: str, entry_price: float, lookback_days: int = 60) -> float | None:
    """Find the most recent swing low within -5% to -20% of entry price."""
    try:
        import ccxt
        exchange = ccxt.coinbase()
        pair = f"{symbol}/USDT"
        ohlcv = exchange.fetch_ohlcv(pair, '1d', limit=lookback_days + 5)
        if len(ohlcv) < 5:
            return None

        min_price = entry_price * (1 - 0.20)  # -20%
        max_price = entry_price * (1 - STRUCTURAL_SL_MIN_PCT)  # -5%

        # Find swing lows (local minima with >3% bounce)
        candidates = []
        for i in range(1, len(ohlcv) - 1):
            low = ohlcv[i][3]
            if low < ohlcv[i-1][3] and low < ohlcv[i+1][3]:
                if min_price <= low <= max_price:
                    candidates.append(low)

        # Return the highest (most conservative) swing low in range
        return max(candidates) if candidates else None
    except Exception as e:
        logger.warning(f"Failed to fetch swing low for {symbol}: {e}")
        return None


def _find_key_level(symbol: str, entry_price: float) -> float | None:
    """Find the nearest key support level below entry within acceptable range."""
    levels = KEY_LEVELS.get(symbol, [])
    min_price = entry_price * (1 - STRUCTURAL_SL_MAX_PCT)
    max_price = entry_price * (1 - STRUCTURAL_SL_MIN_PCT)

    valid = [l for l in levels if min_price <= l <= max_price]
    return max(valid) if valid else None


def _compute_structural_stop(symbol: str, side: str, entry_price: float) -> float:
    """Compute structural stop-loss: max(swing_low, key_level, safety_floor).
    
    For longs: finds support below entry.
    For shorts: mirrors logic above entry (inverted).
    """
    if side == "short":
        # For shorts, simple percentage-based for now
        return entry_price * (1 + STOP_LOSS_PCT)

    # Safety floor: -15% max
    safety_floor = entry_price * (1 - STRUCTURAL_SL_MAX_PCT)

    swing_low = _find_swing_low(symbol, entry_price) or 0
    key_level = _find_key_level(symbol, entry_price) or 0

    # Take the highest (most conservative) of the three
    sl_price = max(swing_low, key_level, safety_floor)

    logger.info(
        f"Structural SL for {symbol}: swing_low=${swing_low:,.2f}, "
        f"key_level=${key_level:,.2f}, safety=${safety_floor:,.2f} → stop=${sl_price:,.2f} "
        f"({(1 - sl_price/entry_price)*100:.1f}% from entry)"
    )
    return sl_price


def _set_position_stop_loss(symbol: str, side: str, entry_price: float) -> None:
    """Set exchange-level stop-loss using structural support levels.
    
    Uses max(swing_low, key_level, entry*0.85) for longs.
    Cancels existing orders first.
    """
    try:
        sl_price = _compute_structural_stop(symbol, side, entry_price)

        # Cancel existing stop orders for this symbol before placing new one
        cancelled = cancel_all_orders(symbol)
        if cancelled:
            logger.info(f"Cancelled {cancelled} existing orders for {symbol} before setting new stop")

        set_stop_loss(symbol, sl_price)
        pct = abs(sl_price - entry_price) / entry_price * 100
        logger.info(f"Stop-loss set for {symbol} at ${sl_price:,.2f} ({pct:.1f}% from ${entry_price:,.2f})")
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
