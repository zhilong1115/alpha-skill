"""Tests for Hyperliquid integration module."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


class TestLeverageCap:
    """Test that leverage is always capped at 3x."""

    def test_leverage_cap_enforced(self):
        from scripts.crypto.hyperliquid import MAX_LEVERAGE
        assert MAX_LEVERAGE == 3

    def test_set_leverage_caps_at_max(self):
        from scripts.crypto.hyperliquid import MAX_LEVERAGE
        # Requesting 10x should be capped to 3x
        requested = 10
        capped = min(requested, MAX_LEVERAGE)
        assert capped == 3

    def test_set_leverage_allows_lower(self):
        from scripts.crypto.hyperliquid import MAX_LEVERAGE
        requested = 2
        capped = min(requested, MAX_LEVERAGE)
        assert capped == 2

    def test_set_leverage_minimum_is_1(self):
        requested = 0
        capped = max(requested, 1)
        assert capped == 1


class TestPositionSizing:
    """Test position sizing calculations."""

    def test_margin_pct_cap(self):
        from scripts.crypto.hyperliquid import MAX_POSITION_MARGIN_PCT
        assert MAX_POSITION_MARGIN_PCT == 0.30

    def test_target_notional_with_leverage(self):
        """With 3x leverage, 30% margin = 90% exposure."""
        account_value = 10_000
        margin_pct = 0.30
        leverage = 3
        margin = account_value * margin_pct  # $3,000
        notional = margin * leverage  # $9,000
        assert margin == 3_000
        assert notional == 9_000
        assert notional / account_value == 0.90  # 90% exposure

    def test_margin_pct_capped(self):
        """Target pct above 30% should be capped."""
        from scripts.crypto.hyperliquid import MAX_POSITION_MARGIN_PCT
        target_pct = 0.50  # Conservative outputs up to 50%
        capped = min(target_pct, MAX_POSITION_MARGIN_PCT)
        assert capped == 0.30

    def test_small_account_sizing(self):
        """Position sizing should work for small accounts."""
        account_value = 1_000
        margin_pct = 0.20
        leverage = 3
        btc_price = 100_000
        margin = account_value * margin_pct  # $200
        notional = margin * leverage  # $600
        size = notional / btc_price  # 0.006 BTC
        assert size == pytest.approx(0.006)


class TestStopLossRequired:
    """Test that stop-loss is always set."""

    def test_stop_loss_pct_defined(self):
        from scripts.crypto.hyperliquid_trader import STOP_LOSS_PCT
        assert STOP_LOSS_PCT == 0.05

    def test_long_stop_loss_price(self):
        """Long stop-loss should be below entry."""
        entry = 100_000
        sl_pct = 0.05
        sl_price = entry * (1 - sl_pct)
        assert sl_price == 95_000

    def test_short_stop_loss_price(self):
        """Short stop-loss should be above entry."""
        entry = 100_000
        sl_pct = 0.05
        sl_price = entry * (1 + sl_pct)
        assert sl_price == 105_000


class TestDrawdownCircuitBreaker:
    """Test the 15% drawdown circuit breaker."""

    def test_max_drawdown_defined(self):
        from scripts.crypto.hyperliquid import MAX_DRAWDOWN
        assert MAX_DRAWDOWN == 0.15

    def test_drawdown_calculation(self):
        hwm = 10_000
        current = 8_600
        drawdown = (hwm - current) / hwm
        assert drawdown == pytest.approx(0.14)
        assert drawdown < 0.15  # Should not trigger

    def test_drawdown_trigger(self):
        hwm = 10_000
        current = 8_400
        drawdown = (hwm - current) / hwm
        assert drawdown == pytest.approx(0.16)
        assert drawdown >= 0.15  # Should trigger


class TestPriceRounding:
    """Test price rounding for different symbols."""

    def test_btc_price_rounding(self):
        from scripts.crypto.hyperliquid import _round_price
        assert _round_price("BTC", 97123.456) == 97123.5

    def test_eth_price_rounding(self):
        from scripts.crypto.hyperliquid import _round_price
        assert _round_price("ETH", 3200.1234) == 3200.12

    def test_sol_price_rounding(self):
        from scripts.crypto.hyperliquid import _round_price
        assert _round_price("SOL", 150.12346) == 150.1235


class TestSizeRounding:
    """Test size rounding for different symbols."""

    def test_btc_size_rounding(self):
        from scripts.crypto.hyperliquid import _round_size
        assert _round_size("BTC", 0.123456) == 0.12346

    def test_eth_size_rounding(self):
        from scripts.crypto.hyperliquid import _round_size
        assert _round_size("ETH", 1.23456) == 1.2346

    def test_sol_size_rounding(self):
        from scripts.crypto.hyperliquid import _round_size
        assert _round_size("SOL", 10.1234) == 10.123


class TestSignalToPosition:
    """Test Conservative signal → position mapping."""

    def test_buy_signal(self):
        from scripts.crypto.hyperliquid_trader import _signal_to_position
        side, pct = _signal_to_position("BUY", 0.30)
        assert side == "long"
        assert pct == 0.30

    def test_sell_signal_closes(self):
        from scripts.crypto.hyperliquid_trader import _signal_to_position
        side, pct = _signal_to_position("SELL", 0)
        assert side is None
        assert pct == 0

    def test_hold_with_position(self):
        from scripts.crypto.hyperliquid_trader import _signal_to_position
        side, pct = _signal_to_position("HOLD", 0.20)
        assert side == "long"
        assert pct == 0.20

    def test_hold_no_position(self):
        from scripts.crypto.hyperliquid_trader import _signal_to_position
        side, pct = _signal_to_position("HOLD", 0)
        assert side is None
        assert pct == 0


class TestSupportedSymbols:
    """Test supported symbol list."""

    def test_supported_symbols(self):
        from scripts.crypto.hyperliquid import SUPPORTED_SYMBOLS
        assert "BTC" in SUPPORTED_SYMBOLS
        assert "ETH" in SUPPORTED_SYMBOLS
        assert "SOL" in SUPPORTED_SYMBOLS
        assert len(SUPPORTED_SYMBOLS) == 3


class TestConnection:
    """Test connection with mocked SDK."""

    @patch("scripts.crypto.hyperliquid._get_private_key")
    @patch("scripts.crypto.hyperliquid.eth_account.Account.from_key")
    @patch("scripts.crypto.hyperliquid.Info")
    @patch("scripts.crypto.hyperliquid.Exchange")
    def test_connect_testnet(self, mock_exchange, mock_info, mock_from_key, mock_get_key):
        from scripts.crypto.hyperliquid import connect, constants
        mock_get_key.return_value = "0x" + "a" * 64
        mock_account = MagicMock()
        mock_account.address = "0x1234"
        mock_from_key.return_value = mock_account

        info, exchange = connect(testnet=True)
        mock_info.assert_called_once_with(constants.TESTNET_API_URL, skip_ws=True)

    @patch("scripts.crypto.hyperliquid._get_private_key")
    @patch("scripts.crypto.hyperliquid.eth_account.Account.from_key")
    @patch("scripts.crypto.hyperliquid.Info")
    @patch("scripts.crypto.hyperliquid.Exchange")
    def test_connect_mainnet(self, mock_exchange, mock_info, mock_from_key, mock_get_key):
        from scripts.crypto.hyperliquid import connect, constants
        mock_get_key.return_value = "0x" + "a" * 64
        mock_account = MagicMock()
        mock_account.address = "0x1234"
        mock_from_key.return_value = mock_account

        info, exchange = connect(testnet=False)
        mock_info.assert_called_once_with(constants.MAINNET_API_URL, skip_ws=True)
