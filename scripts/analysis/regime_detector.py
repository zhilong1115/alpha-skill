"""Market regime detection using SPY and VIX."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from scripts.core.data_pipeline import get_price_data


def _get_vix_level() -> float:
    """Fetch current VIX level.

    Returns:
        Current VIX value, or 20.0 as default.
    """
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 20.0


def detect_regime(spy_data: Optional[pd.DataFrame] = None) -> str:
    """Detect current market regime.

    Uses SMA200 trend, VIX level, and drawdown from highs.

    Args:
        spy_data: Optional pre-fetched SPY price DataFrame. Fetched if None.

    Returns:
        One of "bull", "bear", or "sideways".
    """
    try:
        info = detect_regime_detailed(spy_data)
        return info["regime"]
    except Exception:
        return "sideways"


def detect_regime_detailed(spy_data: Optional[pd.DataFrame] = None) -> dict:
    """Detect market regime with detailed metrics.

    Args:
        spy_data: Optional pre-fetched SPY price DataFrame. Fetched if None.

    Returns:
        Dict with regime, vix_level, sma200_trend, drawdown_from_high,
        momentum_20d, and confidence.
    """
    defaults = {
        "regime": "sideways",
        "vix_level": 20.0,
        "sma200_trend": "below",
        "drawdown_from_high": 0.0,
        "momentum_20d": 0.0,
        "confidence": 0.0,
    }

    try:
        if spy_data is None or spy_data.empty:
            spy_data = get_price_data("SPY", period="1y")
        if spy_data is None or spy_data.empty or len(spy_data) < 200:
            return defaults

        close = spy_data["Close"].values if "Close" in spy_data.columns else spy_data["close"].values
        current = float(close[-1])

        # SMA200
        sma200 = float(np.mean(close[-200:]))
        above_sma200 = current > sma200
        sma200_trend = "above" if above_sma200 else "below"

        # Drawdown from 52-week high
        high_52w = float(np.max(close[-252:])) if len(close) >= 252 else float(np.max(close))
        drawdown = (current / high_52w - 1.0) * 100 if high_52w > 0 else 0.0

        # VIX
        vix_level = _get_vix_level()

        # 20-day momentum
        if len(close) >= 20:
            momentum_20d = (current / float(close[-20]) - 1.0) * 100
        else:
            momentum_20d = 0.0

        # Scoring
        bull_score = 0
        bear_score = 0

        if above_sma200:
            bull_score += 2
        else:
            bear_score += 2

        if drawdown > -5:
            bull_score += 1
        elif drawdown < -15:
            bear_score += 2
        elif drawdown < -10:
            bear_score += 1

        if vix_level < 18:
            bull_score += 1
        elif vix_level > 30:
            bear_score += 2
        elif vix_level > 22:
            bear_score += 1

        if momentum_20d > 2:
            bull_score += 1
        elif momentum_20d < -2:
            bear_score += 1

        total = bull_score + bear_score
        if bull_score >= 4:
            regime = "bull"
            confidence = min(1.0, bull_score / total) if total > 0 else 0.5
        elif bear_score >= 4:
            regime = "bear"
            confidence = min(1.0, bear_score / total) if total > 0 else 0.5
        else:
            regime = "sideways"
            confidence = 1.0 - abs(bull_score - bear_score) / max(total, 1)

        return {
            "regime": regime,
            "vix_level": round(vix_level, 2),
            "sma200_trend": sma200_trend,
            "drawdown_from_high": round(drawdown, 2),
            "momentum_20d": round(momentum_20d, 2),
            "confidence": round(confidence, 2),
        }

    except Exception:
        return defaults


def get_regime_adjustment() -> float:
    """Get conviction multiplier based on current market regime.

    Returns:
        Float multiplier: 1.2 for bull, 0.5 for bear, 1.0 for sideways.
    """
    regime = detect_regime()
    multipliers = {
        "bull": 1.2,
        "bear": 0.5,
        "sideways": 1.0,
    }
    return multipliers.get(regime, 1.0)


def get_adaptive_weights(regime: str) -> dict[str, float]:
    """Return signal weights adapted to the current market regime.

    Args:
        regime: Market regime — "bull", "bear", or "sideways".

    Returns:
        Dict mapping signal_name to weight, adjusted for regime.
    """
    if regime == "bull":
        return {
            "RSI_14": 0.15,
            "MACD_12_26_9": 0.30,
            "BBANDS_20_2": 0.10,
            "SMA_50_200": 0.30,
            "VOLUME_ANOMALY": 0.15,
            "momentum_12_1": 0.30,
            "mean_reversion_bb_rsi": 0.05,
            "news_sentiment": 0.10,
        }
    elif regime == "bear":
        return {
            "RSI_14": 0.25,
            "MACD_12_26_9": 0.15,
            "BBANDS_20_2": 0.25,
            "SMA_50_200": 0.10,
            "VOLUME_ANOMALY": 0.10,
            "momentum_12_1": 0.05,
            "mean_reversion_bb_rsi": 0.30,
            "news_sentiment": 0.15,
        }
    else:  # sideways
        return {
            "RSI_14": 0.20,
            "MACD_12_26_9": 0.20,
            "BBANDS_20_2": 0.20,
            "SMA_50_200": 0.20,
            "VOLUME_ANOMALY": 0.15,
            "momentum_12_1": 0.15,
            "mean_reversion_bb_rsi": 0.15,
            "news_sentiment": 0.10,
        }
