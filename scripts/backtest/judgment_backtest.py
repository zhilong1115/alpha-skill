"""Extended backtest engine with LLM judgment layer comparison.

Runs side-by-side: baseline (quant-only) vs judgment-enhanced strategy,
over multi-year historical data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf

from scripts.core.signal_engine import compute_signals
from scripts.core.conviction import compute_conviction, DEFAULT_WEIGHTS
from scripts.core.risk_manager import approve_trade

logger = logging.getLogger(__name__)

# ── judgment heuristics (mirrors llm_judge.py logic) ───────────────

CRITICAL_KW = [
    "fed", "rate hike", "rate cut", "tariff", "war", "sanction",
    "bankrupt", "fraud", "sec investigation", "default", "recession",
    "shutdown", "crash", "emergency",
]
POS_KW = [
    "beat", "upgrade", "record revenue", "fda approv", "contract win",
    "dividend hike", "buyback", "acquisition", "partnership",
]
NEG_KW = [
    "miss", "downgrade", "guidance cut", "recall", "lawsuit",
    "layoff", "restructur", "debt", "dilut",
]


def _judgment_adjust(
    conviction: float,
    side: str,
    headlines_lower: str,
    vol_ratio: float,
    change_5d: float,
    regime: str,
    change_1mo: float = 0.0,
) -> tuple[float, str]:
    """Apply rule-based judgment adjustment. Returns (adjusted_conviction, reason)."""
    adj = 0.0
    reasons: list[str] = []

    # Regime-adaptive penalty scaling
    regime_scale = {"BULL": 0.5, "SIDEWAYS": 1.0, "BEAR": 1.3, "VOLATILE": 1.2}.get(regime, 1.0)

    macro = [k for k in CRITICAL_KW if k in headlines_lower]
    if macro:
        if side == "buy":
            adj -= 0.12 * regime_scale
            reasons.append(f"macro:{','.join(macro[:2])}")
        else:
            adj += 0.10

    pos = [k for k in POS_KW if k in headlines_lower]
    if pos and side == "buy":
        boost = 0.10 if regime in ("BULL", "SIDEWAYS") else 0.05
        if len(pos) >= 2:
            boost += 0.05
        adj += boost
        reasons.append(f"pos:{','.join(pos[:2])}")

    neg = [k for k in NEG_KW if k in headlines_lower]
    if neg and side == "buy":
        adj -= 0.08 * regime_scale
        reasons.append(f"neg:{','.join(neg[:2])}")

    # Volume confirmation (can boost now)
    if vol_ratio > 2.0:
        if conviction > 0.35 and change_5d > 0:
            adj += 0.06
            reasons.append(f"vol_confirm:{vol_ratio:.1f}x")
        elif conviction <= 0.35 and vol_ratio > 3.0:
            adj -= 0.05
            reasons.append(f"vol_warn:{vol_ratio:.1f}x")

    # Price action: momentum confirmation
    if side == "buy":
        if 2 < change_5d < 8 and change_1mo > 0:
            adj += 0.04
            reasons.append(f"trend:+{change_5d:.1f}%")
        elif change_5d < -8:
            adj -= 0.08
            reasons.append(f"knife:{change_5d:.1f}%")
        elif change_5d > 12:
            adj -= 0.04
            reasons.append(f"chase:{change_5d:.1f}%")

    # Regime (softened)
    if regime == "VOLATILE" and side == "buy":
        adj -= 0.04
    elif regime == "BEAR" and side == "buy":
        adj -= 0.06
    elif regime == "BULL" and side == "buy" and not macro and not neg:
        adj += 0.03
        reasons.append("bull_tailwind")

    adjusted = max(0.0, min(1.0, conviction + adj))
    return adjusted, "; ".join(reasons) if reasons else "clean"


def _detect_regime_from_data(spy_df: pd.DataFrame, idx: int) -> str:
    """Simple regime detection from SPY data at a given index."""
    if idx < 50:
        return "SIDEWAYS"
    window = spy_df.iloc[max(0, idx - 50) : idx + 1]
    if len(window) < 20:
        return "SIDEWAYS"

    # Use returns volatility as VIX proxy
    rets = window["Close"].pct_change().dropna()
    vol = float(rets.std() * np.sqrt(252) * 100)  # annualized vol %
    sma20 = float(window["Close"].iloc[-20:].mean())
    price = float(window["Close"].iloc[-1])

    if vol > 30:
        return "VOLATILE"
    elif price < sma20 * 0.97:
        return "BEAR"
    elif price > sma20 * 1.03:
        return "BULL"
    return "SIDEWAYS"


# ── simulated news from price action ───────────────────────────────

def _simulate_news_keywords(
    ticker_df: pd.DataFrame, idx: int, spy_df: pd.DataFrame, spy_idx: int
) -> str:
    """Generate synthetic 'news keywords' from price action for backtesting.

    We can't get historical news, so we use price-based proxies:
    - Big SPY drops → "fed crash" keywords
    - Earnings-like gaps → "beat" or "miss"
    - Volume spikes → noted separately
    """
    keywords: list[str] = []

    # SPY-based macro signals
    if spy_idx >= 1:
        spy_ret = (float(spy_df["Close"].iloc[spy_idx]) / float(spy_df["Close"].iloc[spy_idx - 1]) - 1)
        if spy_ret < -0.02:
            keywords.append("crash recession")
        elif spy_ret < -0.01:
            keywords.append("fed rate")
        elif spy_ret > 0.02:
            keywords.append("record rally")

    # Ticker-specific gap detection (earnings proxy)
    if idx >= 1:
        gap = (float(ticker_df["Open"].iloc[idx]) / float(ticker_df["Close"].iloc[idx - 1]) - 1)
        if gap > 0.05:
            keywords.append("beat record revenue upgrade")
        elif gap < -0.05:
            keywords.append("miss downgrade guidance cut")

    return " ".join(keywords)


@dataclass
class ComparisonResult:
    """Side-by-side results: baseline vs judgment-enhanced."""

    baseline_return: float = 0.0
    judgment_return: float = 0.0
    baseline_sharpe: float = 0.0
    judgment_sharpe: float = 0.0
    baseline_max_dd: float = 0.0
    judgment_max_dd: float = 0.0
    baseline_trades: int = 0
    judgment_trades: int = 0
    baseline_win_rate: float = 0.0
    judgment_win_rate: float = 0.0
    vetoed_trades: int = 0
    boosted_trades: int = 0
    reduced_trades: int = 0
    period: str = ""
    tickers: list[str] = field(default_factory=list)
    baseline_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    judgment_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    def summary(self) -> str:
        lines = [
            "=" * 65,
            "  BACKTEST COMPARISON: Baseline vs LLM Judgment",
            f"  Period: {self.period} | Tickers: {', '.join(self.tickers)}",
            "=" * 65,
            f"  {'Metric':<25} {'Baseline':>15} {'+ Judgment':>15}",
            "-" * 65,
            f"  {'Total Return':<25} {self.baseline_return:>+14.2f}% {self.judgment_return:>+14.2f}%",
            f"  {'Sharpe Ratio':<25} {self.baseline_sharpe:>15.3f} {self.judgment_sharpe:>15.3f}",
            f"  {'Max Drawdown':<25} {self.baseline_max_dd:>14.2f}% {self.judgment_max_dd:>14.2f}%",
            f"  {'Win Rate':<25} {self.baseline_win_rate:>14.1f}% {self.judgment_win_rate:>14.1f}%",
            f"  {'Num Trades':<25} {self.baseline_trades:>15} {self.judgment_trades:>15}",
            "-" * 65,
            f"  Judgment Actions: {self.vetoed_trades} vetoed, {self.boosted_trades} boosted, {self.reduced_trades} reduced",
            "=" * 65,
        ]
        delta = self.judgment_return - self.baseline_return
        if delta > 0:
            lines.append(f"  ✅ Judgment layer added +{delta:.2f}% return")
        elif delta < 0:
            lines.append(f"  ⚠️ Judgment layer reduced return by {delta:.2f}%")
        else:
            lines.append(f"  ➡️ No difference in return")

        dd_diff = self.judgment_max_dd - self.baseline_max_dd
        if dd_diff > 0:  # less negative = better
            lines.append(f"  ✅ Judgment layer reduced max drawdown by {dd_diff:.2f}%")

        return "\n".join(lines)


def run_comparison_backtest(
    tickers: list[str],
    start: str = "2023-01-01",
    end: str = "2025-12-31",
    initial_capital: float = 100_000,
    conviction_threshold: float = 0.3,
) -> ComparisonResult:
    """Run side-by-side backtest: baseline vs judgment-enhanced.

    Downloads multi-year data and simulates both strategies day-by-day.
    """
    tickers = [t.upper() for t in tickers]

    # Download data
    logger.info("Downloading data for %s from %s to %s...", tickers, start, end)
    price_data: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        try:
            df = yf.download(tk, start=start, end=end, progress=False)
            if df is not None and not df.empty:
                # Flatten MultiIndex columns if present
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if hasattr(df.index, "tz") and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                price_data[tk] = df
        except Exception as e:
            logger.warning("Failed to download %s: %s", tk, e)

    # SPY for regime detection
    try:
        spy_df = yf.download("SPY", start=start, end=end, progress=False)
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df.columns = spy_df.columns.get_level_values(0)
        if hasattr(spy_df.index, "tz") and spy_df.index.tz is not None:
            spy_df.index = spy_df.index.tz_localize(None)
    except Exception:
        spy_df = pd.DataFrame()

    if not price_data:
        logger.error("No data downloaded")
        return ComparisonResult()

    # Build trading calendar
    all_dates: set[pd.Timestamp] = set()
    for df in price_data.values():
        all_dates.update(pd.to_datetime(df.index))
    trading_days = sorted(d for d in all_dates)
    if len(trading_days) < 60:
        logger.error("Not enough trading days")
        return ComparisonResult()

    logger.info("Running backtest over %d trading days...", len(trading_days))

    # ── simulate both strategies ──
    def _run_strategy(use_judgment: bool) -> tuple[pd.Series, list[dict], dict]:
        cash = initial_capital
        positions: dict[str, dict] = {}
        equity_vals: list[float] = []
        equity_dates: list[pd.Timestamp] = []
        trades: list[dict] = []
        judgment_stats = {"vetoed": 0, "boosted": 0, "reduced": 0}

        for i, day in enumerate(trading_days):
            # Mark to market
            pv = cash
            for tk, pos in positions.items():
                df = price_data.get(tk)
                if df is None:
                    continue
                mask = df.index <= day
                if not mask.any():
                    continue
                cp = float(df.loc[mask, "Close"].iloc[-1])
                pos["highest"] = max(pos["highest"], cp)
                pv += pos["qty"] * cp

            equity_vals.append(pv)
            equity_dates.append(day)

            if i >= len(trading_days) - 1 or i < 50:
                continue

            next_day = trading_days[i + 1]

            # Regime
            spy_mask = spy_df.index <= day if not spy_df.empty else pd.Series(dtype=bool)
            spy_idx = int(spy_mask.sum()) - 1 if spy_mask.any() else -1
            regime = _detect_regime_from_data(spy_df, spy_idx) if spy_idx >= 50 else "SIDEWAYS"

            # Signals
            all_sigs: list[pd.DataFrame] = []
            for tk in tickers:
                df = price_data.get(tk)
                if df is None:
                    continue
                mask = df.index <= day
                end_idx = int(mask.sum()) - 1
                if end_idx < 30:
                    continue
                window = df.iloc[:end_idx + 1]
                try:
                    sigs = compute_signals(tk, window)
                    if not sigs.empty:
                        all_sigs.append(sigs)
                except Exception:
                    continue

            if not all_sigs:
                continue

            combined = pd.concat(all_sigs, ignore_index=True)
            conv_df = compute_conviction(combined, DEFAULT_WEIGHTS)

            # Exits
            for tk in list(positions.keys()):
                pos = positions[tk]
                df = price_data.get(tk)
                if df is None:
                    continue
                nm = df.index == next_day
                if nm.any():
                    ep = float(df.loc[nm, "Open"].iloc[0])
                else:
                    m2 = df.index <= day
                    ep = float(df.loc[m2, "Close"].iloc[-1]) if m2.any() else 0

                stop = pos["highest"] * 0.92
                tk_c = conv_df[conv_df["ticker"] == tk]
                cs = float(tk_c["conviction_score"].iloc[0]) if not tk_c.empty else 0.0

                if ep <= stop or cs < -0.1:
                    pnl = (ep - pos["entry_price"]) * pos["qty"]
                    cash += pos["qty"] * ep
                    trades.append({"ticker": tk, "side": "sell", "price": ep,
                                   "date": str(next_day.date()), "pnl": round(pnl, 2)})
                    del positions[tk]

            # Entries
            for _, row in conv_df.iterrows():
                tk = row["ticker"]
                score = float(row["conviction_score"])
                if score <= conviction_threshold or tk in positions:
                    continue

                # Judgment layer
                if use_judgment:
                    df = price_data.get(tk)
                    if df is None:
                        continue
                    mask = df.index <= day
                    end_idx = int(mask.sum()) - 1

                    # 5-day change
                    if end_idx >= 5:
                        c5 = float(df["Close"].iloc[end_idx])
                        c5ago = float(df["Close"].iloc[end_idx - 5])
                        change_5d = (c5 / c5ago - 1) * 100 if c5ago > 0 else 0
                    else:
                        change_5d = 0

                    # 1-month change
                    if end_idx >= 21:
                        c1mo = float(df["Close"].iloc[end_idx - 21])
                        change_1mo = (float(df["Close"].iloc[end_idx]) / c1mo - 1) * 100 if c1mo > 0 else 0
                    else:
                        change_1mo = 0

                    # Volume ratio
                    if end_idx >= 20:
                        today_vol = float(df["Volume"].iloc[end_idx])
                        avg_vol = float(df["Volume"].iloc[end_idx - 20:end_idx].mean())
                        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1.0
                    else:
                        vol_ratio = 1.0

                    # Simulated news
                    news_kw = _simulate_news_keywords(df, end_idx, spy_df, spy_idx)

                    adj_score, reason = _judgment_adjust(
                        score, "buy", news_kw, vol_ratio, change_5d, regime, change_1mo
                    )

                    if adj_score <= 0.05:
                        judgment_stats["vetoed"] += 1
                        continue
                    elif adj_score > score + 0.03:
                        judgment_stats["boosted"] += 1
                    elif adj_score < score - 0.03:
                        judgment_stats["reduced"] += 1

                    score = adj_score

                if score <= conviction_threshold:
                    continue

                df = price_data.get(tk)
                if df is None:
                    continue
                nm = df.index == next_day
                if nm.any():
                    ep = float(df.loc[nm, "Open"].iloc[0])
                else:
                    m2 = df.index <= day
                    ep = float(df.loc[m2, "Close"].iloc[-1]) if m2.any() else 0

                if ep <= 0:
                    continue

                target_val = pv * 0.05
                qty = int(target_val / ep)
                if qty <= 0:
                    continue

                cost = qty * ep
                if cost > cash:
                    qty = int(cash / ep)
                    if qty <= 0:
                        continue
                    cost = qty * ep

                cash -= cost
                positions[tk] = {"qty": qty, "entry_price": ep, "highest": ep}
                trades.append({"ticker": tk, "side": "buy", "price": ep,
                               "date": str(next_day.date()), "pnl": 0.0,
                               "reason": f"conv={score:.3f}"})

        eq = pd.Series(equity_vals, index=equity_dates) if equity_vals else pd.Series(dtype=float)
        return eq, trades, judgment_stats

    # Run both
    logger.info("Running baseline strategy...")
    base_eq, base_trades, _ = _run_strategy(use_judgment=False)
    logger.info("Running judgment-enhanced strategy...")
    judg_eq, judg_trades, j_stats = _run_strategy(use_judgment=True)

    def _metrics(eq: pd.Series, trades: list[dict]) -> tuple[float, float, float, float]:
        if eq.empty:
            return 0, 0, 0, 0
        ret = (float(eq.iloc[-1]) / initial_capital - 1) * 100
        dr = eq.pct_change().dropna()
        sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if len(dr) > 1 and dr.std() > 0 else 0
        peak = eq.expanding().max()
        dd = float(((eq - peak) / peak * 100).min())
        closed = [t for t in trades if t["side"] == "sell"]
        wr = (sum(1 for t in closed if t["pnl"] > 0) / len(closed) * 100) if closed else 0
        return ret, sharpe, dd, wr

    br, bs, bd, bw = _metrics(base_eq, base_trades)
    jr, js, jd, jw = _metrics(judg_eq, judg_trades)

    return ComparisonResult(
        baseline_return=round(br, 2),
        judgment_return=round(jr, 2),
        baseline_sharpe=round(bs, 3),
        judgment_sharpe=round(js, 3),
        baseline_max_dd=round(bd, 2),
        judgment_max_dd=round(jd, 2),
        baseline_trades=len(base_trades),
        judgment_trades=len(judg_trades),
        baseline_win_rate=round(bw, 1),
        judgment_win_rate=round(jw, 1),
        vetoed_trades=j_stats["vetoed"],
        boosted_trades=j_stats["boosted"],
        reduced_trades=j_stats["reduced"],
        period=f"{start} → {end}",
        tickers=tickers,
        baseline_equity=base_eq,
        judgment_equity=judg_eq,
    )
