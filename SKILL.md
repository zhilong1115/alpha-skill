---
name: us-stock-trading
description: |
  AI-powered US stock trading agent with quantitative signals, sentiment analysis, and risk management.
  Combines technical indicators (RSI, MACD, Bollinger, SMA crossover), sentiment (Reddit, news),
  event-driven strategies (earnings, 13F/congressional following, momentum factor, mean reversion),
  and a multi-agent debate framework for trade decisions. Supports Alpaca paper/live trading.
  Includes real-time news monitoring, automated trade execution, and market pulse dashboard.
  Scans 600+ tickers: S&P 500 + Reddit trending + unusual volume detection.
  
  Use when: scanning stocks for signals, analyzing a ticker, executing trades, checking portfolio,
  reviewing earnings, watching whale/institutional moves, running backtests, generating reports,
  monitoring breaking news, checking market pulse, or any US equities trading task.
---

# US Stock Trading

AI-powered trading system with quantitative signals, sentiment analysis, real-time news monitoring, and automated execution via Alpaca.

## Setup

1. Create virtual environment and install dependencies:
```bash
cd <skill-dir>
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure Alpaca API keys in `.env`:
```bash
cp .env.example .env
# Edit .env with your Alpaca API key and secret
```

3. Default mode is paper trading. Edit `config.yaml` to switch to live.

## CLI Commands

All commands run from the project directory with venv activated:

```bash
source .venv/bin/activate
python cli.py <command>
```

| Command | Usage | Description |
|---------|-------|-------------|
| `scan` | `scan --universe full` | Signal scan across 600+ tickers |
| `auto-trade` | `auto-trade --universe full --execute` | Automated scan → decide → execute |
| `monitor` | `monitor` | Check positions, stops, P&L, alerts |
| `news` | `news AAPL NVDA` | Breaking news + Reddit sentiment shifts |
| `pulse` | `pulse` | Market pulse: SPY, VIX, sectors, regime |
| `analyze` | `analyze AAPL` | Deep-dive: technicals + all signals |
| `trade` | `trade buy AAPL 10` | Manual trade with risk checks |
| `portfolio` | `portfolio` | Current positions and P&L |
| `earnings` | `earnings AAPL MSFT` | Upcoming earnings + surprise data |
| `signals` | `signals AAPL --period 1y` | All active signals |
| `whale-watch` | `whale-watch` | Latest 13F/congressional/institutional moves |
| `backtest` | `backtest AAPL NVDA --strategy technical` | Historical strategy backtest |
| `risk` | `risk` | Risk dashboard |
| `report` | `report` | Generate daily report |
| `config` | `config` | View current configuration |

### Universe Modes

`scan` and `auto-trade` support `--universe` flag:

| Mode | Tickers | Speed | Use case |
|------|---------|-------|----------|
| `watchlist` | 7 tech stocks | ~10s | Quick check |
| `sp500` | 503 S&P 500 | ~3 min | Blue-chip scan |
| `full` | 600+ (S&P 500 + Reddit + volume) | ~5 min | Full market, catches meme/squeeze plays |

## Architecture

```
scripts/
├── core/
│   ├── data_pipeline.py      # yfinance data + parquet caching
│   ├── signal_engine.py      # Technical indicator computation
│   ├── conviction.py         # Weighted signal synthesis
│   ├── risk_manager.py       # Position sizing, limits, stops
│   ├── executor.py           # Alpaca broker integration
│   ├── orchestrator.py       # End-to-end trading pipeline
│   └── trader.py             # Automated trading engine (AutoTrader)
├── strategies/               # Earnings, momentum, mean reversion, sentiment, following
├── analysis/
│   ├── sentiment_scraper.py  # Reddit scraping + ticker discovery
│   ├── news_analyzer.py      # yfinance news sentiment
│   ├── earnings_analyzer.py  # Earnings keyword analysis
│   ├── filing_parser.py      # SEC 13F parser
│   ├── regime_detector.py    # Bull/bear/sideways detection
│   └── debate.py             # Multi-agent bull/bear debate
├── monitoring/
│   ├── portfolio_tracker.py  # P&L tracking
│   ├── report_generator.py   # Daily/weekly reports
│   ├── alert_system.py       # Drawdown, stop-loss, signal alerts
│   ├── signal_efficacy.py    # Signal performance tracking
│   ├── news_monitor.py       # Breaking news + sentiment shifts
│   └── market_pulse.py       # Market-wide health dashboard
├── backtest/                 # Backtesting engine + optimizer
└── utils/
    ├── universe.py           # S&P 500 + Reddit trending + volume screener
    └── calendar.py           # Market hours + earnings calendar
```

## Trading Pipeline

```
Universe Discovery          Signal Generation           Trade Execution
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ S&P 500 (503)    │      │ Technical (5)    │      │ Conviction Score │
│ Reddit WSB (100) │─────▶│ Strategies (5)   │─────▶│ Risk Check       │─────▶ Alpaca
│ Volume Spikes    │      │ Regime Adjust    │      │ Position Size    │
└──────────────────┘      └──────────────────┘      └──────────────────┘
                                  ▲
                          ┌───────┴────────┐
                          │ News Monitor   │
                          │ Reddit Shifts  │
                          │ Volume Spikes  │
                          └────────────────┘
```

## Autonomous Trading Schedule

When deployed with OpenClaw cron:

| Time (PT) | Action | Notification |
|-----------|--------|-------------|
| 6:00 AM | Market pulse + full scan (600+ tickers) | ✅ Telegram |
| 6:30 AM | Auto-trade execute | ✅ Telegram |
| Every 30 min | News monitoring (持仓 + Reddit trending) | ⚠️ Critical only |
| 8/10/12 AM | Position monitor (stops, P&L) | ⚠️ Alerts only |
| 12:45 PM | Pre-close trade | ✅ Telegram |
| 1:15 PM | Daily report | ✅ Telegram |

## Risk Controls

| Control | Default | Action |
|---------|---------|--------|
| Max position | 5% of portfolio | Size down or reject |
| Max open positions | 15 | Reject new buys |
| Min cash reserve | 20% | Reject buys |
| Stop-loss | 8% trailing | Auto-exit |
| Daily drawdown | 3% | Halt trading |
| Total drawdown | 15% | Halt + alert |

## Configuration

Edit `config.yaml` for: broker settings, universe, strategy enable/disable, risk limits. See `references/config-schema.md` for full schema.

## Backtesting

```bash
python cli.py backtest AAPL NVDA TSLA --strategy technical --start 2025-06-01 --end 2025-12-31 --capital 100000
```

Strategies: `technical`, `momentum`, `mean_reversion`, `combined`.
