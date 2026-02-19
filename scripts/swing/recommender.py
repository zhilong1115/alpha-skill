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

    # 4. Reddit buzz boost
    reddit_buzz = {}
    try:
        from scripts.analysis.sentiment_scraper import discover_trending_tickers
        trending = discover_trending_tickers(min_mentions=3)
        for t in trending:
            ticker = t["ticker"]
            mentions = t["mentions"]
            reddit_buzz[ticker] = mentions
        logger.info("Reddit buzz: %d trending tickers (top: %s)",
                    len(reddit_buzz),
                    ", ".join(f"{t['ticker']}({t['mentions']})" for t in trending[:5]))
    except Exception as e:
        logger.warning("Reddit buzz scan failed: %s", e)

    # 5. Conviction scoring
    convictions = compute_conviction(combined, weights)

    # Apply Reddit buzz boost: +0.05 per 3 mentions, capped at +0.15
    if reddit_buzz:
        def _apply_buzz(row):
            mentions = reddit_buzz.get(row["ticker"], 0)
            if mentions >= 3:
                boost = min(mentions / 3 * 0.05, 0.15)
                return row["conviction_score"] + boost
            return row["conviction_score"]
        convictions["conviction_score"] = convictions.apply(_apply_buzz, axis=1)
        convictions["reddit_mentions"] = convictions["ticker"].map(lambda t: reddit_buzz.get(t, 0))

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

            # --- ATR for volatility-based sizing ---
            tr = pd.concat([
                df["High"] - df["Low"],
                (df["High"] - df["Close"].shift()).abs(),
                (df["Low"] - df["Close"].shift()).abs(),
            ], axis=1).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = atr14 / current * 100  # ATR as % of price

            # --- Support: ATR-based stop (1.5x ATR below current) ---
            recent_low = float(df["Low"].iloc[-20:].min())
            atr_stop = current - 1.5 * atr14
            # Use the tighter of: 2% below recent low, or 1.5x ATR
            stop_loss = round(max(recent_low * 0.98, atr_stop), 2)
            # But never closer than 1% (avoid stop hunting)
            if stop_loss > current * 0.99:
                stop_loss = round(current * 0.97, 2)
            stop_loss_pct = round((current - stop_loss) / current * 100, 1)

            # --- Target: multi-level resistance scan ---
            # Collect potential resistance levels above current price
            resistance_levels = []
            recent_high_20 = float(df["High"].iloc[-20:].max())
            recent_high_50 = float(df["High"].iloc[-50:].max()) if len(df) >= 50 else recent_high_20
            sma200 = float(df["Close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

            # Level 1: 20-day high
            if recent_high_20 > current * 1.01:
                resistance_levels.append(("20日高点", recent_high_20))
            # Level 2: 50-day high
            if recent_high_50 > current * 1.01 and recent_high_50 != recent_high_20:
                resistance_levels.append(("50日高点", recent_high_50))
            # Level 3: SMA200 (if above current)
            if sma200 and sma200 > current * 1.01:
                resistance_levels.append(("200日均线", sma200))
            # Level 4: 52-week high
            if high_52w > current * 1.05:
                resistance_levels.append(("52周高点", high_52w))
            # Level 5: Round number above (e.g., $100, $150, $200)
            round_levels = [n for n in range(int(current / 10) * 10 + 10,
                                              int(current / 10) * 10 + 60, 10)
                           if n > current * 1.02]
            if round_levels:
                resistance_levels.append(("整数关口", float(round_levels[0])))

            # ATR-based target: 2.5x ATR above entry (volatility-adjusted)
            atr_target = current + 2.5 * atr14
            resistance_levels.append(("ATR目标(2.5x)", atr_target))

            # R-multiple target: ensure at least 2:1 R/R
            risk_distance = current - stop_loss
            r_target = current + 2.5 * risk_distance
            resistance_levels.append(("2.5R目标", r_target))

            # Pick the nearest resistance above current (but at least 3% up)
            valid_targets = [(name, lvl) for name, lvl in resistance_levels
                            if lvl > current * 1.03]
            valid_targets.sort(key=lambda x: x[1])

            if valid_targets:
                target_name, target = valid_targets[0]
            else:
                # Fallback: ATR-based
                target_name = "ATR目标"
                target = atr_target

            target = round(target, 2)
            target_pct = round((target - current) / current * 100, 1)

            # Risk/reward ratio
            risk = current - stop_loss
            reward = target - current
            rr_ratio = round(reward / risk, 2) if risk > 0 else 0

            # Only recommend if R/R >= 1.5
            if rr_ratio < 1.5:
                # Try next resistance level for better R/R
                for name, lvl in valid_targets[1:]:
                    reward2 = lvl - current
                    rr2 = round(reward2 / risk, 2) if risk > 0 else 0
                    if rr2 >= 1.5:
                        target = round(lvl, 2)
                        target_pct = round((target - current) / current * 100, 1)
                        target_name = name
                        rr_ratio = rr2
                        break
                else:
                    continue  # No target gives R/R >= 1.5

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

            mentions = reddit_buzz.get(ticker, 0)
            if mentions >= 5:
                reasons.insert(0, f"🔥 Reddit热门 ({mentions}次提及)")
            elif mentions >= 3:
                reasons.append(f"📢 Reddit关注 ({mentions}次提及)")

            recs.append({
                "ticker": ticker,
                "conviction": round(score, 3),
                "reddit_mentions": mentions,
                "atr": round(atr14, 2),
                "atr_pct": round(atr_pct, 1),
                "target_basis": target_name,
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
        target_basis = r.get("target_basis", "")
        atr_pct = r.get("atr_pct", 0)
        lines.extend([
            f"**{i}. {r['ticker']}** — Conviction {r['conviction']:.2f} (ATR {atr_pct:.1f}%)",
            f"   💰 现价 ${r['current_price']:.2f}",
            f"   🎯 目标 ${r['target_price']:.2f} (+{r['target_pct']:.1f}%) [{target_basis}]",
            f"   🛑 止损 ${r['stop_loss']:.2f} (-{r['stop_loss_pct']:.1f}%)",
            f"   ⚖️ R/R {r['risk_reward']:.1f}:1",
            f"   📝 {reasons_str}",
            "",
        ])

    lines.append("⚠️ 以上为系统推荐，请自行判断后在Robinhood操作。买入后告诉我，我帮你跟踪。")
    return "\n".join(lines)
