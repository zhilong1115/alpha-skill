"""CLI interface for the US stock trading system."""

from __future__ import annotations

import click
import pandas as pd
import yaml
from pathlib import Path


@click.group()
def cli() -> None:
    """US Stock Trading System — AI-powered trading agent."""
    pass


@cli.command()
@click.argument("tickers", nargs=-1)
@click.option("--universe", "universe_mode", type=click.Choice(["watchlist", "sp500", "full"]), default="watchlist",
              help="Universe: watchlist (7 stocks), sp500, full (sp500+reddit+volume)")
@click.option("--period", default="1y", help="Data period (e.g. 1y, 6mo, 5d)")
def scan(tickers: tuple[str, ...], universe_mode: str, period: str) -> None:
    """Scan tickers for trading signals.

    --universe watchlist: default 7 tech stocks (fast)
    --universe sp500: full S&P 500 (3-5 min)
    --universe full: S&P 500 + Reddit trending + volume spikes (5-8 min)
    """
    from scripts.core.data_pipeline import get_price_data
    from scripts.core.signal_engine import compute_signals
    from scripts.core.conviction import compute_conviction

    if tickers:
        pass  # use provided tickers
    elif universe_mode == "sp500":
        from scripts.utils.universe import get_sp500_tickers
        tickers = tuple(get_sp500_tickers())
        click.echo(f"📡 Scanning {len(tickers)} S&P 500 tickers...")
    elif universe_mode == "full":
        from scripts.utils.universe import get_full_universe
        u = get_full_universe()
        tickers = tuple(u["all_unique"])
        click.echo(
            f"📡 Scanning {len(tickers)} tickers "
            f"(S&P 500 + {len(u['reddit_trending'])} Reddit trending "
            f"+ {len(u['volume_spikes'])} volume spikes)..."
        )
    else:
        tickers = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
        click.echo(f"No tickers specified. Scanning defaults: {', '.join(tickers)}")

    all_signals = []
    for ticker in tickers:
        try:
            click.echo(f"Scanning {ticker}...")
            df = get_price_data(ticker, period)
            signals = compute_signals(ticker, df)
            all_signals.append(signals)
        except Exception as e:
            click.echo(f"  Error: {e}")

    # Also run sentiment signals
    try:
        from scripts.strategies.sentiment_momentum import generate_sentiment_signals
        click.echo("Running sentiment analysis...")
        sentiment_signals = generate_sentiment_signals(list(tickers))
        all_signals.append(sentiment_signals)
    except Exception as e:
        click.echo(f"  Sentiment error: {e}")

    if all_signals:
        combined = pd.concat(all_signals, ignore_index=True)
        conviction = compute_conviction(combined)
        conviction = conviction.sort_values("conviction_score", ascending=False)
        top_n = min(20, len(conviction))
        click.echo("\n" + "=" * 50)
        click.echo(f"TOP {top_n} CONVICTION SCORES (of {len(conviction)} scanned)")
        click.echo("=" * 50)
        for _, row in conviction.head(top_n).iterrows():
            score = row["conviction_score"]
            indicator = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "⚪"
            click.echo(f"  {indicator} {row['ticker']:<8} {score:+.3f}")


@cli.command()
def portfolio() -> None:
    """Show current portfolio and P&L."""
    from scripts.monitoring.portfolio_tracker import format_portfolio
    click.echo(format_portfolio())


@cli.command()
@click.argument("ticker")
@click.option("--period", default="1y", help="Data period")
def analyze(ticker: str, period: str) -> None:
    """Deep-dive analysis on a specific ticker."""
    from scripts.core.data_pipeline import get_price_data
    from scripts.core.signal_engine import compute_signals

    click.echo(f"Analyzing {ticker}...")
    df = get_price_data(ticker, period)
    signals = compute_signals(ticker, df)

    click.echo(f"\nPrice: ${df['Close'].iloc[-1]:.2f}")
    click.echo(f"52-week range: ${df['Close'].min():.2f} - ${df['Close'].max():.2f}")
    click.echo(f"\nSignals:")
    for _, row in signals.iterrows():
        score = row["score"]
        indicator = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "⚪"
        click.echo(f"  {indicator} {row['signal_name']:<20} value={row['value']:.4f}  score={score:+.3f}")


@cli.command()
def report() -> None:
    """Generate daily report."""
    from scripts.core.executor import get_positions
    from scripts.monitoring.report_generator import generate_daily_report

    positions = get_positions()
    report_text = generate_daily_report(positions, pd.DataFrame(), [])
    click.echo(report_text)


@cli.command()
@click.argument("ticker")
@click.argument("side", type=click.Choice(["buy", "sell"]))
@click.argument("qty", type=int)
@click.option("--order-type", default="market", help="Order type")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def trade(ticker: str, side: str, qty: int, order_type: str, yes: bool) -> None:
    """Execute a trade with risk checks."""
    from scripts.core.executor import get_account, get_positions, place_order
    from scripts.core.risk_manager import approve_trade
    from scripts.core.data_pipeline import get_price_data

    account = get_account()
    if not account:
        click.echo("❌ Cannot trade: Alpaca not connected.")
        return

    positions = get_positions()
    df = get_price_data(ticker, period="5d")
    price = float(df["Close"].iloc[-1])

    approved, sized_qty, reason = approve_trade(
        ticker, side, qty, price, account["portfolio_value"], positions
    )

    click.echo(f"Risk check: {reason}")
    if not approved:
        click.echo("❌ Trade rejected.")
        return

    click.echo(f"Order: {side.upper()} {sized_qty} {ticker} @ ~${price:.2f} ({order_type})")
    if not yes and not click.confirm("Confirm?"):
        click.echo("Cancelled.")
        return

    result = place_order(ticker, side, sized_qty, order_type)
    if result:
        click.echo(f"✅ Order placed: {result['id']} — {result['status']}")
    else:
        click.echo("❌ Order failed.")


@cli.command("config")
def show_config() -> None:
    """View current configuration."""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        click.echo(config_path.read_text())
    else:
        click.echo("No config.yaml found.")


@cli.command()
def risk() -> None:
    """Show risk dashboard."""
    from scripts.core.executor import get_account, get_positions
    from scripts.core.risk_manager import _load_risk_config

    account = get_account()
    positions = get_positions()
    cfg = _load_risk_config()

    click.echo("=" * 50)
    click.echo("RISK DASHBOARD")
    click.echo("=" * 50)

    if account:
        pv = account["portfolio_value"]
        cash = account["cash"]
        cash_pct = (cash / pv * 100) if pv > 0 else 0
        click.echo(f"  Portfolio Value:  ${pv:>12,.2f}")
        click.echo(f"  Cash:             ${cash:>12,.2f} ({cash_pct:.1f}%)")
        click.echo(f"  Open Positions:   {len(positions)} / {cfg['max_open_positions']}")
        click.echo(f"  Max per Position: {cfg['max_position_pct']}%")
        click.echo(f"  Min Cash Reserve: {cfg['min_cash_pct']}%")
        click.echo(f"  Trailing Stop:    {cfg['stop_loss_pct']}%")
    else:
        click.echo("  ⚠ Alpaca not connected.")


@cli.command()
@click.argument("tickers", nargs=-1)
def earnings(tickers: tuple[str, ...]) -> None:
    """Show upcoming earnings and surprise data for tickers."""
    from scripts.analysis.earnings_analyzer import analyze_earnings_surprise

    if not tickers:
        tickers = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")

    click.echo("=" * 50)
    click.echo("EARNINGS ANALYSIS")
    click.echo("=" * 50)
    for ticker in tickers:
        try:
            surprise = analyze_earnings_surprise(ticker)
            beat_icon = "✅" if surprise.get("beat") else "❌" if surprise.get("beat") is False else "❓"
            pct = surprise.get("surprise_pct")
            pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
            click.echo(f"  {beat_icon} {ticker:<8} Surprise: {pct_str}")
        except Exception as e:
            click.echo(f"  ❓ {ticker:<8} Error: {e}")


@cli.command()
@click.argument("tickers", nargs=-1)
@click.option("--period", default="1y", help="Data period")
def signals(tickers: tuple[str, ...], period: str) -> None:
    """Show all active signals (technical + sentiment + earnings + following)."""
    from scripts.core.data_pipeline import get_price_data
    from scripts.core.signal_engine import compute_signals
    from scripts.strategies.sentiment_momentum import generate_sentiment_signals
    from scripts.strategies.earnings_event import generate_earnings_signals
    from scripts.strategies.investor_following import generate_following_signals

    if not tickers:
        tickers = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")

    all_signals = []

    # Technical signals
    for ticker in tickers:
        try:
            df = get_price_data(ticker, period)
            sigs = compute_signals(ticker, df)
            all_signals.append(sigs)
        except Exception:
            pass

    # Sentiment signals
    try:
        all_signals.append(generate_sentiment_signals(list(tickers)))
    except Exception:
        pass

    # Earnings signals
    try:
        all_signals.append(generate_earnings_signals(list(tickers)))
    except Exception:
        pass

    # Following signals
    try:
        all_signals.append(generate_following_signals(list(tickers)))
    except Exception:
        pass

    if all_signals:
        combined = pd.concat(all_signals, ignore_index=True)
        click.echo("=" * 60)
        click.echo("ALL ACTIVE SIGNALS")
        click.echo("=" * 60)
        for ticker in tickers:
            ticker_sigs = combined[combined["ticker"] == ticker]
            if ticker_sigs.empty:
                continue
            click.echo(f"\n  {ticker}:")
            for _, row in ticker_sigs.iterrows():
                score = row["score"]
                icon = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "⚪"
                click.echo(f"    {icon} {row['signal_name']:<25} score={score:+.3f}")
    else:
        click.echo("No signals generated.")


@cli.command()
@click.argument("tickers", nargs=-1)
@click.option("--start", default="2025-06-01", help="Start date (YYYY-MM-DD)")
@click.option("--end", default="2025-12-31", help="End date (YYYY-MM-DD)")
@click.option("--strategy", default="technical", help="Strategy: technical, momentum, mean_reversion, combined")
@click.option("--capital", default=100000, type=float, help="Initial capital")
def backtest(tickers: tuple[str, ...], start: str, end: str, strategy: str, capital: float) -> None:
    """Run a backtest on historical data."""
    from scripts.backtest.engine import BacktestEngine

    if not tickers:
        tickers = ("AAPL", "NVDA")
        click.echo(f"No tickers specified. Using defaults: {', '.join(tickers)}")

    click.echo(f"Running backtest: {', '.join(tickers)} | {start} → {end} | {strategy} | ${capital:,.0f}")
    engine = BacktestEngine(list(tickers), start, end, initial_capital=capital, strategy=strategy)
    result = engine.run()
    click.echo(result.summary())

    if result.trades:
        click.echo(f"\nRecent trades (last 10):")
        for t in result.trades[-10:]:
            icon = "🟢" if t["side"] == "buy" else "🔴"
            click.echo(f"  {icon} {t['date']} {t['side'].upper()} {t['qty']} {t['ticker']} @ ${t['price']:.2f}")


@cli.command("backtest-compare")
@click.argument("tickers", nargs=-1)
@click.option("--start", default="2023-01-01", help="Start date (YYYY-MM-DD)")
@click.option("--end", default="2025-12-31", help="End date (YYYY-MM-DD)")
@click.option("--capital", default=100000, type=float, help="Initial capital")
def backtest_compare(tickers: tuple[str, ...], start: str, end: str, capital: float) -> None:
    """Compare baseline vs LLM judgment strategy on historical data."""
    from scripts.backtest.judgment_backtest import run_comparison_backtest

    if not tickers:
        tickers = ("AAPL", "NVDA", "TSLA", "GOOGL", "MSFT", "AMZN", "META")
        click.echo(f"Using default tickers: {', '.join(tickers)}")

    click.echo(f"⏳ Running comparison backtest: {start} → {end} | ${capital:,.0f}")
    click.echo("   This may take a few minutes...")

    result = run_comparison_backtest(list(tickers), start, end, capital)
    click.echo(result.summary())


@cli.command("whale-watch")
@click.argument("tickers", nargs=-1)
def whale_watch(tickers: tuple[str, ...]) -> None:
    """Show latest institutional/congressional moves (13F filings)."""
    from scripts.analysis.filing_parser import fetch_latest_13f

    if not tickers:
        tickers = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")

    click.echo("=" * 50)
    click.echo("WHALE WATCH — Institutional Moves")
    click.echo("=" * 50)
    for ticker in tickers:
        try:
            filings = fetch_latest_13f(ticker)
            if filings:
                click.echo(f"\n  {ticker}:")
                for f in filings[:3]:
                    click.echo(f"    {f}")
            else:
                click.echo(f"  {ticker}: No recent 13F data found.")
        except Exception as e:
            click.echo(f"  {ticker}: Error — {e}")


@cli.command("auto-trade")
@click.argument("tickers", nargs=-1)
@click.option("--universe", "universe_mode", type=click.Choice(["watchlist", "sp500", "full"]), default="full",
              help="Universe: watchlist, sp500, full (default)")
@click.option("--execute/--dry-run", default=False, help="Actually execute trades (default: dry run)")
def auto_trade(tickers: tuple[str, ...], universe_mode: str, execute: bool) -> None:
    """Run automated trading cycle: scan → decide → execute."""
    from scripts.core.trader import AutoTrader

    trader = AutoTrader()
    if tickers:
        ticker_list = list(tickers)
    elif universe_mode == "sp500":
        from scripts.utils.universe import get_sp500_tickers
        ticker_list = get_sp500_tickers()
        click.echo(f"📡 Using S&P 500 universe: {len(ticker_list)} tickers")
    elif universe_mode == "full":
        from scripts.utils.universe import get_full_universe
        u = get_full_universe()
        ticker_list = u["all_unique"]
        click.echo(
            f"📡 Using full universe: {len(ticker_list)} tickers "
            f"(S&P 500 + {len(u['reddit_trending'])} Reddit + {len(u['volume_spikes'])} volume)"
        )
    else:
        ticker_list = None

    if execute:
        click.echo("🚀 LIVE MODE — executing real trades!")
        result = trader.run_trading_cycle(ticker_list)
    else:
        click.echo("🧪 DRY RUN — no orders will be placed.")
        from scripts.core.orchestrator import TradingOrchestrator
        orch = TradingOrchestrator()
        ideas = orch.generate_trade_ideas()
        result = {"mode": "dry_run", "ideas": ideas}

    if result.get("mode") == "dry_run":
        ideas = result.get("ideas", [])
        if ideas:
            click.echo(f"\n📋 {len(ideas)} trade ideas:")
            for idea in ideas:
                click.echo(
                    f"  🟢 BUY {idea['qty']} {idea['ticker']} @ ${idea['price']:.2f} "
                    f"(conviction={idea['conviction']:.3f}) — {idea.get('reason', '')}"
                )
        else:
            click.echo("\n  No trade ideas above conviction threshold.")
    else:
        actions = result.get("actions_taken", [])
        click.echo(f"\n✅ Cycle complete: {len(actions)} actions taken.")
        for a in actions:
            click.echo(f"  {a.get('type', '')} {a.get('ticker', '')} qty={a.get('qty', 0)}")
        errors = result.get("errors", [])
        if errors:
            click.echo(f"\n⚠️ Errors: {len(errors)}")
            for e in errors:
                click.echo(f"  {e}")


@cli.command()
@click.argument("tickers", nargs=-1)
def monitor(tickers: tuple[str, ...]) -> None:
    """Check positions, stops, and alerts."""
    from scripts.core.trader import AutoTrader

    trader = AutoTrader()
    result = trader.monitor_positions()

    positions = result.get("positions", [])
    click.echo("=" * 50)
    click.echo("POSITION MONITOR")
    click.echo("=" * 50)

    if not positions:
        click.echo("  No open positions.")
    else:
        click.echo(f"  {len(positions)} positions | Total P&L: ${result.get('total_pnl', 0):,.2f}")
        for pos in positions:
            pnl = float(pos.get("unrealized_pl", 0))
            icon = "🟢" if pnl >= 0 else "🔴"
            click.echo(
                f"  {icon} {pos['ticker']:<8} {int(float(pos.get('qty', 0)))} shares "
                f"@ ${float(pos.get('current_price', 0)):.2f}  P&L: ${pnl:,.2f}"
            )

    stops = result.get("stop_status", [])
    if stops:
        click.echo(f"\n🛑 Stop-Loss Status:")
        for s in stops:
            click.echo(
                f"  {s['ticker']:<8} stop=${s['stop']:.2f}  "
                f"distance={s['distance_pct']:.1f}%"
            )

    alerts = result.get("alerts", [])
    if alerts:
        from scripts.monitoring.alert_system import format_alert
        click.echo(f"\n⚠️ Alerts ({len(alerts)}):")
        for a in alerts:
            click.echo(f"  {format_alert(a)}")


@cli.command()
@click.argument("tickers", nargs=-1)
def news(tickers: tuple[str, ...]) -> None:
    """Check breaking news and sentiment shifts."""
    from scripts.monitoring.news_monitor import NewsMonitor

    ticker_list = list(tickers) if tickers else None
    monitor = NewsMonitor(watchlist=ticker_list)

    click.echo("📰 Checking news...")
    news_events = monitor.check_breaking_news(ticker_list)

    if news_events:
        click.echo(f"\n🗞 Breaking News ({len(news_events)} items):")
        for ev in news_events[:15]:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(ev["urgency"], "⚪")
            click.echo(f"  {icon} [{ev['ticker']}] {ev['headline'][:80]}")
            click.echo(f"      urgency={ev['urgency']}  sentiment={ev['sentiment']:+.2f}  source={ev['source']}")
    else:
        click.echo("\n  No breaking news found.")

    click.echo("\n📊 Checking unusual volume...")
    volume = monitor.check_unusual_volume(ticker_list)
    if volume:
        for v in volume:
            click.echo(
                f"  ⚡ {v['ticker']}: {v['ratio']:.1f}x avg volume, "
                f"price {v['price_change_pct']:+.1f}%"
            )
    else:
        click.echo("  No unusual volume detected.")


@cli.command("ab-status")
def ab_status() -> None:
    """Show A/B test comparison: baseline vs judgment strategy."""
    from scripts.core.ab_tracker import load_state, get_ab_summary
    state = load_state()
    click.echo(get_ab_summary(state))


@cli.command("ab-reset")
@click.confirmation_option(prompt="Reset A/B test data?")
def ab_reset() -> None:
    """Reset A/B test tracking data."""
    from scripts.core.ab_tracker import ABState, save_state
    save_state(ABState(started_at=__import__("datetime").datetime.now().isoformat()))
    click.echo("✅ A/B test data reset.")


@cli.command()
@click.argument("tickers", nargs=-1)
@click.option("--regime", default=None, help="Override regime (BULL/BEAR/SIDEWAYS/VOLATILE)")
def judge(tickers: tuple[str, ...], regime: str | None) -> None:
    """LLM subjective review of trade candidates.

    Gathers news, price action, volume for each ticker and applies
    judgment adjustments to conviction scores.
    """
    from scripts.analysis.llm_judge import gather_context, apply_rule_based_judgment, build_judgment_prompt
    from scripts.analysis.regime_detector import detect_regime_detailed

    if not tickers:
        # Use top ideas from a scan
        from scripts.core.orchestrator import TradingOrchestrator
        orch = TradingOrchestrator()
        ideas = orch.generate_trade_ideas(0.3)
        if not ideas:
            click.echo("No trade candidates to review.")
            return
        tickers_to_review = [(i["ticker"], i["conviction"], i.get("side", "buy"), i.get("reason", "")) for i in ideas]
    else:
        tickers_to_review = [(t.upper(), 0.5, "buy", "manual review") for t in tickers]

    if regime is None:
        ri = detect_regime_detailed()
        regime = ri["regime"]

    click.echo(f"🧠 LLM Judgment Layer | Regime: {regime}")
    click.echo("=" * 60)

    for ticker, conv, side, reason in tickers_to_review:
        ctx = gather_context(ticker)
        j = apply_rule_based_judgment(ticker, conv, side, ctx, regime)
        prompt = build_judgment_prompt(ticker, conv, side, reason, ctx, regime)

        icon = {"proceed": "✅", "boost": "🟢", "reduce": "🟡", "veto": "🔴"}[j.action]
        click.echo(f"\n{icon} {ticker} — {j.action.upper()}")
        click.echo(f"  Conviction: {j.original_conviction:.3f} → {j.adjusted_conviction:.3f} ({j.adjustment:+.3f})")
        click.echo(f"  Reasoning: {j.reasoning}")
        if j.news_digest:
            click.echo(f"  News ({len(j.news_digest)}):")
            for h in j.news_digest[:3]:
                click.echo(f"    • {h}")


@cli.command()
def pulse() -> None:
    """Market pulse: SPY, VIX, sectors, regime."""
    from scripts.monitoring.market_pulse import MarketPulse

    mp = MarketPulse()
    click.echo(mp.format_pulse())


if __name__ == "__main__":
    cli()
