---
name: us-stock-trading
description: |
  AI-powered US stock trading agent with quantitative signals, sentiment analysis, and risk management.
  Combines technical indicators (RSI, MACD, Bollinger, SMA crossover), sentiment (Reddit, news),
  event-driven strategies (earnings, 13F/congressional following, momentum factor, mean reversion),
  and a multi-agent debate framework for trade decisions. Supports Alpaca paper/live trading.
  
  Use when: scanning stocks for signals, analyzing a ticker, executing trades, checking portfolio,
  reviewing earnings, watching whale/institutional moves, running backtests, generating reports,
  or any US equities trading task.
---

# US Stock Trading

AI-powered trading system with quantitative signals, sentiment analysis, and automated execution via Alpaca.

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
| `scan` | `scan AAPL NVDA TSLA` | Run signal scan, show conviction scores |
| `analyze` | `analyze AAPL` | Deep-dive: technicals + all signals |
| `trade` | `trade buy AAPL 10` | Execute trade with risk checks |
| `portfolio` | `portfolio` | Current positions and P&L |
| `earnings` | `earnings AAPL MSFT` | Upcoming earnings + surprise data |
| `signals` | `signals AAPL --period 1y` | All active signals (technical + sentiment + strategy) |
| `whale-watch` | `whale-watch` | Latest 13F/congressional/institutional moves |
| `backtest` | `backtest AAPL NVDA --strategy technical --start 2025-01-01` | Historical strategy backtest |
| `risk` | `risk` | Risk dashboard |
| `report` | `report` | Generate daily report |
| `config` | `config` | View current configuration |

## Architecture

```
scripts/
├── core/           # Engine: data pipeline, signals, conviction, risk, execution
├── strategies/     # Earnings, momentum, mean reversion, sentiment, investor following
├── analysis/       # Sentiment scraper, news, earnings analyzer, regime detector, debate
├── monitoring/     # Portfolio tracker, alerts, signal efficacy, reports
├── backtest/       # Backtesting engine + weight optimizer
└── utils/          # Universe (S&P 500), market calendar
```

## Signal Pipeline

1. **Data** → yfinance OHLCV with parquet caching
2. **Signals** → RSI, MACD, Bollinger, SMA crossover, volume anomaly
3. **Strategies** → Earnings event, momentum factor, mean reversion, sentiment momentum, investor following
4. **Regime** → Bull/bear/sideways detection adjusts signal weights
5. **Conviction** → Weighted synthesis → score [-1, 1] per ticker
6. **Risk** → Position sizing, limits, stop-loss, drawdown checks
7. **Execution** → Alpaca paper or live orders

## Orchestrator

For programmatic use, the `TradingOrchestrator` ties everything together:

```python
from scripts.core.orchestrator import TradingOrchestrator
orch = TradingOrchestrator()
result = orch.run_scan()           # Full market scan
analysis = orch.run_analysis("AAPL")  # Deep analysis
ideas = orch.generate_trade_ideas()   # Filtered trade recommendations
```

## Risk Controls

| Control | Default | Action |
|---------|---------|--------|
| Max position | 5% of portfolio | Size down or reject |
| Max open positions | 15 | Reject new buys |
| Min cash reserve | 20% | Reject buys |
| Stop-loss | 8% trailing | Auto-exit |
| Daily drawdown | 3% | Halt trading |
| Total drawdown | 15% | Halt + alert |

## Scheduling (OpenClaw Cron)

Recommended crons for autonomous operation:
- **Pre-market scan**: `0 6 * * 1-5` (6 AM PT) → `scan`
- **Post-close report**: `0 13 * * 1-5` (1 PM PT / 4 PM ET) → `report`
- **Weekly review**: `0 9 * * 6` (Saturday 9 AM) → `report --weekly`

## Configuration

Edit `config.yaml` for: broker settings, universe (S&P 500 or custom), strategy enable/disable, risk limits, notification preferences. See `references/config-schema.md` for full schema.

## Backtesting

```bash
python cli.py backtest AAPL NVDA TSLA --strategy technical --start 2025-06-01 --end 2025-12-31 --capital 100000
```

Strategies: `technical`, `momentum`, `mean_reversion`, `combined`.
