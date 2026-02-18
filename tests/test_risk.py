"""Tests for the risk manager module."""

from __future__ import annotations

import pytest

from scripts.core.risk_manager import approve_trade


class TestApproveTrade:
    """Tests for approve_trade function."""

    def test_normal_buy_approved(self) -> None:
        """A small buy within all limits should be approved."""
        approved, qty, reason = approve_trade(
            "AAPL", "buy", 10, 150.0, 100_000, []
        )
        assert approved is True
        assert qty == 10

    def test_sell_always_approved(self) -> None:
        """Sell orders should always be approved regardless of limits."""
        approved, qty, reason = approve_trade(
            "AAPL", "sell", 1000, 150.0, 100_000, []
        )
        assert approved is True
        assert qty == 1000

    def test_position_size_limit(self) -> None:
        """Trade exceeding max position size should be sized down or rejected."""
        # 5% of 100k = $5000. Buying 100 shares @ $150 = $15000 > $5000
        approved, qty, reason = approve_trade(
            "AAPL", "buy", 100, 150.0, 100_000, []
        )
        # Should be sized down to fit within 5%
        assert qty <= 33  # $5000 / $150 = 33

    def test_max_positions_limit(self) -> None:
        """Should reject new position when max positions reached."""
        positions = [{"ticker": f"TICK{i}", "market_value": 1000} for i in range(15)]
        approved, qty, reason = approve_trade(
            "NEW", "buy", 10, 50.0, 100_000, positions
        )
        assert approved is False
        assert qty == 0

    def test_existing_ticker_bypasses_max_positions(self) -> None:
        """Adding to an existing position should bypass max positions check."""
        positions = [{"ticker": f"TICK{i}", "market_value": 1000} for i in range(15)]
        positions[0]["ticker"] = "AAPL"
        approved, qty, reason = approve_trade(
            "AAPL", "buy", 5, 50.0, 100_000, positions
        )
        # Should not be rejected for max positions since AAPL is already held
        # May still be sized down for other reasons
        assert approved is True or "position size" in reason.lower()

    def test_cash_reserve_enforcement(self) -> None:
        """Should reject or size down when cash reserve would be violated."""
        # 20% of $100k = $20k reserve. If positions use $85k, only $15k cash
        # Buying $10k more would leave $5k < $20k reserve
        positions = [{"ticker": f"T{i}", "market_value": 8500} for i in range(10)]
        approved, qty, reason = approve_trade(
            "NEW", "buy", 100, 100.0, 100_000, positions
        )
        # Trade value = $10,000. Cash = $100k - $85k = $15k.
        # After trade: $5k < $20k reserve. Should be sized down.
        if approved:
            assert qty < 100
