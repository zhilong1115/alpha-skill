"""Market-wide health check: SPY, VIX, sectors, regime."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLC": "Communication",
    "XLY": "Consumer Disc.",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials",
}


class MarketPulse:
    """Quick market-wide health check."""

    def __init__(self) -> None:
        pass

    def get_pulse(self) -> dict:
        """Get current market conditions.

        Returns:
            Dict with spy_price, spy_change_pct, vix_level, regime,
            sector_leaders, sector_laggards, breadth, key_levels.
        """
        result: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "spy_price": 0.0,
            "spy_change_pct": 0.0,
            "vix_level": 0.0,
            "regime": "unknown",
            "sector_leaders": [],
            "sector_laggards": [],
            "breadth": 0.0,
            "key_levels": {},
        }

        # SPY
        try:
            spy = yf.Ticker("SPY")
            hist = spy.history(period="5d")
            if hist is not None and len(hist) >= 2:
                result["spy_price"] = round(float(hist["Close"].iloc[-1]), 2)
                prev_close = float(hist["Close"].iloc[-2])
                if prev_close > 0:
                    result["spy_change_pct"] = round(
                        (result["spy_price"] - prev_close) / prev_close * 100, 2
                    )

                # Key levels from recent data
                spy_long = spy.history(period="3mo")
                if spy_long is not None and not spy_long.empty:
                    result["key_levels"] = {
                        "support": round(float(spy_long["Low"].rolling(20).min().iloc[-1]), 2),
                        "resistance": round(float(spy_long["High"].rolling(20).max().iloc[-1]), 2),
                        "sma_50": round(float(spy_long["Close"].rolling(50).mean().iloc[-1]), 2)
                        if len(spy_long) >= 50 else None,
                    }
        except Exception as e:
            logger.warning("SPY fetch failed: %s", e)

        # VIX
        try:
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="5d")
            if vix_hist is not None and not vix_hist.empty:
                result["vix_level"] = round(float(vix_hist["Close"].iloc[-1]), 2)
        except Exception as e:
            logger.warning("VIX fetch failed: %s", e)

        # Regime
        result["regime"] = self._determine_regime(
            result["spy_change_pct"], result["vix_level"]
        )

        # Sectors
        sector_changes: dict[str, float] = {}
        above_50ma = 0
        total_sectors = 0

        for etf, name in SECTOR_ETFS.items():
            try:
                t = yf.Ticker(etf)
                h = t.history(period="5d")
                if h is not None and len(h) >= 2:
                    last = float(h["Close"].iloc[-1])
                    prev = float(h["Close"].iloc[-2])
                    chg = ((last - prev) / prev * 100) if prev > 0 else 0
                    sector_changes[name] = round(chg, 2)

                    # Breadth estimate: is sector above 50d MA?
                    h_long = t.history(period="3mo")
                    if h_long is not None and len(h_long) >= 50:
                        sma50 = float(h_long["Close"].rolling(50).mean().iloc[-1])
                        if last > sma50:
                            above_50ma += 1
                    total_sectors += 1
            except Exception:
                continue

        if sector_changes:
            sorted_sectors = sorted(sector_changes.items(), key=lambda x: x[1], reverse=True)
            result["sector_leaders"] = [
                {"sector": s, "change_pct": c} for s, c in sorted_sectors[:3]
            ]
            result["sector_laggards"] = [
                {"sector": s, "change_pct": c} for s, c in sorted_sectors[-3:]
            ]

        result["breadth"] = round(above_50ma / total_sectors * 100, 1) if total_sectors > 0 else 0.0

        return result

    def should_trade_today(self) -> tuple[bool, str]:
        """Determine if market conditions are suitable for trading.

        Returns:
            Tuple of (should_trade, reason).
        """
        pulse = self.get_pulse()

        vix = pulse["vix_level"]
        spy_chg = pulse["spy_change_pct"]

        if vix > 35:
            return False, f"VIX too high ({vix:.1f}) — extreme fear, stay out."
        if spy_chg < -3:
            return False, f"SPY down {spy_chg:.1f}% — crash day, no new positions."
        if pulse["spy_price"] == 0:
            return False, "Cannot fetch market data — market may be closed."

        return True, f"Market OK. VIX={vix:.1f}, SPY {spy_chg:+.1f}%, regime={pulse['regime']}."

    def format_pulse(self) -> str:
        """Format market pulse as readable string."""
        pulse = self.get_pulse()

        regime_icons = {"bull": "🟢", "bear": "🔴", "sideways": "⚪", "unknown": "❓"}
        icon = regime_icons.get(pulse["regime"], "❓")

        lines = [
            "📈 Market Pulse",
            "=" * 40,
            f"  SPY:    ${pulse['spy_price']:.2f}  ({pulse['spy_change_pct']:+.2f}%)",
            f"  VIX:    {pulse['vix_level']:.1f}",
            f"  Regime: {icon} {pulse['regime'].upper()}",
            f"  Breadth: {pulse['breadth']:.0f}% sectors above 50-day MA",
        ]

        kl = pulse.get("key_levels", {})
        if kl:
            lines.append(f"  Support: ${kl.get('support', 'N/A')}  Resistance: ${kl.get('resistance', 'N/A')}")

        if pulse["sector_leaders"]:
            lines.append("\n  📊 Sector Leaders:")
            for s in pulse["sector_leaders"]:
                lines.append(f"    🟢 {s['sector']:<20} {s['change_pct']:+.2f}%")

        if pulse["sector_laggards"]:
            lines.append("  📉 Sector Laggards:")
            for s in pulse["sector_laggards"]:
                lines.append(f"    🔴 {s['sector']:<20} {s['change_pct']:+.2f}%")

        should_trade, reason = self.should_trade_today()
        lines.append(f"\n  {'✅' if should_trade else '⛔'} {reason}")

        return "\n".join(lines)

    @staticmethod
    def _determine_regime(spy_change_pct: float, vix: float) -> str:
        """Simple regime determination from SPY change and VIX."""
        if vix > 30 or spy_change_pct < -2:
            return "bear"
        if vix < 18 and spy_change_pct > 0.5:
            return "bull"
        return "sideways"
