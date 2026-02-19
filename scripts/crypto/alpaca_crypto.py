#!/usr/bin/env python3
"""Alpaca crypto trading integration.

Connects to Alpaca paper trading for BTC/USD, ETH/USD, SOL/USD.
Hard cap: $50K total crypto allocation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

CRYPTO_ALLOCATION = 50_000.0  # Hard cap for crypto
MAX_PER_COIN = 25_000.0       # 50% of allocation
SUPPORTED_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame


def _get_trading_client() -> TradingClient:
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def _get_data_client() -> CryptoHistoricalDataClient:
    return CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_account_info() -> dict:
    """Get account info."""
    client = _get_trading_client()
    acct = client.get_account()
    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "portfolio_value": float(acct.portfolio_value),
    }


def get_crypto_positions() -> list[dict]:
    """Get current crypto positions only."""
    client = _get_trading_client()
    positions = client.get_all_positions()
    crypto_pos = []
    for p in positions:
        sym = p.symbol
        # Alpaca returns crypto symbols like BTCUSD or BTC/USD
        normalized = sym.replace("USD", "/USD") if "/" not in sym else sym
        if normalized in SUPPORTED_SYMBOLS or sym.replace("/", "") + "/USD" in [s.replace("/", "") + "/USD" for s in SUPPORTED_SYMBOLS]:
            crypto_pos.append({
                "symbol": normalized if normalized in SUPPORTED_SYMBOLS else sym,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            })
    return crypto_pos


def get_crypto_exposure() -> float:
    """Total market value of all crypto positions."""
    return sum(p["market_value"] for p in get_crypto_positions())


def get_crypto_buying_power() -> float:
    """How much more we can allocate to crypto (respecting $50K cap)."""
    exposure = get_crypto_exposure()
    remaining = CRYPTO_ALLOCATION - exposure
    # Also check actual account cash
    acct = get_account_info()
    return min(remaining, float(acct["cash"]))


def place_crypto_order(symbol: str, side: str, notional: float = None, qty: float = None) -> dict | None:
    """Place a crypto order. Specify notional (USD amount) or qty.
    
    Enforces hard caps before placing.
    """
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}. Must be one of {SUPPORTED_SYMBOLS}")

    client = _get_trading_client()

    if side == "buy":
        # Check allocation cap
        exposure = get_crypto_exposure()
        # Get current position for this symbol
        positions = get_crypto_positions()
        current_pos_value = sum(p["market_value"] for p in positions if p["symbol"] == symbol)

        if notional:
            if exposure + notional > CRYPTO_ALLOCATION:
                allowed = CRYPTO_ALLOCATION - exposure
                if allowed <= 0:
                    print(f"❌ Crypto allocation cap reached (${exposure:,.0f} / ${CRYPTO_ALLOCATION:,.0f})")
                    return None
                print(f"⚠️ Reducing order from ${notional:,.0f} to ${allowed:,.0f} (cap)")
                notional = allowed

            if current_pos_value + notional > MAX_PER_COIN:
                allowed = MAX_PER_COIN - current_pos_value
                if allowed <= 0:
                    print(f"❌ Max per coin cap reached for {symbol} (${current_pos_value:,.0f} / ${MAX_PER_COIN:,.0f})")
                    return None
                print(f"⚠️ Reducing order from ${notional:,.0f} to ${allowed:,.0f} (per-coin cap)")
                notional = allowed

    order_data = {
        "symbol": symbol.replace("/", ""),  # Alpaca wants BTCUSD
        "side": OrderSide.BUY if side == "buy" else OrderSide.SELL,
        "time_in_force": TimeInForce.GTC,
    }
    if notional:
        order_data["notional"] = round(notional, 2)
    elif qty:
        order_data["qty"] = qty
    else:
        raise ValueError("Must specify notional or qty")

    req = MarketOrderRequest(**order_data)
    order = client.submit_order(req)

    result = {
        "id": str(order.id),
        "symbol": symbol,
        "side": side,
        "status": str(order.status),
        "notional": notional,
        "qty": qty,
        "created_at": str(order.created_at),
    }

    # Log trade
    _log_trade(result)
    return result


def close_crypto_position(symbol: str) -> dict | None:
    """Close entire position for a crypto symbol."""
    client = _get_trading_client()
    alpaca_symbol = symbol.replace("/", "")
    try:
        order = client.close_position(alpaca_symbol)
        result = {
            "id": str(order.id),
            "symbol": symbol,
            "side": "sell",
            "status": str(order.status),
            "created_at": str(order.created_at),
        }
        _log_trade(result)
        return result
    except Exception as e:
        print(f"❌ Failed to close {symbol}: {e}")
        return None


def get_crypto_bars(symbol: str, timeframe: TimeFrame, start: datetime, end: datetime = None):
    """Fetch crypto OHLCV bars from Alpaca."""
    import pandas as pd
    client = _get_data_client()
    req = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    bars = client.get_crypto_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()

    # Reset multi-index (symbol, timestamp) → just timestamp
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=0, drop=True)

    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated()]
    df.index.name = "timestamp"
    return df


def _log_trade(trade: dict):
    """Append trade to data/crypto_trades.json."""
    log_path = _PROJECT_ROOT / "data" / "crypto_trades.json"
    trades = []
    if log_path.exists():
        try:
            trades = json.loads(log_path.read_text())
        except (json.JSONDecodeError, Exception):
            trades = []
    trade["logged_at"] = datetime.now().isoformat()
    trades.append(trade)
    log_path.write_text(json.dumps(trades, indent=2, default=str))


if __name__ == "__main__":
    print("Testing Alpaca crypto connection...")
    info = get_account_info()
    print(f"Account equity: ${info['equity']:,.2f}")
    print(f"Cash: ${info['cash']:,.2f}")
    print(f"Crypto positions: {get_crypto_positions()}")
    print(f"Crypto exposure: ${get_crypto_exposure():,.2f}")
    print(f"Crypto buying power: ${get_crypto_buying_power():,.2f}")
