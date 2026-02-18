"""Report generator: daily summary in markdown format."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd


def generate_daily_report(
    positions: list[dict],
    signals: pd.DataFrame,
    trades: list[dict],
) -> str:
    """Generate a daily summary report in markdown format.

    Args:
        positions: List of position dicts (from executor.get_positions()).
        signals: DataFrame of signals (from signal_engine.compute_signals()).
        trades: List of trade dicts executed today.

    Returns:
        Markdown-formatted report string.
    """
    now = datetime.now()
    lines: list[str] = []

    lines.append(f"# Daily Trading Report — {now.strftime('%Y-%m-%d')}")
    lines.append("")

    # Portfolio Summary
    lines.append("## Portfolio Summary")
    if positions:
        total_value = sum(p.get("market_value", 0) for p in positions)
        total_pl = sum(p.get("unrealized_pl", 0) for p in positions)
        lines.append(f"- **Positions**: {len(positions)}")
        lines.append(f"- **Total Value**: ${total_value:,.2f}")
        lines.append(f"- **Unrealized P&L**: ${total_pl:+,.2f}")
        lines.append("")
        lines.append("| Ticker | Qty | Entry | Current | P&L |")
        lines.append("|--------|-----|-------|---------|-----|")
        for p in positions:
            lines.append(
                f"| {p.get('ticker', 'N/A')} | {p.get('qty', 0):.0f} | "
                f"${p.get('avg_entry_price', 0):.2f} | ${p.get('current_price', 0):.2f} | "
                f"${p.get('unrealized_pl', 0):+.2f} |"
            )
    else:
        lines.append("No open positions.")
    lines.append("")

    # Trades Today
    lines.append("## Trades Executed")
    if trades:
        lines.append("| Ticker | Side | Qty | Type | Status |")
        lines.append("|--------|------|-----|------|--------|")
        for t in trades:
            lines.append(
                f"| {t.get('ticker', 'N/A')} | {t.get('side', 'N/A')} | "
                f"{t.get('qty', 'N/A')} | {t.get('type', 'N/A')} | {t.get('status', 'N/A')} |"
            )
    else:
        lines.append("No trades executed today.")
    lines.append("")

    # Signals
    lines.append("## Active Signals")
    if signals is not None and not signals.empty:
        top = signals.sort_values("score", ascending=False).head(10)
        lines.append("| Ticker | Signal | Score |")
        lines.append("|--------|--------|-------|")
        for _, row in top.iterrows():
            lines.append(f"| {row['ticker']} | {row['signal_name']} | {row['score']:+.2f} |")
    else:
        lines.append("No signals generated.")
    lines.append("")

    lines.append(f"*Generated at {now.strftime('%H:%M:%S %Z')}*")
    return "\n".join(lines)
