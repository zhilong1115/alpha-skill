#!/usr/bin/env python3
"""Crypto monitor daemon — lightweight, zero-LLM-token signal checker.

Runs as a cron job (systemEvent). Computes indicators, checks for:
1. Stop-loss triggers (5% loss)
2. Buy/sell signal changes
3. Proximity to key thresholds (alert mode)

Outputs a one-line status or actionable alert. No LLM needed.
"""
from __future__ import annotations

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Setup
PT = ZoneInfo("America/Los_Angeles")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CRYPTO_STATE_FILE = DATA_DIR / "crypto_monitor_state.json"

# Add crypto skill to path FIRST (before any imports that need it)
_SKILL_SCRIPTS = "/Users/zhilongzheng/Projects/alpha-crypto-skill/scripts"
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

# Add project root to path
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.crypto.crypto_trader import analyze_symbol, scan_all, execute_trades
from scripts.crypto.alpaca_crypto import (
    get_crypto_positions, SUPPORTED_SYMBOLS,
)


# Thresholds for "alert mode" (indicators approaching flip)
TSI_ALERT_ZONE = 10      # within ±10 of the ±40 threshold
WT_ALERT_ZONE = 15       # within ±15 of the ±60 threshold
STOP_LOSS_PCT = -0.05    # -5%


def load_state() -> dict:
    """Load previous monitor state."""
    if CRYPTO_STATE_FILE.exists():
        try:
            return json.loads(CRYPTO_STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_signals": {}, "alert_mode": False, "last_check": None}


def save_state(state: dict) -> None:
    """Save monitor state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CRYPTO_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def check_proximity_to_thresholds(analysis: dict) -> list[str]:
    """Check if indicators are near flip thresholds."""
    alerts = []
    symbol = analysis["symbol"]
    tsi = analysis.get("tsi")
    wt1 = analysis.get("wt1")
    
    if tsi is not None:
        # TSI approaching buy threshold (-40) from below
        if -40 - TSI_ALERT_ZONE <= tsi <= -40 + TSI_ALERT_ZONE:
            alerts.append(f"{symbol} TSI={tsi:.1f} 接近买入阈值(-40)")
        # TSI approaching sell threshold (+40) from below
        if 40 - TSI_ALERT_ZONE <= tsi <= 40 + TSI_ALERT_ZONE:
            alerts.append(f"{symbol} TSI={tsi:.1f} 接近卖出阈值(+40)")
    
    if wt1 is not None:
        if -60 - WT_ALERT_ZONE <= wt1 <= -60 + WT_ALERT_ZONE:
            alerts.append(f"{symbol} WT={wt1:.1f} 接近超卖区(-60)")
        if 60 - WT_ALERT_ZONE <= wt1 <= 60 + WT_ALERT_ZONE:
            alerts.append(f"{symbol} WT={wt1:.1f} 接近超买区(+60)")
    
    return alerts


def run_check(auto_execute: bool = True) -> dict:
    """Run a full crypto check cycle.
    
    Args:
        auto_execute: If True, execute trades when signals trigger.
    
    Returns:
        Dict with status, alerts, actions taken.
    """
    state = load_state()
    prev_signals = state.get("last_signals", {})
    now = datetime.now(PT)
    
    result = {
        "timestamp": now.isoformat(),
        "status": "ok",
        "signal_changes": [],
        "stop_loss_alerts": [],
        "proximity_alerts": [],
        "actions": [],
        "alert_mode": False,
    }
    
    # 1. Check positions for stop-loss
    positions = get_crypto_positions()
    for pos in positions:
        pnl_pct = pos.get("unrealized_plpc", 0)
        if pnl_pct <= STOP_LOSS_PCT:
            result["stop_loss_alerts"].append(
                f"🚨 {pos['symbol']} 触发止损! P&L={pnl_pct*100:.1f}%"
            )
            result["status"] = "STOP_LOSS"
    
    # 2. Scan all symbols for signal changes
    analyses = scan_all()
    new_signals = {}
    
    for a in analyses:
        symbol = a["symbol"]
        signal = a.get("signal", "ERROR")
        new_signals[symbol] = signal
        
        # Check for signal change
        prev = prev_signals.get(symbol)
        if prev and prev != signal:
            result["signal_changes"].append(
                f"📊 {symbol}: {prev} → {signal} (TSI={a.get('tsi')}, WT={a.get('wt1')})"
            )
        
        # Check proximity to thresholds
        if a.get("tsi") is not None:
            alerts = check_proximity_to_thresholds(a)
            result["proximity_alerts"].extend(alerts)
    
    # 3. Determine alert mode
    result["alert_mode"] = bool(result["proximity_alerts"]) or bool(result["signal_changes"])
    
    # 4. Auto-execute if signals changed and auto_execute is on
    if auto_execute and (result["signal_changes"] or result["stop_loss_alerts"]):
        actions = execute_trades(dry_run=False)
        result["actions"] = actions
    
    # 5. Save state
    state["last_signals"] = new_signals
    state["alert_mode"] = result["alert_mode"]
    state["last_check"] = now.isoformat()
    save_state(state)
    
    return result


def format_report(result: dict) -> str:
    """Format check result as a compact string for cron output."""
    lines = []
    
    if result["stop_loss_alerts"]:
        lines.extend(result["stop_loss_alerts"])
    
    if result["signal_changes"]:
        lines.append("⚡ 信号变化:")
        lines.extend(result["signal_changes"])
    
    if result["proximity_alerts"]:
        lines.append("⚠️ 接近关键位:")
        lines.extend(result["proximity_alerts"])
    
    if result["actions"]:
        lines.append("🔄 已执行:")
        for a in result["actions"]:
            lines.append(f"  {a.get('symbol')}: {a.get('action')}")
    
    if not lines:
        return "CRYPTO_OK"  # Nothing to report
    
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't execute trades")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()
    
    result = run_check(auto_execute=not args.dry_run)
    
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_report(result))
