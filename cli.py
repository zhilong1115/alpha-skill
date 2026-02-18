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
@click.option("--period", default="1y", help="Data period (e.g. 1y, 6mo, 5d)")
def scan(tickers: tuple[str, ...], period: str) -> None:
    """Scan tickers for trading signals."""
    from scripts.core.data_pipeline import get_price_data
    from scripts.core.signal_engine import compute_signals
    from scripts.core.conviction import compute_conviction

    if not tickers:
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
        click.echo("\n" + "=" * 50)
        click.echo("CONVICTION SCORES")
        click.echo("=" * 50)
        for _, row in conviction.iterrows():
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


if __name__ == "__main__":
    cli()
