"""Intraday risk manager: position limits, daily loss cap, hard close.

V2.2 changes:
- ATR-based stops replace fixed 2% (1.5x ATR stop, 3x ATR target)
- Time-window weights (power hour boost, midday block)
- Per-symbol re-entry limits (max 2 trades/symbol/day, half size after loss)
- Consecutive loss circuit breaker (3 losses → 30 min pause)
- Hard close moved to 15:45 ET
- Position size reduction after 3 consecutive losses (50%)
- Market regime filter (VIX > 30 → 50% size reduction)
- Bid-ask spread filter (> 0.5% → skip)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_FILE = DATA_DIR / "intraday_state.json"

# Risk parameters — V3: agent-driven, no hard caps on sizing
MAX_POSITIONS = 99             # effectively unlimited — agent decides
MAX_POSITION_PCT = 50.0        # generous ceiling — agent sizes based on conviction
MAX_DAILY_LOSS_DOLLARS = 2000  # safety net only
MAX_DAILY_LOSS_PCT = 2.0       # % of portfolio — last resort circuit breaker
HARD_CLOSE_HOUR_ET = 15        # V2.2: 15:45 ET (was 12:45 PT)
HARD_CLOSE_MINUTE_ET = 45

# V2.2: ATR-based stops (replace fixed 2%/4%)
ATR_STOP_MULTIPLIER = 1.5     # stop = entry - 1.5 * ATR
ATR_TARGET_MULTIPLIER = 3.0   # target = entry + 3.0 * ATR (1:2 R/R)
FALLBACK_STOP_PCT = 2.0       # fallback if ATR unavailable
FALLBACK_TARGET_PCT = 4.0

# V2.3: Minimum stop distance floor (fixes too-tight stops on low-ATR stocks like F)
MIN_STOP_PCT = 0.5            # minimum stop = 0.5% of entry price
MAX_STOP_PCT = 5.0            # maximum stop = 5% (sanity cap)

# V2.3: Last entry cutoff — no new positions within this many minutes of hard close
LAST_ENTRY_MINUTES_BEFORE_CLOSE = 90  # 1.5 hours before 15:45 ET = no entry after 14:15 ET

# V2.2: Re-entry limits
MAX_TRADES_PER_SYMBOL = 2     # max trades per ticker per day
REENTRY_SIZE_FACTOR = 0.5     # half size after loss on same ticker

# V2.2: Consecutive loss circuit breaker
MAX_CONSECUTIVE_LOSSES = 3
LOSS_PAUSE_MINUTES = 30
LOSS_SIZE_REDUCTION = 0.5     # 50% size after 3 consecutive losses

# V2.2: Market regime
VIX_HIGH_THRESHOLD = 30       # VIX > 30 → reduce size 50%

# V2.2: Bid-ask spread filter
MAX_SPREAD_PCT = 0.5          # skip if spread > 0.5%


def _now_et() -> datetime:
    """Current time in Eastern."""
    return datetime.now(ET)


# ── Time Window Weights (V2.2) ──────────────────────────────────────────────

def get_time_weight() -> float:
    """Return position size multiplier based on current ET time window.

    9:30-10:30 = 1.5x (power hour — best signals)
    10:30-11:30 = 1.0x (still good)
    11:30-13:00 = 0.3x (midday chop — reduced, not blocked)
    13:00-15:00 = 0.8x (afternoon session)
    15:00-15:45 = 0.0 (approaching close — no new positions)

    Note: We hard close at 12:45 PM PST = 15:45 ET, so the full
    afternoon window is available. Midday is reduced (0.3x) not
    fully blocked, since our trading day ends at 1 PM PST anyway.
    """
    now = _now_et()
    t = now.time()

    if time(9, 30) <= t < time(10, 30):
        return 1.5
    elif time(10, 30) <= t < time(11, 30):
        return 1.0
    elif time(11, 30) <= t < time(13, 0):
        return 0.3  # Midday chop — reduced but not blocked
    elif time(13, 0) <= t < time(15, 0):
        return 0.8  # Afternoon session
    else:
        return 0.0  # Before market or approaching close


# ── State Management ────────────────────────────────────────────────────────

def _load_state() -> dict:
    """Load today's intraday state, then sync open_positions with Alpaca."""
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") == date.today().isoformat():
                # Ensure V2.2 fields exist
                _ensure_v22_fields(state)
                _sync_positions_from_alpaca(state)
                return state
        except Exception:
            pass

    state = _fresh_state()
    _sync_positions_from_alpaca(state)
    return state


def _fresh_state() -> dict:
    return {
        "date": date.today().isoformat(),
        "trades": [],
        "open_positions": {},
        "realized_pnl": 0.0,
        "total_trades": 0,
        "stopped_trading": False,
        "stop_reason": None,
        # V2.2 fields
        "symbol_trade_counts": {},    # {symbol: count}
        "symbol_last_result": {},     # {symbol: "win"|"loss"}
        "consecutive_losses": 0,
        "loss_pause_until": None,     # ISO timestamp or None
    }


def _ensure_v22_fields(state: dict) -> None:
    """Ensure V2.2 state fields exist (upgrade from V2.1 state)."""
    state.setdefault("symbol_trade_counts", {})
    state.setdefault("symbol_last_result", {})
    state.setdefault("consecutive_losses", 0)
    state.setdefault("loss_pause_until", None)


_CRYPTO_TICKERS = {"BTCUSD", "ETHUSD", "SOLUSD", "BTC/USD", "ETH/USD", "SOL/USD"}


def _sync_positions_from_alpaca(state: dict) -> None:
    """Reconcile local open_positions with Alpaca's actual positions."""
    try:
        from scripts.core.executor import get_positions
        alpaca_positions = get_positions()
    except Exception as e:
        logger.warning("Could not sync with Alpaca: %s", e)
        return

    alpaca_tickers = {
        p["ticker"] for p in alpaca_positions
        if p["ticker"] not in _CRYPTO_TICKERS and p["qty"] > 0
    }

    local_tickers = set(state.get("open_positions", {}).keys())
    stale = local_tickers - alpaca_tickers
    for ticker in stale:
        state["open_positions"].pop(ticker)
        logger.info("Sync: removed stale position %s (not on Alpaca)", ticker)

    if stale:
        save_state(state)


def save_state(state: dict) -> None:
    """Persist intraday state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ── Market Regime Filter (V2.2 P2) ─────────────────────────────────────────

def get_market_regime() -> dict:
    """Check VIX and SPY for market regime. Returns size multiplier and info."""
    regime = {"vix": None, "spy_direction": None, "size_multiplier": 1.0}
    try:
        import yfinance as yf
        # VIX
        vix_data = yf.download("^VIX", period="1d", interval="1m", progress=False)
        if not vix_data.empty:
            vix_close = vix_data["Close"]
            if hasattr(vix_close, "columns"):
                vix_close = vix_close.iloc[:, 0]
            vix_val = float(vix_close.dropna().iloc[-1])
            regime["vix"] = round(vix_val, 2)
            if vix_val > VIX_HIGH_THRESHOLD:
                regime["size_multiplier"] = 0.5
                logger.info("High VIX (%.1f > %d): reducing position size 50%%", vix_val, VIX_HIGH_THRESHOLD)
    except Exception as e:
        logger.warning("Market regime check failed: %s", e)
    return regime


# ── Bid-Ask Spread Filter (V2.2 P2) ────────────────────────────────────────

def check_spread(ticker: str) -> tuple[bool, float]:
    """Check if bid-ask spread is acceptable.

    Returns:
        (pass, spread_pct) — pass=True if spread ≤ MAX_SPREAD_PCT.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        bid = info.get("bid", 0)
        ask = info.get("ask", 0)
        if bid and ask and ask > bid:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid * 100
            return spread_pct <= MAX_SPREAD_PCT, round(spread_pct, 3)
    except Exception:
        pass
    return True, 0.0  # Pass if data unavailable


# ── Can Trade Check (V2.2) ──────────────────────────────────────────────────

def can_trade(state: dict, portfolio_value: float, ticker: str | None = None) -> tuple[bool, str]:
    """Check if we're allowed to take new trades.

    V2.2 changes:
    - Time-window weights replace dead zone (weight=0 blocks entry)
    - Per-symbol trade limit (max 2)
    - Consecutive loss pause (3 losses → 30 min cooldown)

    Args:
        state: Current intraday state.
        portfolio_value: Account value.
        ticker: Optional ticker for per-symbol checks.

    Returns:
        (allowed, reason)
    """
    if state.get("stopped_trading"):
        return False, f"Trading stopped: {state.get('stop_reason', 'daily loss limit')}"

    # V2.2: Time weight check (replaces dead zone)
    tw = get_time_weight()
    if tw <= 0:
        return False, "Time window closed (weight=0): no new entries now"

    # V2.3: No new entries within 1.5h of hard close (14:15 ET for 15:45 close).
    # Ensures enough time for the trade to play out before forced liquidation.
    now = _now_et()
    hard_close_dt = now.replace(hour=HARD_CLOSE_HOUR_ET, minute=HARD_CLOSE_MINUTE_ET, second=0, microsecond=0)
    minutes_to_close = (hard_close_dt - now).total_seconds() / 60
    if 0 < minutes_to_close < LAST_ENTRY_MINUTES_BEFORE_CLOSE:
        return False, (f"Too close to hard close: {minutes_to_close:.0f} min left "
                       f"(need >{LAST_ENTRY_MINUTES_BEFORE_CLOSE} min)")

    # V2.2: Consecutive loss pause
    pause_until = state.get("loss_pause_until")
    if pause_until:
        try:
            pause_dt = datetime.fromisoformat(pause_until)
            if pause_dt.tzinfo is None:
                pause_dt = pause_dt.replace(tzinfo=ET)
            if _now_et() < pause_dt:
                remaining = (pause_dt - _now_et()).total_seconds() / 60
                return False, f"Loss pause: {remaining:.0f} min remaining ({MAX_CONSECUTIVE_LOSSES} consecutive losses)"
        except Exception:
            pass
        # Pause expired
        state["loss_pause_until"] = None

    # Daily loss check
    max_loss_dollars = MAX_DAILY_LOSS_DOLLARS
    max_loss_pct = portfolio_value * MAX_DAILY_LOSS_PCT / 100
    max_loss = min(max_loss_dollars, max_loss_pct)

    if state["realized_pnl"] < -max_loss:
        state["stopped_trading"] = True
        state["stop_reason"] = f"Daily loss limit hit: ${state['realized_pnl']:.2f} (max -${max_loss:.2f})"
        save_state(state)
        return False, state["stop_reason"]

    # Position limit
    open_count = len(state.get("open_positions", {}))
    if open_count >= MAX_POSITIONS:
        return False, f"Max positions reached ({open_count}/{MAX_POSITIONS})"

    # V2.2: Per-symbol trade limit
    if ticker:
        count = state.get("symbol_trade_counts", {}).get(ticker, 0)
        if count >= MAX_TRADES_PER_SYMBOL:
            return False, f"Max trades for {ticker} ({count}/{MAX_TRADES_PER_SYMBOL})"

    return True, "OK"


# ── Position Sizing (V2.2: ATR-based) ──────────────────────────────────────

def size_position(ticker: str, price: float, portfolio_value: float,
                  state: dict, atr: float = 0.0) -> tuple[int, float, float]:
    """Calculate position size with ATR-based stop-loss and take-profit.

    V2.2: Uses 1.5x ATR for stop distance, 3x ATR for target (1:2 R/R).
    Falls back to fixed 2%/4% if ATR unavailable.
    Applies time weight, consecutive loss reduction, re-entry reduction,
    and market regime adjustment.

    Args:
        ticker: Stock symbol.
        price: Current price.
        portfolio_value: Account value.
        state: Current intraday state.
        atr: Average True Range from signals (0 = use fallback).

    Returns:
        (quantity, stop_price, target_price)
    """
    if price <= 0:
        return 0, 0.0, 0.0

    # Calculate stop/target distances
    if atr > 0:
        stop_distance = ATR_STOP_MULTIPLIER * atr
        target_distance = ATR_TARGET_MULTIPLIER * atr
    else:
        stop_distance = price * FALLBACK_STOP_PCT / 100
        target_distance = price * FALLBACK_TARGET_PCT / 100

    # V2.3: Enforce minimum and maximum stop distance as % of price.
    # Fixes too-tight stops on low-volatility / low-price stocks (e.g. F @ $14,
    # 5min ATR ~$0.03 → 0.2% stop is way too tight; floor at 0.5%).
    min_stop_distance = price * MIN_STOP_PCT / 100
    max_stop_distance = price * MAX_STOP_PCT / 100
    if stop_distance < min_stop_distance:
        logger.info("Stop floor: %s ATR stop $%.3f < min $%.3f (%.1f%%), using floor",
                     ticker, stop_distance, min_stop_distance, MIN_STOP_PCT)
        stop_distance = min_stop_distance
        # Scale target proportionally to maintain R:R ratio
        target_distance = max(target_distance, stop_distance * (ATR_TARGET_MULTIPLIER / ATR_STOP_MULTIPLIER))
    elif stop_distance > max_stop_distance:
        logger.info("Stop cap: %s stop $%.3f > max $%.3f (%.1f%%), using cap",
                     ticker, stop_distance, max_stop_distance, MAX_STOP_PCT)
        stop_distance = max_stop_distance
        target_distance = stop_distance * (ATR_TARGET_MULTIPLIER / ATR_STOP_MULTIPLIER)

    stop_price = round(price - stop_distance, 2)
    target_price = round(price + target_distance, 2)

    # Base position size from max position %
    max_value = portfolio_value * MAX_POSITION_PCT / 100
    qty = int(max_value / price) if price > 0 else 0

    # Risk-based sizing: don't risk more than remaining budget per trade
    max_loss = portfolio_value * MAX_DAILY_LOSS_PCT / 100
    remaining_risk = max_loss + state["realized_pnl"]
    if remaining_risk > 0 and stop_distance > 0:
        risk_qty = int(remaining_risk / stop_distance)
        qty = min(qty, risk_qty)

    # V2.2: Apply time weight
    tw = get_time_weight()
    if tw > 0:
        qty = int(qty * tw)

    # V2.2: Consecutive loss size reduction (3+ losses → 50%)
    if state.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
        qty = int(qty * LOSS_SIZE_REDUCTION)
        logger.info("Consecutive loss reduction: %d losses, size × %.0f%%",
                     state["consecutive_losses"], LOSS_SIZE_REDUCTION * 100)

    # V2.2: Re-entry after loss on same symbol → half size
    if state.get("symbol_last_result", {}).get(ticker) == "loss":
        qty = int(qty * REENTRY_SIZE_FACTOR)
        logger.info("Re-entry reduction for %s (previous loss): size × %.0f%%",
                     ticker, REENTRY_SIZE_FACTOR * 100)

    # V2.2: Market regime adjustment (VIX > 30 → 50%)
    try:
        regime = get_market_regime()
        regime_mult = regime.get("size_multiplier", 1.0)
        if regime_mult < 1.0:
            qty = int(qty * regime_mult)
    except Exception:
        pass

    return max(qty, 0), stop_price, target_price


def should_hard_close() -> bool:
    """Check if it's time to force-close all positions (15:45 ET)."""
    now = _now_et()
    return (now.hour > HARD_CLOSE_HOUR_ET or
            (now.hour == HARD_CLOSE_HOUR_ET and now.minute >= HARD_CLOSE_MINUTE_ET))


# ── Trade Recording (V2.2: track per-symbol counts & consecutive losses) ───

def record_trade(state: dict, trade: dict) -> None:
    """Record a completed trade (entry or exit)."""
    state["trades"].append({
        **trade,
        "timestamp": datetime.utcnow().isoformat(),
    })
    state["total_trades"] += 1

    ticker = trade.get("ticker", "")

    if trade.get("side") == "sell" and "pnl" in trade:
        state["realized_pnl"] += trade["pnl"]

        # V2.2: Track per-symbol trade count
        counts = state.setdefault("symbol_trade_counts", {})
        counts[ticker] = counts.get(ticker, 0) + 1

        # V2.2: Track per-symbol last result
        results = state.setdefault("symbol_last_result", {})
        is_loss = trade["pnl"] < 0
        results[ticker] = "loss" if is_loss else "win"

        # V2.2: Track consecutive losses
        if is_loss:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
                pause_until = _now_et() + timedelta(minutes=LOSS_PAUSE_MINUTES)
                state["loss_pause_until"] = pause_until.isoformat()
                logger.warning("Circuit breaker: %d consecutive losses → pausing until %s",
                               state["consecutive_losses"], pause_until.strftime("%H:%M ET"))
        else:
            state["consecutive_losses"] = 0
            state["loss_pause_until"] = None

    save_state(state)


def record_open_position(state: dict, ticker: str, qty: int, entry_price: float,
                         stop_price: float, target_price: float, reason: str = "",
                         atr: float = 0.0) -> None:
    """Track an open intraday position."""
    state["open_positions"][ticker] = {
        "qty": qty,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "entry_time": datetime.utcnow().isoformat(),
        "reason": reason,
        "atr": atr,                    # V2.2: store ATR for partial TP
        "partial_tp_done": False,      # V2.2: track partial take-profit
        "original_qty": qty,           # V2.2: for partial TP calc
    }
    save_state(state)


def close_position(state: dict, ticker: str, exit_price: float, reason: str = "") -> dict | None:
    """Close an open position and record P&L."""
    pos = state["open_positions"].pop(ticker, None)
    if not pos:
        return None

    pnl = (exit_price - pos["entry_price"]) * pos["qty"]
    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100

    trade = {
        "ticker": ticker,
        "side": "sell",
        "qty": pos["qty"],
        "entry_price": pos["entry_price"],
        "exit_price": exit_price,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "reason": reason,
        "hold_time": pos.get("entry_time", ""),
    }
    record_trade(state, trade)
    return trade


def check_stops_and_targets(state: dict, current_prices: dict[str, float]) -> list[dict]:
    """Check all open positions against stop-loss and take-profit levels."""
    triggered = []
    for ticker, pos in list(state["open_positions"].items()):
        price = current_prices.get(ticker)
        if price is None:
            continue

        if price <= pos["stop_price"]:
            triggered.append({"ticker": ticker, "price": price, "reason": "stop_loss",
                            "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})
        elif price >= pos["target_price"]:
            triggered.append({"ticker": ticker, "price": price, "reason": "take_profit",
                            "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})

    return triggered


# ── Partial Take-Profit (V2.2 P1) ──────────────────────────────────────────

def check_partial_tp(state: dict, current_prices: dict[str, float]) -> list[dict]:
    """Check positions for partial take-profit at 1R (ATR distance).

    At 1R profit: sell half, move stop to breakeven.
    Remaining uses trailing stop (handled in trader).

    Returns:
        List of partial TP actions to execute.
    """
    actions = []
    for ticker, pos in list(state["open_positions"].items()):
        if pos.get("partial_tp_done"):
            continue

        price = current_prices.get(ticker)
        if price is None:
            continue

        atr = pos.get("atr", 0)
        if atr <= 0:
            continue

        # 1R = 1.5 * ATR (the stop distance)
        one_r = ATR_STOP_MULTIPLIER * atr
        gain = price - pos["entry_price"]

        if gain >= one_r:
            sell_qty = max(pos["qty"] // 2, 1)
            if sell_qty >= pos["qty"]:
                continue  # Don't sell everything as partial

            actions.append({
                "ticker": ticker,
                "action": "partial_tp",
                "sell_qty": sell_qty,
                "remaining_qty": pos["qty"] - sell_qty,
                "price": price,
                "gain_pct": round(gain / pos["entry_price"] * 100, 2),
            })

    return actions


def apply_partial_tp(state: dict, ticker: str, sold_qty: int, sell_price: float) -> None:
    """Update state after partial take-profit execution."""
    pos = state["open_positions"].get(ticker)
    if not pos:
        return

    # Record partial sell P&L
    partial_pnl = (sell_price - pos["entry_price"]) * sold_qty
    state["realized_pnl"] += partial_pnl

    # Update position
    pos["qty"] -= sold_qty
    pos["partial_tp_done"] = True
    pos["stop_price"] = pos["entry_price"]  # Move stop to breakeven

    logger.info("Partial TP %s: sold %d @ $%.2f, P&L $%.2f, stop → breakeven",
                ticker, sold_qty, sell_price, partial_pnl)
    save_state(state)


# ── Trailing Stop (V2.2: for remaining position after partial TP) ──────────

def update_trailing_stops(state: dict, current_prices: dict[str, float]) -> None:
    """Update trailing stops for positions that have taken partial profit.

    After partial TP, trail stop at entry + (current_gain - 1*ATR), never lower.
    """
    for ticker, pos in state.get("open_positions", {}).items():
        if not pos.get("partial_tp_done"):
            continue

        price = current_prices.get(ticker)
        if price is None:
            continue

        atr = pos.get("atr", 0)
        if atr <= 0:
            continue

        # Trail: current price minus 1x ATR, but never below current stop
        trail_stop = round(price - atr, 2)
        if trail_stop > pos["stop_price"]:
            old = pos["stop_price"]
            pos["stop_price"] = trail_stop
            logger.info("Trailing stop %s: $%.2f → $%.2f (price=$%.2f)",
                        ticker, old, trail_stop, price)
            save_state(state)


# ── Summary & Archive ───────────────────────────────────────────────────────

def get_daily_summary(state: dict) -> dict:
    """Generate end-of-day summary."""
    trades = state.get("trades", [])
    sells = [t for t in trades if t.get("side") == "sell"]

    winners = [t for t in sells if t.get("pnl", 0) > 0]
    losers = [t for t in sells if t.get("pnl", 0) < 0]

    return {
        "date": state.get("date"),
        "total_trades": len(trades),
        "round_trips": len(sells),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(sells) * 100, 1) if sells else 0,
        "realized_pnl": round(state.get("realized_pnl", 0), 2),
        "best_trade": max(sells, key=lambda t: t.get("pnl", 0)) if sells else None,
        "worst_trade": min(sells, key=lambda t: t.get("pnl", 0)) if sells else None,
        "stopped_early": state.get("stopped_trading", False),
        "consecutive_losses": state.get("consecutive_losses", 0),
    }


def archive_day(state: dict) -> None:
    """Archive today's state to history."""
    history_dir = DATA_DIR / "intraday_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    archive_path = history_dir / f"{state['date']}.json"
    archive_path.write_text(json.dumps(state, indent=2, default=str))
    logger.info("Archived intraday state to %s", archive_path)
