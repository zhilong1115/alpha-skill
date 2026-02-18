"""Portfolio tracker: display current positions and P&L."""

from __future__ import annotations

from typing import Optional

from scripts.core.executor import get_account, get_positions


def format_portfolio() -> str:
    """Format current portfolio as a readable table string.

    Returns:
        Formatted string with positions and P&L summary.
    """
    account = get_account()
    positions = get_positions()

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("PORTFOLIO SUMMARY")
    lines.append("=" * 70)

    if account:
        lines.append(f"  Equity:        ${account['equity']:>12,.2f}")
        lines.append(f"  Cash:          ${account['cash']:>12,.2f}")
        lines.append(f"  Buying Power:  ${account['buying_power']:>12,.2f}")
        lines.append(f"  Status:        {account['status']}")
    else:
        lines.append("  ⚠ Alpaca not connected. Configure API keys in .env")

    lines.append("")
    lines.append("-" * 70)

    if not positions:
        lines.append("  No open positions.")
    else:
        header = f"  {'Ticker':<8} {'Qty':>6} {'Avg Entry':>10} {'Current':>10} {'P&L':>12} {'P&L%':>8}"
        lines.append(header)
        lines.append("  " + "-" * 64)

        total_pl = 0.0
        for p in positions:
            pl_str = f"${p['unrealized_pl']:>+10,.2f}"
            pct_str = f"{p['unrealized_plpc']*100:>+6.2f}%"
            lines.append(
                f"  {p['ticker']:<8} {p['qty']:>6.0f} "
                f"${p['avg_entry_price']:>9,.2f} ${p['current_price']:>9,.2f} "
                f"{pl_str} {pct_str}"
            )
            total_pl += p["unrealized_pl"]

        lines.append("  " + "-" * 64)
        lines.append(f"  Total Unrealized P&L: ${total_pl:>+,.2f}")

    lines.append("=" * 70)
    return "\n".join(lines)
