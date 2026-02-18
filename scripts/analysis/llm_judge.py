"""LLM subjective judgment layer for trade decisions.

After the quantitative signal engine produces trade candidates, this module
reads the raw news headlines, Reddit posts, and market context, then produces
a human-like judgment: adjust conviction up/down, or veto entirely.

The judgment is rendered as structured text that the cron agent (Alpha) can
parse when running auto-trade.  In CLI mode it prints to stdout.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JUDGMENT_LOG_PATH = PROJECT_ROOT / "data" / "judgments"


@dataclass
class LLMJudgment:
    """Result of LLM subjective review on a trade candidate."""

    ticker: str
    original_conviction: float
    adjusted_conviction: float
    adjustment: float  # delta
    action: str  # "proceed", "boost", "reduce", "veto"
    reasoning: str  # one-line explanation
    news_digest: list[str]  # key headlines considered
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def gather_context(ticker: str, max_news: int = 8) -> dict:
    """Gather raw context for LLM judgment: news, price action, fundamentals.

    Returns a dict with:
        - headlines: list of recent news headline strings
        - price_action: dict with recent price stats
        - volume_info: current vs average volume
    """
    context: dict = {"ticker": ticker, "headlines": [], "price_action": {}, "volume_info": {}}

    try:
        t = yf.Ticker(ticker)

        # News headlines (raw text for LLM to read)
        news_items = t.news or []
        for item in news_items[:max_news]:
            content = item.get("content", item)
            title = content.get("title", item.get("title", ""))
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else item.get("publisher", "")
            pub_date = content.get("pubDate", item.get("providerPublishTime", ""))
            if title:
                context["headlines"].append(f"[{source}] {title} ({pub_date})")

        # Price action
        hist_5d = t.history(period="5d")
        hist_1mo = t.history(period="1mo")
        if hist_5d is not None and not hist_5d.empty:
            last = float(hist_5d["Close"].iloc[-1])
            open_5d = float(hist_5d["Open"].iloc[0])
            context["price_action"] = {
                "current_price": round(last, 2),
                "5d_change_pct": round((last - open_5d) / open_5d * 100, 2) if open_5d > 0 else 0,
                "5d_high": round(float(hist_5d["High"].max()), 2),
                "5d_low": round(float(hist_5d["Low"].min()), 2),
            }
            if hist_1mo is not None and not hist_1mo.empty:
                open_1mo = float(hist_1mo["Open"].iloc[0])
                context["price_action"]["1mo_change_pct"] = round((last - open_1mo) / open_1mo * 100, 2) if open_1mo > 0 else 0

        # Volume
        if hist_5d is not None and not hist_5d.empty and hist_1mo is not None and not hist_1mo.empty:
            today_vol = int(hist_5d["Volume"].iloc[-1])
            avg_vol = int(hist_1mo["Volume"].mean()) if len(hist_1mo) > 0 else 1
            context["volume_info"] = {
                "today_volume": today_vol,
                "avg_volume": avg_vol,
                "ratio": round(today_vol / avg_vol, 2) if avg_vol > 0 else 0,
            }
    except Exception as e:
        logger.warning("Context gather failed for %s: %s", ticker, e)

    return context


def build_judgment_prompt(
    ticker: str,
    conviction: float,
    side: str,
    reason: str,
    context: dict,
    regime: str = "SIDEWAYS",
) -> str:
    """Build a structured prompt for LLM judgment.

    This prompt is designed to be evaluated by the Alpha agent (an LLM)
    during the cron-triggered auto-trade cycle.  It contains all raw data
    needed for a subjective call.
    """
    headlines_text = "\n".join(f"  - {h}" for h in context.get("headlines", [])) or "  (no recent news)"
    pa = context.get("price_action", {})
    vi = context.get("volume_info", {})

    prompt = f"""## LLM Trade Review: {ticker}

**Quantitative Signal**: {side.upper()} conviction={conviction:.3f}
**Regime**: {regime}
**Signal Reason**: {reason}

### Recent News
{headlines_text}

### Price Action
- Current: ${pa.get('current_price', '?')}
- 5-day: {pa.get('5d_change_pct', '?')}% | 1-month: {pa.get('1mo_change_pct', '?')}%
- 5d range: ${pa.get('5d_low', '?')} - ${pa.get('5d_high', '?')}

### Volume
- Today: {vi.get('today_volume', '?'):,} | Avg: {vi.get('avg_volume', '?'):,} | Ratio: {vi.get('ratio', '?')}x

### Your Task
Read the news headlines carefully. Consider:
1. Is there a macro catalyst (Fed, tariffs, geopolitics) that the quant model can't see?
2. Is the Reddit/news sentiment genuine or just noise/hype?
3. Does the price action confirm or contradict the signal?
4. Any red flags the model missed?

**Output one of:**
- PROCEED (conviction unchanged, signal looks clean)
- BOOST +X.XX (increase conviction, with reason)
- REDUCE -X.XX (decrease conviction, with reason)
- VETO (kill the trade entirely, with reason)
"""
    return prompt


def apply_rule_based_judgment(
    ticker: str,
    conviction: float,
    side: str,
    context: dict,
    regime: str = "SIDEWAYS",
) -> LLMJudgment:
    """Rule-based fallback judgment when no LLM is available.

    Applies heuristic adjustments based on news urgency, volume, and regime.
    This runs locally without any API call.
    """
    adjustment = 0.0
    reasons: list[str] = []
    headlines = context.get("headlines", [])
    vi = context.get("volume_info", {})
    pa = context.get("price_action", {})

    # --- News-based adjustments ---
    critical_keywords = [
        "fed", "rate", "tariff", "war", "sanction", "bankrupt", "fraud",
        "sec investigation", "default", "recession", "shutdown",
        "impeach", "emergency", "crash",
    ]
    positive_catalysts = [
        "beat", "upgrade", "record revenue", "fda approv", "contract win",
        "dividend hike", "buyback", "acquisition", "partnership",
    ]
    negative_catalysts = [
        "miss", "downgrade", "guidance cut", "recall", "lawsuit",
        "layoff", "restructur", "debt", "dilut",
    ]

    headlines_lower = " ".join(headlines).lower()

    # Regime-adaptive penalty scaling (less aggressive in bull markets)
    regime_scale = {"BULL": 0.5, "SIDEWAYS": 1.0, "BEAR": 1.3, "VOLATILE": 1.2}.get(regime, 1.0)

    # --- News-based adjustments ---
    macro_hits = [kw for kw in critical_keywords if kw in headlines_lower]
    if macro_hits:
        if side == "buy":
            adjustment -= 0.12 * regime_scale
            reasons.append(f"Macro risk detected: {', '.join(macro_hits[:3])}")
        else:
            adjustment += 0.10
            reasons.append(f"Macro catalyst supports sell: {', '.join(macro_hits[:3])}")

    # Positive catalysts — BOOSTED: more generous, regime-aware
    pos_hits = [kw for kw in positive_catalysts if kw in headlines_lower]
    if pos_hits and side == "buy":
        boost = 0.10 if regime in ("BULL", "SIDEWAYS") else 0.05
        if len(pos_hits) >= 2:
            boost += 0.05  # multiple positive catalysts = strong signal
        adjustment += boost
        reasons.append(f"Positive catalyst: {', '.join(pos_hits[:3])}")

    # Negative catalysts
    neg_hits = [kw for kw in negative_catalysts if kw in headlines_lower]
    if neg_hits and side == "buy":
        adjustment -= 0.08 * regime_scale
        reasons.append(f"Negative catalyst: {', '.join(neg_hits[:2])}")

    # --- Volume confirmation (now can boost) ---
    vol_ratio = vi.get("ratio", 1.0)
    change_5d = pa.get("5d_change_pct", 0)

    if vol_ratio > 2.0:
        if conviction > 0.35 and change_5d > 0:
            # High volume + positive price + decent conviction = momentum confirmation
            adjustment += 0.06
            reasons.append(f"Volume confirms momentum: {vol_ratio:.1f}x vol, +{change_5d:.1f}% 5d")
        elif conviction <= 0.35 and vol_ratio > 3.0:
            adjustment -= 0.05
            reasons.append(f"High volume but weak conviction: {vol_ratio:.1f}x vol")

    # --- Price action: momentum confirmation (NEW — can boost) ---
    change_1mo = pa.get("1mo_change_pct", 0)
    if side == "buy":
        if 2 < change_5d < 8 and change_1mo > 0:
            # Healthy uptrend — not chasing, not falling
            adjustment += 0.04
            reasons.append(f"Healthy trend: +{change_5d:.1f}% 5d, +{change_1mo:.1f}% 1mo")
        elif change_5d < -8:
            adjustment -= 0.08
            reasons.append(f"Falling knife: {change_5d:.1f}% in 5 days")
        elif change_5d > 12:
            adjustment -= 0.04
            reasons.append(f"Extended runup: +{change_5d:.1f}% in 5 days")

    # --- Regime adjustment (softened for BULL) ---
    if regime == "VOLATILE" and side == "buy":
        adjustment -= 0.04
        reasons.append("Volatile regime — slight caution")
    elif regime == "BEAR" and side == "buy":
        adjustment -= 0.06
        reasons.append("Bear regime — caution on longs")
    elif regime == "BULL" and side == "buy" and not macro_hits and not neg_hits:
        adjustment += 0.03
        reasons.append("Bull regime — trend tailwind")

    # Compute final
    adjusted = max(0.0, min(1.0, conviction + adjustment))
    adjustment = round(adjusted - conviction, 3)

    if adjusted <= 0.05:
        action = "veto"
    elif adjustment > 0.03:
        action = "boost"
    elif adjustment < -0.03:
        action = "reduce"
    else:
        action = "proceed"

    reasoning = "; ".join(reasons) if reasons else "No significant factors — signal looks clean"

    return LLMJudgment(
        ticker=ticker,
        original_conviction=round(conviction, 3),
        adjusted_conviction=round(adjusted, 3),
        adjustment=adjustment,
        action=action,
        reasoning=reasoning,
        news_digest=[h[:80] for h in headlines[:5]],
    )


def review_trade_ideas(
    ideas: list[dict],
    regime: str = "SIDEWAYS",
) -> list[dict]:
    """Review a list of trade ideas through the judgment layer.

    For each idea, gathers context and applies judgment. Returns the
    ideas list with added 'judgment' field and updated conviction.

    Args:
        ideas: list of dicts with ticker, conviction, side, etc.
        regime: current market regime string.

    Returns:
        Updated ideas list (filtered — vetoed ideas removed).
    """
    reviewed: list[dict] = []

    for idea in ideas:
        ticker = idea["ticker"]
        conviction = idea.get("conviction", 0)
        side = idea.get("side", "buy")
        reason = idea.get("reason", "")

        # Gather raw context
        context = gather_context(ticker)

        # Build the prompt (for logging / future LLM integration)
        prompt = build_judgment_prompt(ticker, conviction, side, reason, context, regime)

        # Apply rule-based judgment (will be replaced by LLM call in cron)
        judgment = apply_rule_based_judgment(ticker, conviction, side, context, regime)

        # Log
        _log_judgment(judgment, prompt)

        if judgment.action == "veto":
            logger.info("VETO %s: %s", ticker, judgment.reasoning)
            continue

        # Update conviction
        idea["original_conviction"] = conviction
        idea["conviction"] = judgment.adjusted_conviction
        idea["judgment"] = asdict(judgment)
        idea["judgment_prompt"] = prompt
        reviewed.append(idea)

        logger.info(
            "%s %s: %.3f → %.3f (%s) — %s",
            judgment.action.upper(),
            ticker,
            conviction,
            judgment.adjusted_conviction,
            f"{judgment.adjustment:+.3f}",
            judgment.reasoning,
        )

    return reviewed


def _log_judgment(judgment: LLMJudgment, prompt: str) -> None:
    """Persist judgment to disk for review."""
    try:
        JUDGMENT_LOG_PATH.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = JUDGMENT_LOG_PATH / f"judgment_{judgment.ticker}_{ts}.json"
        log_file.write_text(json.dumps({
            "judgment": asdict(judgment),
            "prompt": prompt,
        }, indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to log judgment: %s", e)
