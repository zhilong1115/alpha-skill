"""Alert detection system for portfolio monitoring."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd


def check_drawdown_alerts(
    positions: list[dict],
    portfolio_value: float,
    daily_pnl: float,
) -> list[dict]:
    """Check for drawdown-related alerts.

    Args:
        positions: List of position dicts.
        portfolio_value: Total portfolio value.
        daily_pnl: Today's P&L in dollars.

    Returns:
        List of alert dicts.
    """
    alerts = []
    now = datetime.utcnow().isoformat()

    if portfolio_value > 0:
        daily_drawdown_pct = abs(daily_pnl) / portfolio_value * 100 if daily_pnl < 0 else 0

        if daily_drawdown_pct > 2:
            severity = "critical" if daily_drawdown_pct > 5 else "warning"
            alerts.append({
                "type": "drawdown",
                "ticker": "PORTFOLIO",
                "severity": severity,
                "message": f"Daily drawdown: {daily_drawdown_pct:.1f}% (${daily_pnl:,.2f})",
                "timestamp": now,
            })

        # Check total unrealized drawdown
        total_unrealized = sum(
            float(p.get("unrealized_plpc", 0)) for p in positions
        )
        total_drawdown_pct = abs(total_unrealized) * 100 if total_unrealized < 0 else 0

        if total_drawdown_pct > 10:
            alerts.append({
                "type": "drawdown",
                "ticker": "PORTFOLIO",
                "severity": "critical",
                "message": f"Total portfolio drawdown: {total_drawdown_pct:.1f}%",
                "timestamp": now,
            })

    return alerts


def check_stop_loss_alerts(positions: list[dict]) -> list[dict]:
    """Check for positions that should trigger stop losses.

    Args:
        positions: List of position dicts with avg_entry_price, current_price, etc.

    Returns:
        List of alert dicts.
    """
    alerts = []
    now = datetime.utcnow().isoformat()

    for pos in positions:
        try:
            ticker = pos.get("symbol", "UNKNOWN")
            entry = float(pos.get("avg_entry_price", 0))
            current = float(pos.get("current_price", 0))
            highest = float(pos.get("highest_price", current))

            if highest <= 0:
                continue

            drawdown_from_high = (highest - current) / highest * 100

            if drawdown_from_high > 8:
                severity = "critical" if drawdown_from_high > 15 else "warning"
                alerts.append({
                    "type": "stop_loss",
                    "ticker": ticker,
                    "severity": severity,
                    "message": (
                        f"{ticker} down {drawdown_from_high:.1f}% from high "
                        f"(${highest:.2f} → ${current:.2f})"
                    ),
                    "timestamp": now,
                })
        except (ValueError, TypeError):
            continue

    return alerts


def check_signal_alerts(
    signals_df: pd.DataFrame, threshold: float = 0.7
) -> list[dict]:
    """Check for strong trading signals that warrant attention.

    Args:
        signals_df: DataFrame with columns: ticker, signal_name, value, score.
        threshold: Minimum absolute score to trigger an alert.

    Returns:
        List of alert dicts.
    """
    alerts = []
    now = datetime.utcnow().isoformat()

    if signals_df is None or signals_df.empty:
        return alerts

    strong = signals_df[signals_df["score"].abs() > threshold]
    for _, row in strong.iterrows():
        direction = "BULLISH" if row["score"] > 0 else "BEARISH"
        alerts.append({
            "type": "strong_signal",
            "ticker": row["ticker"],
            "severity": "warning",
            "message": (
                f"Strong {direction} signal: {row['signal_name']} "
                f"(score={row['score']:+.3f})"
            ),
            "timestamp": now,
        })

    return alerts


def format_alert(alert: dict) -> str:
    """Format an alert dict into a human-readable string for notifications.

    Args:
        alert: Alert dict with type, ticker, severity, message, timestamp.

    Returns:
        Formatted alert string.
    """
    icon = "🔴" if alert["severity"] == "critical" else "⚠️"
    type_label = alert["type"].upper().replace("_", " ")
    return f"{icon} [{type_label}] {alert['ticker']}: {alert['message']}"
