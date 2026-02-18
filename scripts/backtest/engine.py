"""Core backtesting engine for simulating trading strategies on historical data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from scripts.core.data_pipeline import get_price_data
from scripts.core.signal_engine import compute_signals
from scripts.core.conviction import compute_conviction, DEFAULT_WEIGHTS
from scripts.core.risk_manager import approve_trade


@dataclass
class BacktestResult:
    """Container for backtest results and performance metrics."""

    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    daily_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trades: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        """Return a formatted string report of backtest results."""
        lines = [
            "=" * 55,
            "  BACKTEST RESULTS",
            "=" * 55,
            f"  Total Return:    {self.total_return:+.2f}%",
            f"  Sharpe Ratio:    {self.sharpe_ratio:.3f}",
            f"  Max Drawdown:    {self.max_drawdown:.2f}%",
            f"  Win Rate:        {self.win_rate:.1f}%",
            f"  Num Trades:      {self.num_trades}",
            f"  Trading Days:    {len(self.equity_curve)}",
        ]
        if not self.equity_curve.empty:
            lines.append(f"  Start Capital:   ${self.equity_curve.iloc[0]:,.2f}")
            lines.append(f"  End Capital:     ${self.equity_curve.iloc[-1]:,.2f}")
        lines.append("=" * 55)
        return "\n".join(lines)


class BacktestEngine:
    """Day-by-day backtesting engine using the existing signal framework.

    Args:
        tickers: List of ticker symbols to trade.
        start_date: Start date string (YYYY-MM-DD).
        end_date: End date string (YYYY-MM-DD).
        initial_capital: Starting cash amount.
        strategy: Strategy name — "technical", "momentum", "mean_reversion", or "combined".
    """

    def __init__(
        self,
        tickers: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float = 100_000,
        strategy: str = "technical",
    ) -> None:
        self.tickers = [t.upper() for t in tickers]
        self.start_date = pd.Timestamp(start_date)
        self.end_date = pd.Timestamp(end_date)
        self.initial_capital = initial_capital
        self.strategy = strategy
        self.weights: dict[str, float] = dict(DEFAULT_WEIGHTS)

    def set_weights(self, weights: dict[str, float]) -> None:
        """Override default signal weights."""
        self.weights = weights

    def _fetch_data(self) -> dict[str, pd.DataFrame]:
        """Fetch historical data for all tickers covering the backtest window."""
        data: dict[str, pd.DataFrame] = {}
        # We need look-back data for indicators, so fetch more than the window
        for ticker in self.tickers:
            try:
                df = get_price_data(ticker, period="1y")
                if df is not None and not df.empty:
                    # Normalize to tz-naive index
                    if hasattr(df.index, 'tz') and df.index.tz is not None:
                        df = df.copy()
                        df.index = df.index.tz_localize(None)
                    data[ticker] = df
            except Exception:
                continue
        return data

    def _compute_signals_for_window(
        self, ticker: str, df: pd.DataFrame, end_idx: int
    ) -> pd.DataFrame:
        """Compute signals using data up to end_idx (inclusive)."""
        window = df.iloc[: end_idx + 1]
        if len(window) < 14:
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
        try:
            return compute_signals(ticker, window)
        except Exception:
            return pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])

    def run(self) -> BacktestResult:
        """Execute the backtest and return results.

        Iterates through each trading day in the date range. For each day,
        computes signals, derives conviction scores, and simulates trades
        at the next day's open price.

        Returns:
            BacktestResult with performance metrics.
        """
        price_data = self._fetch_data()
        if not price_data:
            return BacktestResult()

        # Build a union trading calendar from available data
        all_dates: set[pd.Timestamp] = set()
        for df in price_data.values():
            idx = df.index
            if not isinstance(idx, pd.DatetimeIndex):
                idx = pd.to_datetime(idx)
            # Normalize to tz-naive for comparison
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            all_dates.update(idx)
        trading_days = sorted(d for d in all_dates if self.start_date <= d <= self.end_date)
        if len(trading_days) < 2:
            return BacktestResult()

        # State
        cash = self.initial_capital
        positions: dict[str, dict] = {}  # ticker -> {qty, entry_price, highest}
        equity_values: list[float] = []
        equity_dates: list[pd.Timestamp] = []
        trades: list[dict] = []

        for i, day in enumerate(trading_days):
            # 1. Update position highs & mark-to-market
            portfolio_value = cash
            pos_list: list[dict] = []
            for tk, pos in positions.items():
                df = price_data.get(tk)
                if df is None:
                    continue
                idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
                mask = idx <= day
                if not mask.any():
                    continue
                current_price = float(df.loc[mask, "Close"].iloc[-1])
                pos["highest"] = max(pos["highest"], current_price)
                mv = pos["qty"] * current_price
                portfolio_value += mv
                pos_list.append({"ticker": tk, "market_value": mv})

            equity_values.append(portfolio_value)
            equity_dates.append(day)

            # Skip signal computation on the last day (no next day to trade)
            if i >= len(trading_days) - 1:
                break

            next_day = trading_days[i + 1]

            # 2. Compute signals for each ticker
            all_signals: list[pd.DataFrame] = []
            for tk in self.tickers:
                df = price_data.get(tk)
                if df is None:
                    continue
                idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
                valid = idx <= day
                end_idx = int(valid.sum()) - 1
                if end_idx < 14:
                    continue
                sigs = self._compute_signals_for_window(tk, df, end_idx)
                if not sigs.empty:
                    all_signals.append(sigs)

            if not all_signals:
                continue

            combined = pd.concat(all_signals, ignore_index=True)
            conviction_df = compute_conviction(combined, self.weights)

            # 3. Check exits first (stop-loss or negative conviction)
            for tk in list(positions.keys()):
                pos = positions[tk]
                df = price_data.get(tk)
                if df is None:
                    continue

                # Get next day's open for execution
                idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
                next_mask = idx == next_day
                if not next_mask.any():
                    # Use current close as fallback
                    mask = idx <= day
                    if not mask.any():
                        continue
                    exec_price = float(df.loc[mask, "Close"].iloc[-1])
                else:
                    exec_price = float(df.loc[next_mask, "Open"].iloc[0])

                # Stop-loss check: 8% trailing
                stop_price = pos["highest"] * 0.92
                tk_conv = conviction_df[conviction_df["ticker"] == tk]
                conv_score = float(tk_conv["conviction_score"].iloc[0]) if not tk_conv.empty else 0.0

                should_exit = exec_price <= stop_price or conv_score < -0.1

                if should_exit:
                    pnl = (exec_price - pos["entry_price"]) * pos["qty"]
                    cash += pos["qty"] * exec_price
                    trades.append({
                        "ticker": tk,
                        "side": "sell",
                        "qty": pos["qty"],
                        "price": exec_price,
                        "date": str(next_day.date()),
                        "pnl": round(pnl, 2),
                        "reason": "stop_loss" if exec_price <= stop_price else "conviction_exit",
                    })
                    del positions[tk]

            # 4. Check entries (conviction > 0.3)
            for _, row in conviction_df.iterrows():
                tk = row["ticker"]
                score = row["conviction_score"]
                if score <= 0.3 or tk in positions:
                    continue

                df = price_data.get(tk)
                if df is None:
                    continue

                idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
                next_mask = idx == next_day
                if not next_mask.any():
                    mask = idx <= day
                    if not mask.any():
                        continue
                    exec_price = float(df.loc[mask, "Close"].iloc[-1])
                else:
                    exec_price = float(df.loc[next_mask, "Open"].iloc[0])

                if exec_price <= 0:
                    continue

                # Position sizing: 5% of portfolio
                target_value = portfolio_value * 0.05
                qty = int(target_value / exec_price)
                if qty <= 0:
                    continue

                # Risk check
                approved, sized_qty, _ = approve_trade(
                    tk, "buy", qty, exec_price, portfolio_value, pos_list
                )
                if not approved or sized_qty <= 0:
                    continue

                cost = sized_qty * exec_price
                if cost > cash:
                    sized_qty = int(cash / exec_price)
                    if sized_qty <= 0:
                        continue
                    cost = sized_qty * exec_price

                cash -= cost
                positions[tk] = {
                    "qty": sized_qty,
                    "entry_price": exec_price,
                    "highest": exec_price,
                }
                trades.append({
                    "ticker": tk,
                    "side": "buy",
                    "qty": sized_qty,
                    "price": exec_price,
                    "date": str(next_day.date()),
                    "pnl": 0.0,
                    "reason": f"conviction={score:.3f}",
                })

        # Build result
        if not equity_values:
            return BacktestResult()

        equity_curve = pd.Series(equity_values, index=equity_dates)
        daily_returns = equity_curve.pct_change().dropna()

        total_return = (equity_values[-1] / self.initial_capital - 1.0) * 100

        sharpe = 0.0
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))

        # Max drawdown
        peak = equity_curve.expanding().max()
        drawdowns = (equity_curve - peak) / peak * 100
        max_dd = float(drawdowns.min())

        # Win rate
        closed = [t for t in trades if t["side"] == "sell"]
        wins = sum(1 for t in closed if t["pnl"] > 0)
        win_rate = (wins / len(closed) * 100) if closed else 0.0

        return BacktestResult(
            total_return=round(total_return, 2),
            sharpe_ratio=round(sharpe, 3),
            max_drawdown=round(max_dd, 2),
            win_rate=round(win_rate, 1),
            num_trades=len(trades),
            daily_returns=daily_returns,
            equity_curve=equity_curve,
            trades=trades,
        )
