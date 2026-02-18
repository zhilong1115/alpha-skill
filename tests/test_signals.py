"""Tests for signal engine and conviction scoring."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.core.signal_engine import compute_signals, _rsi_score
from scripts.core.conviction import compute_conviction


class TestComputeSignals:
    """Tests for compute_signals function."""

    def test_returns_dataframe_with_correct_columns(self) -> None:
        """Signal output must have ticker, signal_name, value, score columns."""
        df = _make_dummy_ohlcv(days=50)
        result = compute_signals("TEST", df)
        assert isinstance(result, pd.DataFrame)
        for col in ("ticker", "signal_name", "value", "score"):
            assert col in result.columns, f"Missing column: {col}"

    def test_empty_dataframe_returns_empty(self) -> None:
        """Empty input should produce empty output."""
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        result = compute_signals("TEST", df)
        assert result.empty

    def test_too_few_rows_still_works(self) -> None:
        """With only a few rows, should still return at least some signals."""
        df = _make_dummy_ohlcv(days=20)
        result = compute_signals("TEST", df)
        # Should get RSI, MACD, BBANDS, VOLUME at minimum (maybe not SMA200)
        assert len(result) >= 1

    def test_scores_in_valid_range(self) -> None:
        """All scores should be in [-1, 1]."""
        df = _make_dummy_ohlcv(days=250)
        result = compute_signals("TEST", df)
        assert (result["score"] >= -1.0).all()
        assert (result["score"] <= 1.0).all()

    def test_with_real_aapl_data(self) -> None:
        """Integration test with real AAPL data."""
        from scripts.core.data_pipeline import get_price_data

        df = get_price_data("AAPL", period="1mo")
        result = compute_signals("AAPL", df)
        assert not result.empty
        assert (result["ticker"] == "AAPL").all()


class TestRsiScore:
    """Tests for RSI score mapping."""

    def test_oversold_gives_positive(self) -> None:
        """RSI=20 (oversold) should produce a positive (bullish) score."""
        assert _rsi_score(20) > 0

    def test_overbought_gives_negative(self) -> None:
        """RSI=80 (overbought) should produce a negative (bearish) score."""
        assert _rsi_score(80) < 0

    def test_neutral_gives_zero(self) -> None:
        """RSI=50 (neutral) should produce zero."""
        assert _rsi_score(50) == 0.0

    def test_extreme_low(self) -> None:
        """RSI=0 should give maximum bullish score of 1.0."""
        assert _rsi_score(0) == pytest.approx(1.0)

    def test_extreme_high(self) -> None:
        """RSI=100 should give maximum bearish score of -1.0."""
        assert _rsi_score(100) == pytest.approx(-1.0)


class TestConviction:
    """Tests for conviction score computation."""

    def test_conviction_in_range(self) -> None:
        """Conviction score must be in [-1, 1]."""
        signals = pd.DataFrame([
            {"ticker": "TEST", "signal_name": "RSI_14", "value": 30, "score": 0.5},
            {"ticker": "TEST", "signal_name": "MACD_12_26_9", "value": 1.0, "score": -0.3},
        ])
        result = compute_conviction(signals)
        assert len(result) == 1
        score = result.iloc[0]["conviction_score"]
        assert -1.0 <= score <= 1.0

    def test_empty_signals(self) -> None:
        """Empty signals should produce empty conviction."""
        signals = pd.DataFrame(columns=["ticker", "signal_name", "value", "score"])
        result = compute_conviction(signals)
        assert result.empty


def _make_dummy_ohlcv(days: int = 100, start_price: float = 100.0) -> pd.DataFrame:
    """Create synthetic OHLCV data for testing."""
    import numpy as np

    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    close = start_price + np.cumsum(np.random.randn(days) * 2)
    close = np.maximum(close, 1.0)  # keep positive
    return pd.DataFrame(
        {
            "Open": close * (1 + np.random.randn(days) * 0.005),
            "High": close * (1 + abs(np.random.randn(days) * 0.01)),
            "Low": close * (1 - abs(np.random.randn(days) * 0.01)),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 10_000_000, days),
        },
        index=dates,
    )
