---
name: us-stock-trading
description: |
  AI-powered dual-mode US stock trading system:
  
  **Intraday Day Trading** (automated on Alpaca): Alpaca Screener API for real-time full-market
  scanning (gainers, losers, most active), 5-min candle signals (VWAP, ORB, momentum, RSI,
  volume), strict risk management (5 positions max, 2% stop, 1% daily cap, 12:45 PM hard close).
  Target 3-8 selective trades/day.
  
  **Swing Recommendations** (manual on Robinhood): Daily scan of 600+ tickers (S&P 500 + Reddit
  trending + volume spikes), top 3-5 picks with conviction, target price, stop-loss, R/R ratio.
  Tracks user's positions and alerts on stop-loss/target hits.
  
  Shared: Real-time news daemon (Alpaca WebSocket + RSS), LLM judgment layer, regime detection.
  
  Use when: day trading, scanning for intraday setups, getting swing recommendations, tracking
  positions, monitoring news, analyzing stocks, running backtests, or any US equities trading task.
---

# US Stock Trading — Dual Mode

AI-powered trading: Intraday (Alpaca, automated) + Swing recommendations (Robinhood, manual).

## Setup

```bash
cd /Users/zhilongzheng/Projects/us-stock-trading
source .venv/bin/activate
```

## CLI Commands

### Intraday Day Trading
| Command | Usage | Description |
|---------|-------|-------------|
| `day-scan` | `python cli.py day-scan` | Scan: Alpaca screener + gaps + news catalysts |
| `day-trade` | `python cli.py day-trade --execute` | Full cycle: scan → signal → execute → manage |
| `day-trade` | `python cli.py day-trade` | Dry run |
| `day-status` | `python cli.py day-status` | Today's trades, P&L, open positions |
| `day-close` | `python cli.py day-close` | Force close all intraday positions |

### Swing Recommendations
| Command | Usage | Description |
|---------|-------|-------------|
| `swing-recommend` | `python cli.py swing-recommend --universe sp500` | Generate top 5 daily picks |
| `swing-add` | `python cli.py swing-add AAPL 10 185.50 --stop 178 --target 205` | Track a Robinhood position |
| `swing-remove` | `python cli.py swing-remove AAPL --price 200` | Record a sale |
| `swing-status` | `python cli.py swing-status` | Portfolio status + alerts |

### News Daemon
| Command | Usage | Description |
|---------|-------|-------------|
| `news-daemon start` | `python cli.py news-daemon start` | Start real-time monitoring |
| `news-daemon alerts` | `python cli.py news-daemon alerts` | View pending alerts |
| `news-daemon status` | `python cli.py news-daemon status` | Check daemon |
| `news-daemon stop` | `python cli.py news-daemon stop` | Stop daemon |

### Analysis & Monitoring
| Command | Usage | Description |
|---------|-------|-------------|
| `pulse` | `python cli.py pulse` | Market dashboard: SPY, VIX, sectors, regime |
| `scan` | `python cli.py scan --universe full` | Full signal scan (600+ tickers) |
| `analyze` | `python cli.py analyze AAPL` | Deep-dive analysis |
| `judge` | `python cli.py judge AAPL NVDA` | LLM judgment review |
| `portfolio` | `python cli.py portfolio` | Alpaca positions + P&L |
| `monitor` | `python cli.py monitor` | Position health check |

### Backtesting
| Command | Usage | Description |
|---------|-------|-------------|
| `backtest` | `python cli.py backtest AAPL --start 2025-06-01` | Historical backtest |
| `backtest-compare` | `python cli.py backtest-compare AAPL NVDA --start 2022-01-01` | Baseline vs judgment |

## Architecture

```
scripts/
├── intraday/           # Day trading: scanner, signals, risk, trader
├── swing/              # Swing: recommender, tracker
├── core/               # Signal engine, conviction, risk, executor, orchestrator
├── analysis/           # LLM judge, regime, sentiment, debate
├── monitoring/         # News daemon, market pulse, portfolio
├── strategies/         # Earnings, momentum, mean reversion, sentiment
├── backtest/           # Backtesting engines
└── utils/              # Universe management, calendar
```

## Intraday Pipeline

```
Alpaca Screener API (full market) ─→ Gap Scanner ─→ News Catalysts
         │                                │              │
         └────────────────┬───────────────┘              │
                          ▼                              │
              5-min Signals (VWAP, ORB, momentum) ◄──────┘
                          │
                          ▼
              Intraday Risk V2.1 (5 max, 2% stop, $500 daily cap, staged entry)
                          │
                          ▼
              Execute on Alpaca ──→ Hard close 12:45 PM
```

## Risk Controls

### Intraday
- Max 5 positions, 10% portfolio per trade
- 2% stop-loss, 4% take-profit (2:1 R/R)
- 1% daily loss cap → stop trading
- 12:45 PM PT hard close

### Swing (recommendations)
- Min conviction 0.4, min R/R 1.5:1
- Stop-loss below 20-day low
- Target near resistance or +10%

## Schedule (Mon–Fri PT)

| Time | Action |
|------|--------|
| 5:50 AM | News daemon start |
| 6:00 AM | Swing recommendations + Day scan |
| 7:00–12:30 PM | Intraday trading (every 15 min) |
| 12:45 PM | Hard close all |
| 1:15 PM | Daily report |
| 9:00 PM | Swing portfolio check |
