#!/usr/bin/env python3
"""Crypto trader using Conservative mode signals with Alpaca execution.

Fetches daily candles from Alpaca, calculates indicators using the
crypto-trading skill, and executes trades based on Conservative scoring.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta

import pandas as pd

# Add crypto-trading skill to path for indicator imports
_SKILL_SCRIPTS = "/Users/zhilongzheng/Projects/alpha-crypto-skill/scripts"
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

from indicators import calc_all_indicators, _binary_signals
from conservative import score_conservative, get_signal, BULL_SIZING, BEAR_SIZING

from scripts.crypto.alpaca_crypto import (
    get_crypto_positions, get_crypto_exposure, get_crypto_buying_power,
    place_crypto_order, close_crypto_position, get_crypto_bars,
    CRYPTO_ALLOCATION, MAX_PER_COIN, SUPPORTED_SYMBOLS,
)
from alpaca.data.timeframe import TimeFrame


STOP_LOSS_PCT = 0.05  # 5% stop-loss per position


def fetch_daily_data(symbol: str, days: int = 250) -> pd.DataFrame:
    """Fetch daily candles from Alpaca for indicator calculation."""
    start = datetime.now() - timedelta(days=days)
    df = get_crypto_bars(symbol, TimeFrame.Day, start)
    return df


def analyze_symbol(symbol: str) -> dict:
    """Analyze a single crypto symbol using Conservative mode."""
    df = fetch_daily_data(symbol)
    if df.empty or len(df) < 200:
        return {"symbol": symbol, "error": "Insufficient data", "signal": "SKIP"}

    df = calc_all_indicators(df)
    last = df.iloc[-1]
    signal, count, regime, target_pos, details = get_signal(last)

    return {
        "symbol": symbol,
        "price": last["close"],
        "signal": signal,
        "bullish_count": count,
        "regime": regime,
        "target_position_pct": target_pos,
        "target_notional": target_pos * CRYPTO_ALLOCATION,
        "details": details,
        "tsi": round(last["tsi"], 2) if pd.notna(last["tsi"]) else None,
        "wt1": round(last["wt1"], 2) if pd.notna(last["wt1"]) else None,
        "sma200": round(last["sma200"], 2) if pd.notna(last["sma200"]) else None,
        "date": str(df.index[-1]),
    }


def scan_all() -> list[dict]:
    """Scan all supported symbols."""
    results = []
    for symbol in SUPPORTED_SYMBOLS:
        try:
            r = analyze_symbol(symbol)
            results.append(r)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e), "signal": "ERROR"})
    return results


def execute_trades(dry_run: bool = True) -> list[dict]:
    """Execute trades based on Conservative signals.
    
    For each symbol, compare target position vs current position and adjust.
    """
    actions = []
    positions = get_crypto_positions()
    pos_map = {p["symbol"]: p for p in positions}

    for symbol in SUPPORTED_SYMBOLS:
        try:
            analysis = analyze_symbol(symbol)
            if analysis.get("error"):
                actions.append({"symbol": symbol, "action": "skip", "reason": analysis["error"]})
                continue

            target_notional = analysis["target_notional"]
            # Cap at MAX_PER_COIN
            target_notional = min(target_notional, MAX_PER_COIN)

            current = pos_map.get(symbol, {})
            current_value = current.get("market_value", 0)

            # Check stop-loss
            if current and current.get("unrealized_plpc", 0) <= -STOP_LOSS_PCT:
                actions.append({
                    "symbol": symbol,
                    "action": "CLOSE (stop-loss)",
                    "current_value": current_value,
                    "pnl_pct": current["unrealized_plpc"],
                })
                if not dry_run:
                    close_crypto_position(symbol)
                continue

            diff = target_notional - current_value

            if abs(diff) < 50:  # Skip tiny adjustments
                actions.append({
                    "symbol": symbol,
                    "action": "hold",
                    "signal": analysis["signal"],
                    "current": current_value,
                    "target": target_notional,
                })
                continue

            if diff > 0:
                # Need to buy more
                actions.append({
                    "symbol": symbol,
                    "action": f"BUY ${diff:,.0f}",
                    "signal": analysis["signal"],
                    "count": analysis["bullish_count"],
                    "regime": analysis["regime"],
                    "current": current_value,
                    "target": target_notional,
                })
                if not dry_run:
                    place_crypto_order(symbol, "buy", notional=diff)
            else:
                # Need to reduce — sell the excess
                sell_amount = abs(diff)
                if current_value > 0:
                    sell_pct = sell_amount / current_value
                    sell_qty = current.get("qty", 0) * sell_pct
                    actions.append({
                        "symbol": symbol,
                        "action": f"SELL {sell_qty:.6f} (~${sell_amount:,.0f})",
                        "signal": analysis["signal"],
                        "count": analysis["bullish_count"],
                        "regime": analysis["regime"],
                        "current": current_value,
                        "target": target_notional,
                    })
                    if not dry_run:
                        if target_notional == 0:
                            close_crypto_position(symbol)
                        else:
                            place_crypto_order(symbol, "sell", qty=sell_qty)

        except Exception as e:
            actions.append({"symbol": symbol, "action": "error", "reason": str(e)})

    return actions


def format_scan_results(results: list[dict]) -> str:
    """Format scan results for display."""
    lines = [
        "=" * 55,
        "🪙 CRYPTO CONSERVATIVE SCAN",
        "=" * 55,
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"  ❓ {r['symbol']}: {r['error']}")
            continue

        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(r["signal"], "❓")
        lines.append(f"\n  {emoji} {r['symbol']} — {r['signal']}")
        lines.append(f"    Price:    ${r['price']:,.2f}")
        lines.append(f"    Bullish:  {r['bullish_count']}/4  |  Regime: {r['regime']}")
        lines.append(f"    Target:   {r['target_position_pct']*100:.0f}% (${r['target_notional']:,.0f})")
        if r.get("tsi") is not None:
            lines.append(f"    TSI: {r['tsi']}  WT: {r['wt1']}  SMA200: ${r['sma200']:,.0f}")
        if r.get("details"):
            inds = "  ".join(f"{k}:{v}" for k, v in r["details"].items())
            lines.append(f"    {inds}")

    return "\n".join(lines)


if __name__ == "__main__":
    results = scan_all()
    print(format_scan_results(results))
