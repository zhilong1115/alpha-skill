"""Swing portfolio tracker: track Zhilong's Robinhood positions and alert on signals."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PORTFOLIO_FILE = DATA_DIR / "swing_portfolio.json"


def _load_portfolio() -> dict:
    """Load swing portfolio."""
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except Exception:
            pass
    return {"positions": {}, "history": [], "updated": None}


def _save_portfolio(portfolio: dict) -> None:
    """Save swing portfolio."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    portfolio["updated"] = datetime.utcnow().isoformat()
    PORTFOLIO_FILE.write_text(json.dumps(portfolio, indent=2, default=str))


def add_position(ticker: str, qty: int, price: float,
                 stop_loss: float | None = None, target: float | None = None,
                 notes: str = "") -> dict:
    """Add or update a swing position.

    Args:
        ticker: Stock symbol.
        qty: Number of shares.
        price: Entry price.
        stop_loss: Optional stop-loss price.
        target: Optional target price.
        notes: Optional notes.

    Returns:
        Updated position dict.
    """
    portfolio = _load_portfolio()
    ticker = ticker.upper()

    if ticker in portfolio["positions"]:
        # Average in
        existing = portfolio["positions"][ticker]
        old_qty = existing["qty"]
        old_price = existing["entry_price"]
        new_qty = old_qty + qty
        avg_price = (old_qty * old_price + qty * price) / new_qty
        existing["qty"] = new_qty
        existing["entry_price"] = round(avg_price, 4)
        if stop_loss:
            existing["stop_loss"] = stop_loss
        if target:
            existing["target"] = target
        existing["updated"] = datetime.utcnow().isoformat()
        if notes:
            existing["notes"] = notes
        pos = existing
    else:
        pos = {
            "ticker": ticker,
            "qty": qty,
            "entry_price": price,
            "stop_loss": stop_loss,
            "target": target,
            "added": datetime.utcnow().isoformat(),
            "updated": datetime.utcnow().isoformat(),
            "notes": notes,
        }
        portfolio["positions"][ticker] = pos

    _save_portfolio(portfolio)
    logger.info("Added %d shares of %s @ $%.2f", qty, ticker, price)
    return pos


def remove_position(ticker: str, exit_price: float | None = None,
                    reason: str = "") -> dict | None:
    """Remove a position (sold).

    Args:
        ticker: Stock symbol.
        exit_price: Price sold at.
        reason: Why sold.

    Returns:
        Closed position summary, or None if not found.
    """
    portfolio = _load_portfolio()
    ticker = ticker.upper()

    pos = portfolio["positions"].pop(ticker, None)
    if pos is None:
        return None

    pnl = None
    pnl_pct = None
    if exit_price and pos.get("entry_price"):
        pnl = round((exit_price - pos["entry_price"]) * pos["qty"], 2)
        pnl_pct = round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)

    history_entry = {
        **pos,
        "exit_price": exit_price,
        "exit_date": datetime.utcnow().isoformat(),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
    }
    portfolio["history"].append(history_entry)
    _save_portfolio(portfolio)

    logger.info("Closed %s: P&L $%.2f (%.2f%%)", ticker, pnl or 0, pnl_pct or 0)
    return history_entry


def get_portfolio_status() -> dict:
    """Get current portfolio with live prices and P&L.

    Returns:
        Dict with positions, total_value, total_pnl, alerts.
    """
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", {})

    if not positions:
        return {"positions": [], "total_value": 0, "total_pnl": 0, "alerts": []}

    import yfinance as yf
    tickers = list(positions.keys())

    # Batch download current prices
    current_prices = {}
    try:
        data = yf.download(tickers, period="1d", progress=False)
        if not data.empty:
            close = data["Close"]
            for t in tickers:
                try:
                    if len(tickers) == 1:
                        current_prices[t] = float(close.dropna().iloc[-1])
                    elif t in close.columns:
                        current_prices[t] = float(close[t].dropna().iloc[-1])
                except Exception:
                    pass
    except Exception:
        pass

    result_positions = []
    total_value = 0
    total_cost = 0
    alerts = []

    for ticker, pos in positions.items():
        price = current_prices.get(ticker)
        entry = pos["entry_price"]
        qty = pos["qty"]

        pos_data = {
            "ticker": ticker,
            "qty": qty,
            "entry_price": entry,
            "current_price": price,
            "cost_basis": round(entry * qty, 2),
        }

        if price:
            pnl = (price - entry) * qty
            pnl_pct = (price - entry) / entry * 100
            pos_data["pnl"] = round(pnl, 2)
            pos_data["pnl_pct"] = round(pnl_pct, 2)
            pos_data["market_value"] = round(price * qty, 2)
            total_value += price * qty
            total_cost += entry * qty

            # Check alerts
            stop = pos.get("stop_loss")
            target = pos.get("target")

            if stop and price <= stop:
                alerts.append({
                    "ticker": ticker,
                    "type": "STOP_LOSS_HIT",
                    "message": f"🛑 {ticker} 触及止损! 现价 ${price:.2f} ≤ 止损 ${stop:.2f}",
                    "urgency": "critical",
                })
            elif stop and price <= stop * 1.02:
                alerts.append({
                    "ticker": ticker,
                    "type": "APPROACHING_STOP",
                    "message": f"⚠️ {ticker} 接近止损: 现价 ${price:.2f}, 止损 ${stop:.2f}",
                    "urgency": "warning",
                })

            if target and price >= target:
                alerts.append({
                    "ticker": ticker,
                    "type": "TARGET_HIT",
                    "message": f"🎯 {ticker} 到达目标价! 现价 ${price:.2f} ≥ 目标 ${target:.2f}",
                    "urgency": "critical",
                })
            elif target and price >= target * 0.97:
                alerts.append({
                    "ticker": ticker,
                    "type": "APPROACHING_TARGET",
                    "message": f"📈 {ticker} 接近目标: 现价 ${price:.2f}, 目标 ${target:.2f}",
                    "urgency": "info",
                })
        else:
            total_cost += entry * qty

        result_positions.append(pos_data)

    total_pnl = total_value - total_cost if total_value > 0 else 0

    return {
        "positions": sorted(result_positions, key=lambda x: x.get("pnl", 0), reverse=True),
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "position_count": len(positions),
        "alerts": alerts,
        "history_count": len(portfolio.get("history", [])),
    }


def format_status_message(status: dict) -> str:
    """Format portfolio status as Telegram message."""
    positions = status.get("positions", [])
    if not positions:
        return "📋 Swing组合为空 — 还没有持仓"

    lines = [
        f"📋 **Swing组合** ({status['position_count']}只持仓)",
        f"总市值: ${status['total_value']:,.2f} | P&L: ${status['total_pnl']:+,.2f} ({status['total_pnl_pct']:+.2f}%)",
        "",
    ]

    for p in positions:
        pnl_str = f"${p.get('pnl', 0):+.2f} ({p.get('pnl_pct', 0):+.1f}%)" if p.get("pnl") is not None else "N/A"
        icon = "🟢" if (p.get("pnl", 0) or 0) > 0 else "🔴" if (p.get("pnl", 0) or 0) < 0 else "⚪"
        lines.append(f"  {icon} {p['ticker']:<6} {p['qty']}股 @ ${p['entry_price']:.2f} → {pnl_str}")

    if status.get("alerts"):
        lines.append("")
        lines.append("⚠️ **警报:**")
        for a in status["alerts"]:
            lines.append(f"  {a['message']}")

    return "\n".join(lines)
