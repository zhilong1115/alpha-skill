#!/usr/bin/env python3
"""Crypto position monitoring with MA120 hard stop check."""
from __future__ import annotations

from datetime import datetime, timedelta
import pandas as pd

from scripts.crypto.alpaca_crypto import (
    get_crypto_positions, get_crypto_exposure, get_crypto_buying_power,
    get_crypto_bars, close_crypto_position, CRYPTO_ALLOCATION, SUPPORTED_SYMBOLS,
)
from alpaca.data.timeframe import TimeFrame

STOP_LOSS_PCT = 0.05


def check_ma120_4h(symbol: str) -> dict:
    """Check if price is below 4H MA120 (hard stop)."""
    start = datetime.now() - timedelta(days=30)
    df = get_crypto_bars(symbol, TimeFrame.Hour, start)
    if df.empty or len(df) < 120:
        return {"symbol": symbol, "ma120": None, "below": False, "error": "Insufficient 4H data"}

    # Resample to 4H if we got hourly
    df_4h = df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()

    if len(df_4h) < 120:
        return {"symbol": symbol, "ma120": None, "below": False, "note": f"Only {len(df_4h)} 4H bars"}

    ma120 = df_4h["close"].rolling(120).mean().iloc[-1]
    current = df_4h["close"].iloc[-1]

    return {
        "symbol": symbol,
        "current_price": current,
        "ma120_4h": round(ma120, 2),
        "below": current < ma120,
        "distance_pct": round((current - ma120) / ma120 * 100, 2),
    }


def get_portfolio_status() -> dict:
    """Full crypto portfolio status."""
    positions = get_crypto_positions()
    exposure = get_crypto_exposure()
    buying_power = get_crypto_buying_power()

    alerts = []

    # Check stop-losses and MA120
    for pos in positions:
        sym = pos["symbol"]

        # 5% stop-loss check
        if pos["unrealized_plpc"] <= -STOP_LOSS_PCT:
            alerts.append({
                "symbol": sym,
                "type": "STOP_LOSS",
                "message": f"⚠️ {sym} down {pos['unrealized_plpc']*100:.1f}% — stop-loss triggered!",
            })

        # MA120 check
        try:
            ma_check = check_ma120_4h(sym)
            pos["ma120_4h"] = ma_check.get("ma120_4h")
            pos["below_ma120"] = ma_check.get("below", False)
            if ma_check.get("below"):
                alerts.append({
                    "symbol": sym,
                    "type": "MA120_STOP",
                    "message": f"🔴 {sym} below 4H MA120 (${ma_check['ma120_4h']:,.2f}) — HARD STOP!",
                })
        except Exception:
            pass

    total_pnl = sum(p["unrealized_pl"] for p in positions)

    return {
        "positions": positions,
        "total_exposure": exposure,
        "allocation_used_pct": (exposure / CRYPTO_ALLOCATION * 100) if CRYPTO_ALLOCATION > 0 else 0,
        "buying_power": buying_power,
        "total_unrealized_pnl": total_pnl,
        "alerts": alerts,
        "timestamp": datetime.now().isoformat(),
    }


def format_status(status: dict) -> str:
    """Format portfolio status for display."""
    lines = [
        "=" * 55,
        "🪙 CRYPTO PORTFOLIO STATUS",
        "=" * 55,
        f"  Allocation: ${status['total_exposure']:,.2f} / ${CRYPTO_ALLOCATION:,.2f} ({status['allocation_used_pct']:.1f}%)",
        f"  Buying Power: ${status['buying_power']:,.2f}",
        f"  Unrealized P&L: ${status['total_unrealized_pnl']:,.2f}",
    ]

    if status["positions"]:
        lines.append(f"\n  Positions ({len(status['positions'])}):")
        for p in status["positions"]:
            icon = "🟢" if p["unrealized_pl"] >= 0 else "🔴"
            ma_flag = " ⚠️MA120" if p.get("below_ma120") else ""
            lines.append(
                f"    {icon} {p['symbol']:<8} {p['qty']:.6f} "
                f"@ ${p['avg_entry']:,.2f} → ${p['current_price']:,.2f} "
                f"P&L: ${p['unrealized_pl']:,.2f} ({p['unrealized_plpc']*100:+.1f}%){ma_flag}"
            )
    else:
        lines.append("\n  No crypto positions.")

    if status["alerts"]:
        lines.append(f"\n  ⚠️ ALERTS ({len(status['alerts'])}):")
        for a in status["alerts"]:
            lines.append(f"    {a['message']}")

    return "\n".join(lines)


def auto_stop_check(execute: bool = False) -> list[dict]:
    """Check all positions for stop conditions and optionally close them."""
    actions = []
    status = get_portfolio_status()

    for alert in status["alerts"]:
        if alert["type"] in ("STOP_LOSS", "MA120_STOP"):
            sym = alert["symbol"]
            actions.append({"symbol": sym, "reason": alert["type"], "message": alert["message"]})
            if execute:
                close_crypto_position(sym)

    return actions


if __name__ == "__main__":
    status = get_portfolio_status()
    print(format_status(status))
