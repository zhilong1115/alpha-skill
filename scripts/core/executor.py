"""Broker executor: Alpaca paper trading via alpaca-py."""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

# Load .env from project root
from pathlib import Path
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _get_client():
    """Create Alpaca trading client. Returns None if keys are missing."""
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key or api_key == "your_key_here":
        return None

    from alpaca.trading.client import TradingClient
    return TradingClient(api_key, secret_key, paper=True)


def get_account() -> Optional[dict]:
    """Get Alpaca account info.

    Returns:
        Dict with account details, or None if API keys not configured.
    """
    client = _get_client()
    if client is None:
        print("[executor] Alpaca API keys not configured. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
        return None

    account = client.get_account()
    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value),
        "status": account.status,
    }


def get_positions() -> list[dict]:
    """Get current positions from Alpaca.

    Returns:
        List of position dicts, or empty list if keys not configured.
    """
    client = _get_client()
    if client is None:
        print("[executor] Alpaca API keys not configured.")
        return []

    positions = client.get_all_positions()
    return [
        {
            "ticker": p.symbol,
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        }
        for p in positions
    ]


def place_order(
    ticker: str,
    side: str,
    qty: int,
    order_type: str = "market",
) -> Optional[dict]:
    """Place an order via Alpaca.

    Args:
        ticker: Stock ticker symbol.
        side: "buy" or "sell".
        qty: Number of shares.
        order_type: "market" or "limit".

    Returns:
        Order details dict, or None if keys not configured.
    """
    client = _get_client()
    if client is None:
        print("[executor] Alpaca API keys not configured. Cannot place orders.")
        return None

    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

    if order_type == "market":
        request = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
    else:
        raise ValueError(f"Order type '{order_type}' not yet supported. Use 'market'.")

    order = client.submit_order(request)
    result = {
        "id": str(order.id),
        "ticker": order.symbol,
        "side": order.side.value,
        "qty": str(order.qty),
        "type": order.type.value,
        "status": order.status.value,
        "filled_avg_price": None,
    }

    # Wait for fill and get actual fill price
    if order_type == "market":
        import time as _time
        for _ in range(10):  # Poll up to 5s
            _time.sleep(0.5)
            try:
                updated = client.get_order_by_id(str(order.id))
                if updated.status.value == "filled":
                    result["status"] = "filled"
                    result["filled_avg_price"] = float(updated.filled_avg_price)
                    result["filled_qty"] = str(updated.filled_qty)
                    break
            except Exception:
                pass

    return result


def get_order_fill_price(order_id: str) -> Optional[float]:
    """Get the filled average price for a completed order."""
    client = _get_client()
    if client is None:
        return None
    try:
        order = client.get_order_by_id(order_id)
        if order.filled_avg_price:
            return float(order.filled_avg_price)
    except Exception:
        pass
    return None
