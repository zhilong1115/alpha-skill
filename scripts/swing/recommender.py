"""Swing/Position trade recommender: daily picks for manual trading on Robinhood."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from scripts.core.data_pipeline import get_price_data
from scripts.core.signal_engine import compute_signals
from scripts.core.conviction import compute_conviction
from scripts.analysis.regime_detector import detect_regime_detailed, get_adaptive_weights

logger = logging.getLogger(__name__)


def generate_recommendations(
    tickers: list[str] | None = None,
    top_n: int = 5,
    min_conviction: float = 0.4,
) -> list[dict]:
    """Generate daily swing trade recommendations.

    Args:
        tickers: Universe to scan. If None, uses full universe.
        top_n: Number of recommendations to return.
        min_conviction: Minimum conviction threshold.

    Returns:
        List of recommendation dicts with full analysis.
    """
    if tickers is None:
        from scripts.utils.universe import get_full_universe
        u = get_full_universe()
        tickers = u["all_unique"]
        logger.info("Scanning full universe: %d tickers", len(tickers))

    # 1. Regime detection
    regime_info = detect_regime_detailed()
    regime = regime_info["regime"]
    weights = get_adaptive_weights(regime)
    logger.info("Market regime: %s", regime)

    # 2. Compute signals for all tickers
    all_signals = []
    for ticker in tickers:
        try:
            df = get_price_data(ticker, period="1y")
            sigs = compute_signals(ticker, df)
            if not sigs.empty:
                all_signals.append(sigs)
        except Exception:
            continue

    if not all_signals:
        return []

    combined = pd.concat(all_signals, ignore_index=True)

    # 3. Strategy signals
    try:
        from scripts.strategies.momentum_factor import generate_momentum_signals
        mom = generate_momentum_signals(tickers)
        if not mom.empty:
            all_signals.append(mom)
            combined = pd.concat(all_signals, ignore_index=True)
    except Exception:
        pass

    # 4. Conviction scoring
    convictions = compute_conviction(combined, weights)
    convictions = convictions[convictions["conviction_score"] >= min_conviction]
    convictions = convictions.sort_values("conviction_score", ascending=False)

    # 5. Build recommendations
    recs = []
    for _, row in convictions.head(top_n * 2).iterrows():  # Get extra for filtering
        ticker = row["ticker"]
        score = row["conviction_score"]

        try:
            df = get_price_data(ticker, period="6mo")
            if df.empty or len(df) < 20:
                continue

            current = float(df["Close"].iloc[-1])
            sma20 = float(df["Close"].rolling(20).mean().iloc[-1])
            sma50 = float(df["Close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else sma20
            high_52w = float(df["High"].max())
            low_52w = float(df["Low"].min())
            avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])

            # Support/resistance levels
            recent_low = float(df["Low"].iloc[-20:].min())
            recent_high = float(df["High"].iloc[-20:].max())

            # Calculate target (next resistance) and stop (below support)
            stop_loss = round(recent_low * 0.98, 2)  # 2% below recent low
            stop_loss_pct = round((current - stop_loss) / current * 100, 1)

            # Target: based on recent range or 10% upside
            range_target = recent_high * 1.02
            pct_target = current * 1.10
            target = round(min(range_target, pct_target), 2)
            target_pct = round((target - current) / current * 100, 1)

            # Risk/reward ratio
            risk = current - stop_loss
            reward = target - current
            rr_ratio = round(reward / risk, 2) if risk > 0 else 0

            # Only recommend if R/R >= 1.5
            if rr_ratio < 1.5:
                continue

            # Determine reasoning
            reasons = []
            if current > sma20 > sma50:
                reasons.append("上升趋势 (price > SMA20 > SMA50)")
            elif current < sma20 < sma50:
                reasons.append("下降趋势中的反弹机会")

            if current < sma20 * 0.95:
                reasons.append("超卖回调，接近支撑")
            if avg_vol > 1_000_000:
                reasons.append("流动性好")

            from_52w_high = (current - high_52w) / high_52w * 100
            if from_52w_high > -10:
                reasons.append(f"接近52周高点 ({from_52w_high:+.1f}%)")
            elif from_52w_high < -30:
                reasons.append(f"远低于52周高点 ({from_52w_high:+.1f}%)，可能超跌")

            recs.append({
                "ticker": ticker,
                "conviction": round(score, 3),
                "current_price": current,
                "target_price": target,
                "target_pct": target_pct,
                "stop_loss": stop_loss,
                "stop_loss_pct": stop_loss_pct,
                "risk_reward": rr_ratio,
                "sma20": round(sma20, 2),
                "sma50": round(sma50, 2),
                "avg_volume": int(avg_vol),
                "from_52w_high_pct": round(from_52w_high, 1),
                "regime": regime,
                "reasons": reasons,
            })

            if len(recs) >= top_n:
                break

        except Exception as e:
            logger.warning("Error analyzing %s: %s", ticker, e)
            continue

    return recs


def format_recommendation_message(recs: list[dict]) -> str:
    """Format recommendations as a Telegram message.

    Returns:
        Formatted string in Chinese+English.
    """
    if not recs:
        return "📊 今日无推荐 — 没有符合条件的标的 (conviction ≥ 0.4, R/R ≥ 1.5)"

    regime = recs[0].get("regime", "UNKNOWN") if recs else "UNKNOWN"
    lines = [
        f"📊 **今日Swing推荐** ({len(recs)}只)",
        f"市场环境: {regime}",
        "",
    ]

    for i, r in enumerate(recs, 1):
        reasons_str = " | ".join(r["reasons"][:2]) if r["reasons"] else "综合信号"
        lines.extend([
            f"**{i}. {r['ticker']}** — Conviction {r['conviction']:.2f}",
            f"   💰 现价 ${r['current_price']:.2f}",
            f"   🎯 目标 ${r['target_price']:.2f} (+{r['target_pct']:.1f}%)",
            f"   🛑 止损 ${r['stop_loss']:.2f} (-{r['stop_loss_pct']:.1f}%)",
            f"   ⚖️ 风险回报比 {r['risk_reward']:.1f}:1",
            f"   📝 {reasons_str}",
            "",
        ])

    lines.append("⚠️ 以上为系统推荐，请自行判断后在Robinhood操作。买入后告诉我，我帮你跟踪。")
    return "\n".join(lines)
