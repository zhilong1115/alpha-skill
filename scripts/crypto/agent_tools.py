"""Crypto Agent Tools — atomic functions for agent-driven crypto trading on Hyperliquid.

V3.0: The agent IS the trader. These are instruments, not a strategy.

The agent:
1. Reads raw indicator values (not just bullish/bearish)
2. Looks at price structure, support/resistance, funding rates
3. Makes dynamic decisions: entry timing, position size, leverage, stop placement
4. Executes and verifies fills
5. Manages positions with judgment, not fixed rules

Usage:
    from scripts.crypto.agent_tools import *
    
    # Check state
    acct = hl_account()
    pos = hl_positions()
    
    # Get raw data for analysis
    data = get_indicators("ETH")  # Full indicator values
    ohlcv = get_ohlcv("ETH", timeframe="4h", days=30)
    sr = get_support_resistance("ETH")
    fr = get_funding_rates()
    
    # Agent decides, then executes
    result = hl_open("ETH", margin_usd=20, leverage=2)
    result = hl_close("ETH")
    hl_set_stop("ETH", 1880.0)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PT = ZoneInfo("America/Los_Angeles")
logger = logging.getLogger(__name__)

# Ensure indicator modules are importable
_SKILL_SCRIPTS = "/Users/zhilongzheng/Projects/alpha-crypto-skill/scripts"
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ── Account & Positions ────────────────────────────────────────────────────

def hl_account(testnet: bool = False) -> dict:
    """Get Hyperliquid account summary.

    Returns: {account_value, withdrawable, margin_used, free_margin,
              num_positions, address, testnet}
    """
    from scripts.crypto.hyperliquid import connect, get_account_info
    connect(testnet=testnet)
    info = get_account_info()
    info["free_margin"] = round(info["withdrawable"] - info["total_margin_used"], 2)
    return info


def hl_positions(testnet: bool = False) -> list[dict]:
    """Get all open Hyperliquid positions with real P&L.

    Returns: [{symbol, side, size, entry_price, mark_price, unrealized_pnl,
               return_on_equity, liquidation_price, leverage, margin_used}]
    """
    from scripts.crypto.hyperliquid import connect, get_positions
    connect(testnet=testnet)
    return get_positions()


def hl_position(symbol: str, testnet: bool = False) -> Optional[dict]:
    """Get a single position. Returns None if not held."""
    for p in hl_positions(testnet=testnet):
        if p["symbol"] == symbol.upper():
            return p
    return None


def hl_price(symbol: str, testnet: bool = False) -> float:
    """Get current mid price from Hyperliquid."""
    from scripts.crypto.hyperliquid import connect, get_price
    connect(testnet=testnet)
    return get_price(symbol)


def hl_prices(testnet: bool = False) -> dict[str, float]:
    """Get prices for BTC, ETH, SOL."""
    from scripts.crypto.hyperliquid import connect, get_price
    connect(testnet=testnet)
    result = {}
    for sym in ["BTC", "ETH", "SOL"]:
        try:
            result[sym] = get_price(sym)
        except Exception:
            pass
    return result


# ── Order Execution ────────────────────────────────────────────────────────

def hl_open(symbol: str, margin_usd: float, leverage: int = 3,
            side: str = "long", testnet: bool = False) -> dict:
    """Open a position with specified margin in USD.

    Args:
        symbol: BTC, ETH, or SOL
        margin_usd: How much USD margin to use (not notional)
        leverage: 1-3
        side: "long" or "short"

    Returns: {status, symbol, side, size, entry_price, margin, leverage,
              notional, order_result, error?}
    """
    from scripts.crypto.hyperliquid import connect, place_order, get_price

    connect(testnet=testnet)
    price = get_price(symbol)
    notional = margin_usd * leverage
    size = notional / price

    order_side = "buy" if side == "long" else "sell"

    try:
        result = place_order(symbol, order_side, size, leverage=leverage)

        # Check for errors in response
        statuses = (result.get("result", {})
                    .get("response", {})
                    .get("data", {})
                    .get("statuses", [{}]))
        err = statuses[0].get("error", "") if statuses else ""

        if err:
            return {"status": "rejected", "symbol": symbol, "error": err}

        # Verify position exists
        pos = hl_position(symbol, testnet=testnet)

        return {
            "status": "filled",
            "symbol": symbol,
            "side": side,
            "size": result.get("size", size),
            "entry_price": pos["entry_price"] if pos else price,
            "margin_usd": round(margin_usd, 2),
            "leverage": leverage,
            "notional": round(notional, 2),
            "order_result": result,
        }
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


def hl_add(symbol: str, margin_usd: float, leverage: int = 3,
           testnet: bool = False) -> dict:
    """Add to an existing position. Same as hl_open but requires existing position."""
    pos = hl_position(symbol, testnet=testnet)
    if not pos:
        return {"status": "error", "symbol": symbol, "error": "No existing position to add to"}
    return hl_open(symbol, margin_usd, leverage, side=pos["side"], testnet=testnet)


def hl_close(symbol: str, testnet: bool = False) -> dict:
    """Close entire position for a symbol.

    Returns: {status, symbol, pnl, exit_price, size, error?}
    """
    from scripts.crypto.hyperliquid import connect, close_position, get_price

    connect(testnet=testnet)
    pos = hl_position(symbol, testnet=testnet)
    if not pos:
        return {"status": "no_position", "symbol": symbol}

    entry = pos["entry_price"]
    size = pos["size"]
    pre_pnl = pos["unrealized_pnl"]

    try:
        result = close_position(symbol)
        exit_price = get_price(symbol)

        # Verify closed
        remaining = hl_position(symbol, testnet=testnet)

        return {
            "status": "closed" if not remaining else "partial",
            "symbol": symbol,
            "side": pos["side"],
            "size": size,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl": round(pre_pnl, 2),
            "pnl_pct": round(pos["return_on_equity"] * 100, 2),
            "order_result": result,
        }
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


def hl_reduce(symbol: str, pct: float = 50, testnet: bool = False) -> dict:
    """Reduce position by a percentage (e.g., 50% = sell half).

    Args:
        pct: Percentage to close (1-100)

    Returns: {status, symbol, closed_size, remaining_size}
    """
    from scripts.crypto.hyperliquid import connect, place_order, get_price

    connect(testnet=testnet)
    pos = hl_position(symbol, testnet=testnet)
    if not pos:
        return {"status": "no_position", "symbol": symbol}

    close_size = pos["size"] * (pct / 100)
    order_side = "sell" if pos["side"] == "long" else "buy"

    try:
        price = get_price(symbol)
        # Use reduce_only via closing side
        from scripts.crypto.hyperliquid import _ensure_connected, _round_size, _round_price
        _, exchange = _ensure_connected()
        close_size = _round_size(symbol.upper(), close_size)
        slippage = 0.02
        limit_px = price * (1 - slippage) if order_side == "sell" else price * (1 + slippage)
        limit_px = _round_price(symbol.upper(), limit_px)

        result = exchange.order(
            name=symbol.upper(),
            is_buy=(order_side == "buy"),
            sz=close_size,
            limit_px=limit_px,
            order_type={"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )

        remaining = hl_position(symbol, testnet=testnet)
        return {
            "status": "reduced",
            "symbol": symbol,
            "closed_size": close_size,
            "remaining_size": remaining["size"] if remaining else 0,
            "order_result": result,
        }
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


def hl_set_stop(symbol: str, price: float, testnet: bool = False) -> dict:
    """Set stop-loss at a specific price. Cancels existing stops first.

    Returns: {status, symbol, trigger_price, error?}
    """
    from scripts.crypto.hyperliquid import connect, cancel_all_orders, set_stop_loss

    connect(testnet=testnet)
    cancelled = cancel_all_orders(symbol)
    try:
        result = set_stop_loss(symbol, price)
        return {
            "status": "set",
            "symbol": symbol,
            "trigger_price": price,
            "cancelled_previous": cancelled,
            "result": result,
        }
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


def hl_set_tp(symbol: str, price: float, testnet: bool = False) -> dict:
    """Set take-profit at a specific price.

    Returns: {status, symbol, trigger_price}
    """
    from scripts.crypto.hyperliquid import connect, set_take_profit

    connect(testnet=testnet)
    try:
        result = set_take_profit(symbol, price)
        return {"status": "set", "symbol": symbol, "trigger_price": price, "result": result}
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


def hl_cancel_orders(symbol: str = None, testnet: bool = False) -> int:
    """Cancel all open orders, optionally for a specific symbol."""
    from scripts.crypto.hyperliquid import connect, cancel_all_orders
    connect(testnet=testnet)
    return cancel_all_orders(symbol)


def hl_open_orders(testnet: bool = False) -> list[dict]:
    """Get all open orders."""
    from scripts.crypto.hyperliquid import connect, _ensure_connected, _get_master_address
    connect(testnet=testnet)
    info, _ = _ensure_connected()
    address = _get_master_address() or info.user_state.__self__  # fallback
    from scripts.crypto.hyperliquid import _master_address, _account_address
    addr = _master_address or _account_address
    return info.open_orders(addr)


def suggest_stop(symbol: str, entry_price: float, side: str = "long",
                 current_price: Optional[float] = None, atr_multiple: float = 1.5) -> dict:
    """Calculate dynamic stop-loss recommendation based on position state.

    Initial stop: 1.5x ATR from entry (minimum floor).
    Trailing stop: as position profits, stop moves up to lock in gains.

    Trailing tiers (for longs):
      ROE < 10%:  stop = entry - 1.5x ATR  (initial, protect capital)
      ROE 10-20%: stop = entry - 0.5x ATR  (near breakeven, let it breathe)
      ROE 20-40%: stop = entry + 0.5x ATR  (lock in some profit)
      ROE > 40%:  stop = entry + 1.0x ATR  (trail aggressively)

    Always returns the HIGHER of initial stop and trailing stop (never move stop down).

    Returns: {stop_price, atr, atr_pct, roe_pct, tier, note, action}
    """
    ind = get_indicators(symbol)
    atr = ind.get("atr_14", 0)
    atr_pct = ind.get("atr_pct", 0)
    mark = current_price or ind.get("price", entry_price)

    # Calculate ROE (simplified: price change / entry, not margin-adjusted)
    price_change_pct = (mark - entry_price) / entry_price * 100 if side == "long" else (entry_price - mark) / entry_price * 100

    # Initial stop (capital protection floor)
    if side == "long":
        initial_stop = entry_price - (atr * atr_multiple)
    else:
        initial_stop = entry_price + (atr * atr_multiple)

    # Trailing stop based on profit tier
    if price_change_pct < 10:
        trailing_stop = initial_stop
        tier = "initial"
        note = "below breakeven threshold — hold at initial stop"
    elif price_change_pct < 20:
        trailing_stop = entry_price - (atr * 0.5) if side == "long" else entry_price + (atr * 0.5)
        tier = "near_breakeven"
        note = "move stop near breakeven, protect against reversal"
    elif price_change_pct < 40:
        trailing_stop = entry_price + (atr * 0.5) if side == "long" else entry_price - (atr * 0.5)
        tier = "lock_profit"
        note = "lock in partial profit, stop above entry"
    else:
        trailing_stop = entry_price + (atr * 1.0) if side == "long" else entry_price - (atr * 1.0)
        tier = "trail_aggressive"
        note = "strong profit — trail stop aggressively"

    # Never move stop in wrong direction
    if side == "long":
        recommended_stop = max(initial_stop, trailing_stop)
    else:
        recommended_stop = min(initial_stop, trailing_stop)

    distance_pct = abs(recommended_stop - entry_price) / entry_price * 100

    return {
        "symbol": symbol,
        "entry": entry_price,
        "current_price": round(mark, 2),
        "price_change_pct": round(price_change_pct, 2),
        "stop_price": round(recommended_stop, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "distance_from_entry_pct": round(distance_pct, 2),
        "tier": tier,
        "note": note,
        "action": "raise_stop" if trailing_stop > initial_stop and side == "long" else "maintain_stop",
    }


def hl_recent_fills(hours: int = 2, testnet: bool = False) -> list[dict]:
    """Get recent fills from Hyperliquid to detect stop triggers or manual closes.

    Returns list of fills in the last N hours, sorted newest first.
    Use to detect if a position was closed unexpectedly (stop hit).
    """
    from scripts.crypto.hyperliquid import connect, _ensure_connected, _get_master_address
    from scripts.crypto.hyperliquid import _master_address, _account_address
    import datetime

    connect(testnet=testnet)
    info, _ = _ensure_connected()
    addr = _master_address or _account_address

    try:
        fills = info.user_fills(addr)
        cutoff_ms = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).timestamp() * 1000
        recent = [f for f in fills if f.get("time", 0) >= cutoff_ms]
        recent.sort(key=lambda x: x.get("time", 0), reverse=True)

        result = []
        for f in recent:
            ts = datetime.datetime.fromtimestamp(f["time"] / 1000, tz=datetime.timezone.utc)
            ts_pst = ts.astimezone(ZoneInfo("America/Los_Angeles"))
            result.append({
                "time": ts_pst.strftime("%H:%M:%S PST"),
                "symbol": f.get("coin", ""),
                "side": "buy" if f.get("side") == "B" else "sell",
                "size": f.get("sz", ""),
                "price": f.get("px", ""),
                "direction": f.get("dir", ""),
                "closed_pnl": float(f.get("closedPnl", 0)),
                "fee": float(f.get("fee", 0)),
            })
        return result
    except Exception as e:
        return [{"error": str(e)}]


# ── Market Data & Indicators ──────────────────────────────────────────────

def get_ohlcv(symbol: str, timeframe: str = "1d", days: int = 250) -> pd.DataFrame:
    """Fetch OHLCV candles for a symbol.

    Args:
        symbol: BTC, ETH, SOL (or BTC/USD format)
        timeframe: "1d", "4h", "1h", "15m"
        days: How many days of history

    Returns: DataFrame with open, high, low, close, volume columns
    """
    from scripts.crypto.alpaca_crypto import get_crypto_bars
    from alpaca.data.timeframe import TimeFrame

    # Normalize symbol
    if "/" not in symbol:
        symbol = f"{symbol}/USD"

    tf_map = {
        "1d": TimeFrame.Day,
        "4h": TimeFrame(4, "Hour"),
        "1h": TimeFrame.Hour,
        "15m": TimeFrame(15, "Minute"),
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)
    start = datetime.now() - timedelta(days=days)

    df = get_crypto_bars(symbol, tf, start)
    return df


def get_indicators(symbol: str, timeframe: str = "1d", days: int = 250) -> dict:
    """Get full indicator values for a symbol — not just bullish/bearish.

    Returns raw values so the agent can reason about magnitude, not just direction.

    Returns: {
        symbol, price, timestamp,
        tsi, tsi_prev, tsi_trend (rising/falling), tsi_zone (oversold/neutral/overbought),
        obv, obv_ema9, obv_above_ema,
        wt1, wt2, wt_cross (bullish/bearish/none), wt_zone,
        usdt_d_tsi, usdt_d_tsi_prev, usdt_d_falling,
        regime (BULL/BEAR), sma200, mayer_multiple,
        signals: {tsi_bull, obv_bull, usdt_bull, wt_bull},
        bullish_count,
        atr_14, atr_pct (volatility measure),
    }
    """
    from indicators import calc_all_indicators, _binary_signals

    df = get_ohlcv(symbol, timeframe=timeframe, days=days)
    if df.empty or len(df) < 200:
        return {"symbol": symbol, "error": "Insufficient data"}

    df = calc_all_indicators(df)

    # Calculate ATR for volatility context
    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = df["tr"].rolling(14).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    price = last["close"]

    # Binary signals (for reference)
    sigs = _binary_signals(last)

    # TSI analysis
    tsi = last["tsi"]
    tsi_prev = last["tsi_prev"]
    if tsi < -25:
        tsi_zone = "oversold"
    elif tsi > 25:
        tsi_zone = "overbought"
    else:
        tsi_zone = "neutral"

    # WaveTrend analysis
    wt1, wt2 = last["wt1"], last["wt2"]
    wt1_prev, wt2_prev = last.get("wt1_prev", wt1), last.get("wt2_prev", wt2)
    if wt1 > wt2 and wt1_prev <= wt2_prev:
        wt_cross = "bullish_cross"
    elif wt1 < wt2 and wt1_prev >= wt2_prev:
        wt_cross = "bearish_cross"
    elif wt1 > wt2:
        wt_cross = "bullish"
    else:
        wt_cross = "bearish"

    if wt1 < -53:
        wt_zone = "oversold"
    elif wt1 > 53:
        wt_zone = "overbought"
    else:
        wt_zone = "neutral"

    # Regime
    sma200 = last["sma200"]
    regime = "BULL" if price > sma200 else "BEAR"

    # ATR
    atr = last["atr_14"] if pd.notna(last["atr_14"]) else 0
    atr_pct = (atr / price * 100) if price > 0 else 0

    bullish_count = sum([sigs["tsi_bull"], sigs["obv_bull"], sigs["usdt_bull"], sigs["wt_bull"]])

    return {
        "symbol": symbol.replace("/USD", ""),
        "price": round(price, 2),
        "timestamp": str(df.index[-1]),
        # TSI
        "tsi": round(tsi, 2) if pd.notna(tsi) else None,
        "tsi_prev": round(tsi_prev, 2) if pd.notna(tsi_prev) else None,
        "tsi_trend": "rising" if tsi > tsi_prev else "falling",
        "tsi_zone": tsi_zone,
        # OBV
        "obv_above_ema": bool(sigs["obv_bull"]),
        # WaveTrend
        "wt1": round(wt1, 2) if pd.notna(wt1) else None,
        "wt2": round(wt2, 2) if pd.notna(wt2) else None,
        "wt_cross": wt_cross,
        "wt_zone": wt_zone,
        # USDT.D
        "usdt_d_tsi": round(last["usdt_d_tsi"], 2) if pd.notna(last["usdt_d_tsi"]) else None,
        "usdt_d_falling": bool(sigs["usdt_bull"]),
        # Regime
        "regime": regime,
        "sma200": round(sma200, 2) if pd.notna(sma200) else None,
        "mayer_multiple": round(last["mayer"], 3) if pd.notna(last.get("mayer")) else None,
        # Signals summary
        "signals": {
            "tsi_bull": bool(sigs["tsi_bull"]),
            "obv_bull": bool(sigs["obv_bull"]),
            "usdt_bull": bool(sigs["usdt_bull"]),
            "wt_bull": bool(sigs["wt_bull"]),
        },
        "bullish_count": bullish_count,
        # Volatility
        "atr_14": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
    }


def get_support_resistance(symbol: str, days: int = 60) -> dict:
    """Find key support and resistance levels from recent price action.

    Returns: {
        supports: [{price, type, strength}],
        resistances: [{price, type, strength}],
        current_price, nearest_support, nearest_resistance,
        support_distance_pct, resistance_distance_pct,
    }
    """
    df = get_ohlcv(symbol, timeframe="1d", days=days)
    if df.empty or len(df) < 10:
        return {"symbol": symbol, "error": "Insufficient data"}

    price = df["close"].iloc[-1]
    highs = df["high"].values
    lows = df["low"].values

    # Find swing lows (supports)
    supports = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            # Count how many times price bounced near this level
            level = lows[i]
            touches = sum(1 for l in lows if abs(l - level) / level < 0.02)
            supports.append({"price": round(level, 2), "type": "swing_low", "strength": touches})

    # Find swing highs (resistances)
    resistances = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            level = highs[i]
            touches = sum(1 for h in highs if abs(h - level) / level < 0.02)
            resistances.append({"price": round(level, 2), "type": "swing_high", "strength": touches})

    # Add round number levels
    sym = symbol.replace("/USD", "").upper()
    from scripts.crypto.hyperliquid_trader import KEY_LEVELS
    for level in KEY_LEVELS.get(sym, []):
        dist_pct = abs(level - price) / price * 100
        if dist_pct < 20:  # Only levels within 20%
            if level < price:
                supports.append({"price": level, "type": "key_level", "strength": 3})
            else:
                resistances.append({"price": level, "type": "key_level", "strength": 3})

    # Sort and deduplicate
    supports = sorted(supports, key=lambda x: x["price"], reverse=True)
    resistances = sorted(resistances, key=lambda x: x["price"])

    # Dedupe nearby levels (within 1%)
    supports = _dedupe_levels(supports)
    resistances = _dedupe_levels(resistances)

    # Only keep levels below/above current price
    supports = [s for s in supports if s["price"] < price]
    resistances = [r for r in resistances if r["price"] > price]

    nearest_support = supports[0]["price"] if supports else None
    nearest_resistance = resistances[0]["price"] if resistances else None

    return {
        "symbol": sym,
        "current_price": round(price, 2),
        "supports": supports[:5],
        "resistances": resistances[:5],
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_distance_pct": round((price - nearest_support) / price * 100, 2) if nearest_support else None,
        "resistance_distance_pct": round((nearest_resistance - price) / price * 100, 2) if nearest_resistance else None,
    }


def _dedupe_levels(levels: list[dict], threshold_pct: float = 1.0) -> list[dict]:
    """Merge nearby price levels, keeping the one with highest strength."""
    if not levels:
        return []
    result = [levels[0]]
    for level in levels[1:]:
        if abs(level["price"] - result[-1]["price"]) / result[-1]["price"] * 100 < threshold_pct:
            if level["strength"] > result[-1]["strength"]:
                result[-1] = level
        else:
            result.append(level)
    return result


def get_funding_rates(testnet: bool = False) -> dict:
    """Get current funding rates for BTC, ETH, SOL.

    Returns: {BTC: {hourly, daily, annualized_pct}, ...}
    High positive = expensive to hold longs. Negative = paid to hold longs.
    """
    from scripts.crypto.hyperliquid import connect, get_all_funding_rates
    connect(testnet=testnet)
    return get_all_funding_rates()


def get_correlation(coin1: str = "BTC", coin2: str = "ETH", days: int = 30) -> dict:
    """Calculate price correlation between two coins.

    Returns: {correlation, coin1, coin2, period_days}
    """
    df1 = get_ohlcv(coin1, timeframe="1d", days=days)
    df2 = get_ohlcv(coin2, timeframe="1d", days=days)

    if df1.empty or df2.empty:
        return {"error": "Insufficient data"}

    # Align dates
    returns1 = df1["close"].pct_change().dropna()
    returns2 = df2["close"].pct_change().dropna()

    # Align on common index
    common = returns1.index.intersection(returns2.index)
    if len(common) < 10:
        return {"error": "Not enough common dates"}

    corr = returns1.loc[common].corr(returns2.loc[common])

    return {
        "coin1": coin1,
        "coin2": coin2,
        "correlation": round(corr, 3),
        "period_days": days,
        "interpretation": "high" if abs(corr) > 0.7 else "moderate" if abs(corr) > 0.4 else "low",
    }


# ── Portfolio Summary ──────────────────────────────────────────────────────

def portfolio_summary(testnet: bool = False) -> dict:
    """Complete portfolio snapshot — everything the agent needs in one call.

    Returns: {account, positions, prices, total_pnl, margin_usage_pct}
    """
    acct = hl_account(testnet=testnet)
    pos = hl_positions(testnet=testnet)
    px = hl_prices(testnet=testnet)

    total_pnl = sum(p["unrealized_pnl"] for p in pos)
    margin_usage = acct["total_margin_used"] / acct["withdrawable"] * 100 if acct["withdrawable"] > 0 else 0

    return {
        "account": {
            "withdrawable": acct["withdrawable"],
            "margin_used": acct["total_margin_used"],
            "free_margin": acct["free_margin"],
            "margin_usage_pct": round(margin_usage, 1),
        },
        "positions": [
            {
                "symbol": p["symbol"],
                "side": p["side"],
                "size": p["size"],
                "entry": p["entry_price"],
                "mark": p["mark_price"],
                "pnl": round(p["unrealized_pnl"], 2),
                "roe": round(p["return_on_equity"] * 100, 1),
                "leverage": p["leverage"],
                "margin": round(p["margin_used"], 2),
                "liq": p["liquidation_price"],
            }
            for p in pos
        ],
        "prices": px,
        "total_unrealized_pnl": round(total_pnl, 2),
    }
