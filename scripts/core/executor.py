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
    return {
        "id": str(order.id),
        "ticker": order.symbol,
        "side": order.side.value,
        "qty": str(order.qty),
        "type": order.type.value,
        "status": order.status.value,
    }
