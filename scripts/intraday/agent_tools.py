"""Intraday Agent Tools — atomic functions for agent-driven day trading.

V3.1: Adds intraday journal for cross-cycle context persistence.
The agent (not a script) owns the decision loop: scan → decide → execute → verify → adapt.

Key principles:
1. Every function returns structured data — agent makes the decisions
2. Order execution always returns fill status — no fire-and-forget
3. P&L comes from Alpaca fills, not local snapshots
4. State is truth-synced with Alpaca, not tracked independently
5. Journal provides intraday memory across 15-min cycles

Usage by agent:
    1. read_journal() → get today's context (news, trades, observations)
    2. account() → get cash, equity, buying power
    3. positions() → get all Alpaca positions with real P&L
    4. scan() → get ranked candidates with signals
    5. Agent decides which to trade, how much
    6. buy(ticker, qty) → returns fill price, qty, order_id
    7. write_journal(entry) → record decision/observation for future cycles
    8. reconcile_pnl() → true P&L from Alpaca fills
"""

from __future__ import annotations

import json as _json
import logging
import time as _time
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
JOURNAL_DIR = DATA_DIR / "journal"


# ── Intraday Journal (cross-cycle memory) ──────────────────────────────────

def read_journal(date_str: str = "") -> list[dict]:
    """Read today's intraday journal — news alerts, trades, observations.

    The journal persists across 15-min cron cycles, giving the agent
    memory of what happened earlier in the trading day.

    Args:
        date_str: ISO date (default: today). Format: YYYY-MM-DD.

    Returns: List of journal entries sorted by timestamp.
    """
    if not date_str:
        date_str = date.today().isoformat()

    journal_file = JOURNAL_DIR / f"{date_str}.jsonl"
    if not journal_file.exists():
        return []

    entries = []
    for line in journal_file.read_text().strip().split("\n"):
        if line.strip():
            try:
                entries.append(_json.loads(line))
            except Exception:
                continue
    return entries


def write_journal(entry_type: str, content: dict) -> dict:
    """Append an entry to today's intraday journal.

    Use this to record trades, observations, and decisions so future
    cycles within the same trading day have context.

    Args:
        entry_type: One of "trade", "observation", "decision", "news", "alert"
        content: Dict with relevant details. Always include a human-readable "note".

    Returns: The written entry with timestamp.

    Example:
        write_journal("observation", {"note": "Market turning bullish after 10AM, SPY reclaiming VWAP"})
        write_journal("trade", {"note": "Bought 500 DOW@37.2, strong ORB breakout + volume confirmed"})
        write_journal("decision", {"note": "Avoiding NVDA today — spread too wide pre-10AM"})
    """
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    journal_file = JOURNAL_DIR / f"{date.today().isoformat()}.jsonl"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        **content,
    }

    with open(journal_file, "a") as f:
        f.write(_json.dumps(entry, default=str) + "\n")

    logger.info("📝 Journal [%s]: %s", entry_type, content.get("note", "")[:80])
    return entry


# ── Account & Positions ────────────────────────────────────────────────────

def account() -> dict:
    """Get Alpaca account summary.

    Returns: {equity, cash, buying_power, portfolio_value, status}
    """
    from scripts.core.executor import get_account
    return get_account() or {}


def positions() -> list[dict]:
    """Get all current Alpaca positions with real-time P&L.

    Returns: [{ticker, qty, avg_entry_price, current_price, market_value,
               unrealized_pl, unrealized_plpc}]
    """
    from scripts.core.executor import get_positions
    return get_positions()


def position(ticker: str) -> Optional[dict]:
    """Get a single position by ticker. Returns None if not held."""
    for p in positions():
        if p["ticker"] == ticker:
            return p
    return None


# ── Market Data ────────────────────────────────────────────────────────────

def price(ticker: str) -> Optional[float]:
    """Get latest price for a ticker."""
    try:
        import yfinance as yf
        data = yf.download(ticker, period="1d", interval="1m", progress=False)
        if not data.empty:
            close = data["Close"]
            if hasattr(close, 'columns'):
                close = close[ticker] if ticker in close.columns else close.iloc[:, 0]
            return float(close.dropna().iloc[-1])
    except Exception:
        pass
    return None


def prices(tickers: list[str]) -> dict[str, float]:
    """Get latest prices for multiple tickers."""
    result = {}
    for t in tickers:
        p = price(t)
        if p is not None:
            result[t] = p
    return result


def spread(ticker: str) -> dict:
    """Check bid-ask spread for a ticker.

    Returns: {bid, ask, spread_pct, acceptable}
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        bid = info.get("bid", 0)
        ask = info.get("ask", 0)
        if bid and ask and ask > bid:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid * 100
            return {
                "bid": bid, "ask": ask,
                "spread_pct": round(spread_pct, 3),
                "acceptable": spread_pct <= 0.5,
            }
    except Exception:
        pass
    return {"bid": 0, "ask": 0, "spread_pct": 0, "acceptable": True}


# ── Scanning & Signals ─────────────────────────────────────────────────────

def scan(top_n: int = 15) -> list[dict]:
    """Scan for intraday candidates — gaps, volume, catalysts.

    Returns ranked list of candidates with signals. Agent decides which to trade.
    """
    from scripts.intraday.scanner import get_intraday_candidates
    from scripts.intraday.signals import rank_candidates

    candidates = get_intraday_candidates(top_n=top_n)
    if not candidates:
        return []

    return rank_candidates(candidates)


def signals(ticker: str) -> dict:
    """Get intraday signals for a specific ticker.

    Returns: {signal_score, trade_direction, signals[], atr, vwap, ...}
    """
    from scripts.intraday.signals import compute_intraday_signals, get_intraday_data

    df = get_intraday_data(ticker)
    if df.empty:
        return {"error": f"No data for {ticker}"}

    return compute_intraday_signals(ticker, df)


def news_alerts() -> list[dict]:
    """Get pending news alerts."""
    from scripts.intraday.scanner import scan_news_catalysts
    return scan_news_catalysts()


def market_regime() -> dict:
    """Get VIX level and market regime info.

    Returns: {vix, spy_direction, size_multiplier}
    """
    from scripts.intraday import risk as risk_mgr
    return risk_mgr.get_market_regime()


# ── Order Execution ────────────────────────────────────────────────────────

def buy(ticker: str, qty: int) -> dict:
    """Place a market buy order and wait for fill.

    Returns: {order_id, status, filled_qty, filled_avg_price, error?}
    """
    return _place_and_wait(ticker, "buy", qty)


def sell(ticker: str, qty: int) -> dict:
    """Place a market sell order and wait for fill.

    Returns: {order_id, status, filled_qty, filled_avg_price, error?}
    """
    return _place_and_wait(ticker, "sell", qty)


def close(ticker: str) -> dict:
    """Close entire position for a ticker using Alpaca's close_position API.

    More reliable than sell(qty) — handles fractional shares, exact qty.
    Returns: {status, filled_avg_price, filled_qty, pnl?, error?}
    """
    from scripts.core.executor import _get_client

    client = _get_client()
    if not client:
        return {"status": "error", "error": "Alpaca client not configured"}

    # Get position before closing for P&L calc
    pos = position(ticker)
    if not pos:
        return {"status": "error", "error": f"No position in {ticker}"}

    try:
        order = client.close_position(ticker)
        order_id = str(order.id) if hasattr(order, 'id') else str(order)
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Wait for fill
    result = _wait_for_fill(client, order_id)
    result["entry_price"] = pos["avg_entry_price"]
    result["qty"] = pos["qty"]

    if result.get("filled_avg_price"):
        pnl = (result["filled_avg_price"] - pos["avg_entry_price"]) * pos["qty"]
        result["pnl"] = round(pnl, 2)
        result["pnl_pct"] = round((result["filled_avg_price"] - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100, 2)

    return result


def close_all() -> list[dict]:
    """Close all open positions. Returns list of close results."""
    results = []
    for p in positions():
        if p["qty"] > 0:
            result = close(p["ticker"])
            result["ticker"] = p["ticker"]
            results.append(result)
    return results


def _place_and_wait(ticker: str, side: str, qty: int, timeout_s: float = 5.0) -> dict:
    """Place market order and poll for fill."""
    from scripts.core.executor import _get_client
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = _get_client()
    if not client:
        return {"status": "error", "error": "Alpaca client not configured"}

    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
    request = MarketOrderRequest(
        symbol=ticker, qty=qty, side=order_side, time_in_force=TimeInForce.DAY,
    )

    try:
        order = client.submit_order(request)
    except Exception as e:
        return {"status": "error", "error": str(e)}

    order_id = str(order.id)
    result = _wait_for_fill(client, order_id, timeout_s)
    result["order_id"] = order_id
    result["ticker"] = ticker
    result["side"] = side
    result["requested_qty"] = qty
    return result


def _wait_for_fill(client, order_id: str, timeout_s: float = 5.0) -> dict:
    """Poll Alpaca for order fill status."""
    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        _time.sleep(0.5)
        try:
            o = client.get_order_by_id(order_id)
            if o.status.value == "filled":
                return {
                    "status": "filled",
                    "filled_qty": float(o.filled_qty),
                    "filled_avg_price": float(o.filled_avg_price),
                }
            elif o.status.value in ("canceled", "expired", "rejected"):
                return {"status": o.status.value, "error": f"Order {o.status.value}"}
        except Exception:
            pass

    return {"status": "pending", "error": "Fill timeout — check manually"}


# ── Order History (Reconciliation) ─────────────────────────────────────────

def orders_today() -> list[dict]:
    """Get all of today's filled orders from Alpaca — the source of truth for P&L.

    Returns: [{ticker, side, qty, filled_avg_price, filled_at}]
    """
    from scripts.core.executor import _get_client
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    client = _get_client()
    if not client:
        return []

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    req = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=today,
        limit=100,
    )

    try:
        orders = client.get_orders(req)
    except Exception as e:
        logger.error("Failed to fetch orders: %s", e)
        return []

    result = []
    for o in sorted(orders, key=lambda x: x.filled_at or x.created_at):
        if o.status.value != "filled":
            continue
        result.append({
            "ticker": o.symbol,
            "side": o.side.value,
            "qty": float(o.filled_qty),
            "filled_avg_price": float(o.filled_avg_price),
            "filled_at": str(o.filled_at),
            "order_id": str(o.id),
        })

    return result


def reconcile_pnl() -> dict:
    """Calculate true P&L from today's Alpaca fills.

    Groups buys and sells per symbol, computes actual realized P&L.
    This is the source of truth — ignores local state entirely.

    Returns: {total_pnl, per_symbol: {ticker: {bought, sold, pnl}}, trade_count}
    """
    from collections import defaultdict

    orders = orders_today()
    buys = defaultdict(lambda: {"qty": 0, "cost": 0})
    sells = defaultdict(lambda: {"qty": 0, "proceeds": 0})

    for o in orders:
        if o["side"] == "buy":
            buys[o["ticker"]]["qty"] += o["qty"]
            buys[o["ticker"]]["cost"] += o["qty"] * o["filled_avg_price"]
        else:
            sells[o["ticker"]]["qty"] += o["qty"]
            sells[o["ticker"]]["proceeds"] += o["qty"] * o["filled_avg_price"]

    all_tickers = set(list(buys.keys()) + list(sells.keys()))
    per_symbol = {}
    total_pnl = 0

    for ticker in sorted(all_tickers):
        b = buys[ticker]
        s = sells[ticker]
        pnl = s["proceeds"] - b["cost"]
        per_symbol[ticker] = {
            "bought": round(b["cost"], 2),
            "sold": round(s["proceeds"], 2),
            "buy_qty": b["qty"],
            "sell_qty": s["qty"],
            "pnl": round(pnl, 2),
            "closed": abs(b["qty"] - s["qty"]) < 0.01,  # fully closed
        }
        total_pnl += pnl

    return {
        "date": date.today().isoformat(),
        "total_pnl": round(total_pnl, 2),
        "per_symbol": per_symbol,
        "trade_count": len(orders),
    }


# ── Risk Sizing (advisory — agent makes final call) ───────────────────────

def suggest_size(ticker: str, current_price: float, portfolio_value: float,
                 atr: float = 0.0) -> dict:
    """Suggest position size, stop, target based on risk rules.

    This is advisory — agent can adjust. Returns all the math for transparency.

    Returns: {qty, stop_price, target_price, stop_distance, position_value,
              risk_per_share, time_weight, notes[]}
    """
    from scripts.intraday import risk as risk_mgr

    notes = []

    # ATR-based or fallback
    if atr > 0:
        stop_distance = 1.5 * atr
        target_distance = 3.0 * atr
        notes.append(f"ATR-based: stop={1.5}×ATR, target={3.0}×ATR")
    else:
        stop_distance = current_price * 0.02
        target_distance = current_price * 0.04
        notes.append("Fallback: 2% stop, 4% target (no ATR)")

    # Floor/cap
    min_stop = current_price * 0.005
    max_stop = current_price * 0.05
    if stop_distance < min_stop:
        stop_distance = min_stop
        target_distance = stop_distance * 2
        notes.append(f"Stop floored to 0.5%")
    elif stop_distance > max_stop:
        stop_distance = max_stop
        target_distance = stop_distance * 2
        notes.append(f"Stop capped at 5%")

    stop_price = round(current_price - stop_distance, 2)
    target_price = round(current_price + target_distance, 2)

    # Size
    max_value = portfolio_value * 0.25  # 25% default suggestion — agent can override
    qty = int(max_value / current_price) if current_price > 0 else 0

    # Time weight
    tw = risk_mgr.get_time_weight()
    if tw > 0:
        qty = int(qty * tw)
    else:
        qty = 0
        notes.append("Time window closed — no entries")

    return {
        "qty": qty,
        "stop_price": stop_price,
        "target_price": target_price,
        "stop_distance": round(stop_distance, 3),
        "target_distance": round(target_distance, 3),
        "position_value": round(qty * current_price, 2),
        "risk_per_share": round(stop_distance, 3),
        "time_weight": tw,
        "rr_ratio": round(target_distance / stop_distance, 1) if stop_distance > 0 else 0,
        "notes": notes,
    }
