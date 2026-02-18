"""Tests for the backtesting engine."""

from __future__ import annotations

import math

import pytest

from scripts.backtest.engine import BacktestEngine, BacktestResult


class TestBacktestEngine:
    """Tests for BacktestEngine."""

    def test_runs_without_error(self) -> None:
        """Backtest on AAPL for 3 months should complete without exception."""
        engine = BacktestEngine(["AAPL"], "2025-10-01", "2025-12-31")
        result = engine.run()
        assert isinstance(result, BacktestResult)

    def test_result_has_all_fields(self) -> None:
        """BacktestResult should have all expected attributes."""
        engine = BacktestEngine(["AAPL"], "2025-10-01", "2025-12-31")
        result = engine.run()
        assert hasattr(result, "total_return")
        assert hasattr(result, "sharpe_ratio")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "win_rate")
        assert hasattr(result, "num_trades")
        assert hasattr(result, "daily_returns")
        assert hasattr(result, "equity_curve")
        assert hasattr(result, "trades")

    def test_equity_curve_starts_at_initial_capital(self) -> None:
        """Equity curve should begin at the initial capital value."""
        capital = 50_000
        engine = BacktestEngine(["AAPL"], "2025-10-01", "2025-12-31", initial_capital=capital)
        result = engine.run()
        if not result.equity_curve.empty:
            assert result.equity_curve.iloc[0] == pytest.approx(capital)

    def test_sharpe_is_finite(self) -> None:
        """Sharpe ratio should be a finite number."""
        engine = BacktestEngine(["AAPL"], "2025-10-01", "2025-12-31")
        result = engine.run()
        assert math.isfinite(result.sharpe_ratio)

    def test_summary_returns_string(self) -> None:
        """summary() should return a non-empty string."""
        result = BacktestResult()
        s = result.summary()
        assert isinstance(s, str)
        assert "BACKTEST RESULTS" in s

    def test_multiple_tickers(self) -> None:
        """Backtest with multiple tickers should work."""
        engine = BacktestEngine(["AAPL", "NVDA"], "2025-10-01", "2025-12-31")
        result = engine.run()
        assert isinstance(result, BacktestResult)
        assert math.isfinite(result.total_return)
