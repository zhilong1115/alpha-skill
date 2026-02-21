#!/usr/bin/env python3
"""Hyperliquid DEX trading integration.

Connects to Hyperliquid perpetual futures via the official Python SDK.
Supports BTC, ETH, SOL perps with max 3x leverage.

Private key loaded from macOS Keychain (fallback: env var).
Default: testnet. Mainnet requires explicit flag.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

PT = ZoneInfo("America/Los_Angeles")
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADE_LOG = _PROJECT_ROOT / "data" / "hl_trades.json"

# Hard limits
MAX_LEVERAGE = 3
SUPPORTED_SYMBOLS = ["BTC", "ETH", "SOL"]
MAX_DRAWDOWN = 0.15  # 15% circuit breaker
MAX_POSITION_MARGIN_PCT = 0.30  # 30% of account as margin per position

# Module-level state
_info: Info | None = None
_exchange: Exchange | None = None
_account_address: str | None = None  # API wallet address (for signing)
_master_address: str | None = None   # Main account address (for queries)
_is_testnet: bool = True


def _get_private_key() -> str:
    """Load private key from macOS Keychain, fallback to env var."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "hyperliquid-private-key", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            key = result.stdout.strip()
            logger.info("Private key loaded from macOS Keychain")
            return key
    except Exception as e:
        logger.debug(f"Keychain lookup failed: {e}")

    key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    if key:
        logger.info("Private key loaded from env var")
        return key

    raise ValueError(
        "No Hyperliquid private key found. Set it via:\n"
        "  security add-generic-password -a hyperliquid-private-key -s hyperliquid -w <KEY>\n"
        "  or export HYPERLIQUID_PRIVATE_KEY=<KEY>"
    )


def _get_master_address() -> str | None:
    """Load master (main) account address from Keychain or env.
    
    The master address is the Hyperliquid account that authorized the API wallet.
    Needed for querying balance/positions (funds live on master, not API wallet).
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "hyperliquid-master-address", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    addr = os.getenv("HYPERLIQUID_MASTER_ADDRESS", "")
    return addr if addr else None


def connect(private_key: str | None = None, testnet: bool = True) -> tuple[Info, Exchange]:
    """Connect to Hyperliquid. Returns (Info, Exchange) clients.

    Args:
        private_key: Ethereum private key. Auto-loaded from Keychain if None.
        testnet: Use testnet (default True). Set False for mainnet.
    """
    global _info, _exchange, _account_address, _master_address, _is_testnet

    if private_key is None:
        private_key = _get_private_key()

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    _is_testnet = testnet
    api_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL

    account = eth_account.Account.from_key(private_key)
    _account_address = account.address

    # Master address: the main Hyperliquid account that authorized this API wallet.
    # API wallet signs trades, but balance/positions are on the master account.
    _master_address = _get_master_address()

    _info = Info(api_url, skip_ws=True)
    # API wallet trades on behalf of master account — do NOT use vault_address
    # (vault_address is for Hyperliquid Vaults, not regular accounts)
    _exchange = Exchange(account, api_url)

    env_label = "TESTNET" if testnet else "⚠️ MAINNET"
    query_addr = _master_address or _account_address
    logger.info(f"Connected to Hyperliquid {env_label} | API wallet: {_account_address} | Master: {query_addr}")
    return _info, _exchange


def _ensure_connected() -> tuple[Info, Exchange]:
    """Ensure we have active connections."""
    if _info is None or _exchange is None:
        connect()
    return _info, _exchange  # type: ignore


def get_account_info() -> dict:
    """Get account balance, margin, and positions summary.
    
    Handles Unified Account mode: checks both perps margin and spot USDC balance.
    """
    info, _ = _ensure_connected()
    query_addr = _master_address or _account_address
    state = info.user_state(query_addr)
    summary = state.get("marginSummary", {})
    positions = state.get("assetPositions", [])

    perps_value = float(summary.get("accountValue", 0))
    
    # Also check spot USDC balance (Unified Account keeps funds in spot)
    spot_usdc = 0.0
    try:
        spot_state = info.spot_user_state(query_addr)
        for bal in spot_state.get("balances", []):
            if bal.get("coin") == "USDC":
                spot_usdc = float(bal.get("total", 0))
                break
    except Exception as e:
        logger.debug(f"Could not fetch spot balance: {e}")

    # Total available = perps account value + spot USDC
    total_value = perps_value + spot_usdc

    return {
        "address": query_addr,
        "testnet": _is_testnet,
        "account_value": total_value,
        "perps_value": perps_value,
        "spot_usdc": spot_usdc,
        "total_margin_used": float(summary.get("totalMarginUsed", 0)),
        "total_ntl_pos": float(summary.get("totalNtlPos", 0)),
        "withdrawable": float(summary.get("withdrawable", 0)) + spot_usdc,
        "num_positions": len([p for p in positions if float(p["position"]["szi"]) != 0]),
        "timestamp": datetime.now(PT).isoformat(),
    }


def get_positions() -> list[dict]:
    """Get current open positions with PnL."""
    info, _ = _ensure_connected()
    state = info.user_state(_master_address or _account_address)
    positions = []

    for pos_data in state.get("assetPositions", []):
        p = pos_data["position"]
        size = float(p["szi"])
        if size == 0:
            continue

        positions.append({
            "symbol": p["coin"],
            "side": "long" if size > 0 else "short",
            "size": abs(size),
            "entry_price": float(p["entryPx"]) if p.get("entryPx") else None,
            "mark_price": float(p.get("positionValue", 0)) / abs(size) if size != 0 else None,
            "unrealized_pnl": float(p["unrealizedPnl"]),
            "return_on_equity": float(p.get("returnOnEquity", 0)),
            "liquidation_price": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
            "leverage": int(p.get("leverage", {}).get("value", 1)) if isinstance(p.get("leverage"), dict) else 1,
            "margin_used": float(p.get("marginUsed", 0)),
        })

    return positions


def get_price(symbol: str) -> float:
    """Get current mid price for a symbol."""
    info, _ = _ensure_connected()
    all_mids = info.all_mids()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")
    price_str = all_mids.get(symbol)
    if price_str is None:
        raise ValueError(f"No price found for {symbol}. Available: {list(all_mids.keys())[:10]}")
    return float(price_str)


def set_leverage(symbol: str, leverage: int) -> dict:
    """Set leverage for a symbol. Hard-capped at MAX_LEVERAGE."""
    _, exchange = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")

    if leverage > MAX_LEVERAGE:
        logger.warning(f"Leverage {leverage}x exceeds cap, using {MAX_LEVERAGE}x")
        leverage = MAX_LEVERAGE
    if leverage < 1:
        leverage = 1

    result = exchange.update_leverage(leverage=leverage, name=symbol, is_cross=True)
    logger.info(f"Set {symbol} leverage to {leverage}x: {result}")
    return {"symbol": symbol, "leverage": leverage, "result": result}


def place_order(
    symbol: str,
    side: str,
    size: float,
    leverage: int = 3,
    order_type: str = "market",
    price: float | None = None,
) -> dict:
    """Place an order on Hyperliquid.

    Args:
        symbol: BTC, ETH, or SOL
        side: "buy" or "sell"
        size: Position size in units of the asset
        leverage: 1-3 (hard capped)
        order_type: "market" or "limit"
        price: Required for limit orders. For market, used as slippage bound.
    """
    info, exchange = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")

    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}. Must be one of {SUPPORTED_SYMBOLS}")

    # Enforce leverage cap
    leverage = min(leverage, MAX_LEVERAGE)
    set_leverage(symbol, leverage)

    # Check position margin cap
    account = get_account_info()
    account_value = account["account_value"]
    if account_value <= 0:
        raise ValueError("Account has no value")

    current_price = get_price(symbol)
    notional = size * current_price
    margin_required = notional / leverage
    margin_pct = margin_required / account_value

    if margin_pct > MAX_POSITION_MARGIN_PCT:
        max_margin = account_value * MAX_POSITION_MARGIN_PCT
        max_size = (max_margin * leverage) / current_price
        logger.warning(
            f"Position margin {margin_pct*100:.1f}% exceeds {MAX_POSITION_MARGIN_PCT*100:.0f}% cap. "
            f"Reducing size from {size} to {max_size:.6f}"
        )
        size = max_size

    # Check drawdown circuit breaker
    _check_drawdown_circuit_breaker(account_value)

    is_buy = side.lower() == "buy"

    if order_type == "market":
        # Use IOC with slippage protection
        slippage = 0.01  # 1%
        if price is None:
            price = current_price * (1 + slippage) if is_buy else current_price * (1 - slippage)
        ot = {"limit": {"tif": "Ioc"}}
    else:
        if price is None:
            raise ValueError("Price required for limit orders")
        ot = {"limit": {"tif": "Gtc"}}

    # Round price to appropriate precision
    price = _round_price(symbol, price)
    size = _round_size(symbol, size)

    result = exchange.order(
        name=symbol,
        is_buy=is_buy,
        sz=size,
        limit_px=price,
        order_type=ot,
        reduce_only=False,
    )

    order_result = {
        "symbol": symbol,
        "side": side,
        "size": size,
        "price": price,
        "leverage": leverage,
        "order_type": order_type,
        "result": result,
        "timestamp": datetime.now(PT).isoformat(),
    }

    _log_trade(order_result)
    logger.info(f"Order placed: {side} {size} {symbol} @ {price} ({order_type}, {leverage}x): {result}")
    return order_result


def close_position(symbol: str) -> dict:
    """Close entire position for a symbol via market order."""
    info, exchange = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")

    positions = get_positions()
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        return {"symbol": symbol, "action": "no_position", "message": f"No open position for {symbol}"}

    is_buy = pos["side"] == "short"  # Reverse direction to close
    size = pos["size"]
    current_price = get_price(symbol)

    slippage = 0.02  # 2% slippage for close
    price = current_price * (1 + slippage) if is_buy else current_price * (1 - slippage)
    price = _round_price(symbol, price)

    result = exchange.order(
        name=symbol,
        is_buy=is_buy,
        sz=size,
        limit_px=price,
        order_type={"limit": {"tif": "Ioc"}},
        reduce_only=True,
    )

    close_result = {
        "symbol": symbol,
        "action": "close",
        "side": "buy" if is_buy else "sell",
        "size": size,
        "price": price,
        "result": result,
        "timestamp": datetime.now(PT).isoformat(),
    }
    _log_trade(close_result)
    logger.info(f"Closed {symbol} position: {result}")
    return close_result


def set_stop_loss(symbol: str, trigger_price: float) -> dict:
    """Set exchange-level stop-loss for a position (positionTpsl).

    This persists on the exchange — doesn't require client to be online.
    """
    info, exchange = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")

    positions = get_positions()
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        raise ValueError(f"No position found for {symbol}")

    is_buy = pos["side"] == "short"  # Reverse to close
    size = pos["size"]
    trigger_price = _round_price(symbol, trigger_price)

    result = exchange.order(
        name=symbol,
        is_buy=is_buy,
        sz=size,
        limit_px=trigger_price,
        order_type={"trigger": {"triggerPx": float(trigger_price), "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )

    logger.info(f"Stop-loss set for {symbol} at {trigger_price}: {result}")
    return {"symbol": symbol, "trigger_price": trigger_price, "result": result}


def set_take_profit(symbol: str, trigger_price: float) -> dict:
    """Set exchange-level take-profit for a position."""
    info, exchange = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")

    positions = get_positions()
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        raise ValueError(f"No position found for {symbol}")

    is_buy = pos["side"] == "short"
    size = pos["size"]
    trigger_price = _round_price(symbol, trigger_price)

    result = exchange.order(
        name=symbol,
        is_buy=is_buy,
        sz=size,
        limit_px=trigger_price,
        order_type={"trigger": {"triggerPx": float(trigger_price), "isMarket": True, "tpsl": "tp"}},
        reduce_only=True,
    )

    logger.info(f"Take-profit set for {symbol} at {trigger_price}: {result}")
    return {"symbol": symbol, "trigger_price": trigger_price, "result": result}


def get_funding_rate(symbol: str) -> float:
    """Get current funding rate for a symbol (per-hour rate)."""
    info, _ = _ensure_connected()
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")
    meta = info.meta()

    for asset in meta.get("universe", []):
        if asset["name"] == symbol:
            funding = asset.get("funding")
            if funding:
                return float(funding)
    return 0.0


def get_all_funding_rates() -> dict:
    """Get funding rates for all supported symbols."""
    info, _ = _ensure_connected()
    meta = info.meta()
    rates = {}

    for asset in meta.get("universe", []):
        name = asset["name"]
        funding = asset.get("funding")
        if funding and name in SUPPORTED_SYMBOLS:
            rate = float(funding)
            rates[name] = {
                "hourly": rate,
                "daily": rate * 24,
                "annualized": rate * 24 * 365,
                "annualized_pct": rate * 24 * 365 * 100,
            }

    return rates


def _check_drawdown_circuit_breaker(current_value: float) -> None:
    """Check 15% max drawdown circuit breaker."""
    state_file = _PROJECT_ROOT / "data" / "hl_high_water_mark.json"
    hwm = current_value

    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            hwm = max(data.get("high_water_mark", current_value), current_value)
        except Exception:
            pass

    drawdown = (hwm - current_value) / hwm if hwm > 0 else 0

    # Save updated HWM
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "high_water_mark": hwm,
        "current_value": current_value,
        "drawdown": drawdown,
        "updated": datetime.now(PT).isoformat(),
    }, indent=2))

    if drawdown >= MAX_DRAWDOWN:
        raise RuntimeError(
            f"🚨 CIRCUIT BREAKER: Drawdown {drawdown*100:.1f}% exceeds {MAX_DRAWDOWN*100:.0f}% limit. "
            f"HWM: ${hwm:,.2f}, Current: ${current_value:,.2f}. Trading halted."
        )


def _round_price(symbol: str, price: float) -> float:
    """Round price to appropriate tick size for Hyperliquid.
    
    Tick sizes (from Hyperliquid docs):
    BTC: $1.0, ETH: $0.10, SOL: $0.01
    """
    if symbol == "BTC":
        return round(price, 0)   # $1 tick
    elif symbol == "ETH":
        return round(price, 1)   # $0.10 tick
    else:
        return round(price, 2)   # $0.01 tick for SOL etc.


def _round_size(symbol: str, size: float) -> float:
    """Round size to appropriate precision."""
    if symbol == "BTC":
        return round(size, 5)  # 0.00001 BTC
    elif symbol == "ETH":
        return round(size, 4)  # 0.0001 ETH
    else:
        return round(size, 3)  # 0.001 SOL


def _log_trade(trade: dict) -> None:
    """Append trade to hl_trades.json."""
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    trades = []
    if TRADE_LOG.exists():
        try:
            trades = json.loads(TRADE_LOG.read_text())
        except Exception:
            trades = []
    trade["logged_at"] = datetime.now(PT).isoformat()
    trades.append(trade)
    TRADE_LOG.write_text(json.dumps(trades, indent=2, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing Hyperliquid connection (testnet)...")
    try:
        connect(testnet=True)
        info = get_account_info()
        print(f"Account: {info['address']}")
        print(f"Value: ${info['account_value']:,.2f}")
        print(f"Positions: {info['num_positions']}")

        for sym in SUPPORTED_SYMBOLS:
            try:
                p = get_price(sym)
                print(f"{sym}: ${p:,.2f}")
            except Exception as e:
                print(f"{sym}: {e}")
    except Exception as e:
        print(f"Connection failed: {e}")
