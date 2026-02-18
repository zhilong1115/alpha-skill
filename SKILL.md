---
name: us-stock-trading
description: |
  AI-powered US stock trading agent with dual-layer signals (daily + intraday), LLM judgment,
  real-time news (Alpaca WebSocket + RSS + Finnhub), and automated execution via Alpaca.
  
  Dual signal layers: daily (RSI, MACD, Bollinger, SMA, volume) for direction + intraday
  (VWAP, ORB, momentum, RSI on 5min, volume profile) for timing. Combined 60/40 weighting.
  
  LLM judgment layer reads news headlines, price action, volume to adjust conviction:
  BOOST, REDUCE, or VETO trades. Regime-adaptive (less aggressive in bull, more cautious in bear).
  
  Real-time news daemon with bidirectional signals: buy opportunities (FDA approvals, earnings
  beats, rate cuts) and sell warnings (macro shocks, fraud, downgrades). Alpaca WebSocket for
  sub-second latency + RSS feeds every 60s.
  
  A/B test framework compares quant-only vs judgment-enhanced strategies in parallel.
  Scans 600+ tickers: S&P 500 + Reddit trending + unusual volume. Trades every 30 minutes.
  
  Use when: scanning stocks, analyzing tickers, executing trades, checking portfolio,
  monitoring news, reviewing earnings, running backtests, comparing strategies, or any
  US equities trading task.
---

# US Stock Trading

AI-powered trading system with dual-layer signals, LLM judgment, real-time news, and automated execution.

## Setup

```bash
cd <skill-dir>
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, (optional) FINNHUB_API_KEY
```

## CLI Commands

All commands from project directory with venv activated:

```bash
source .venv/bin/activate
python cli.py <command>
```

### Trading
| Command | Usage | Description |
|---------|-------|-------------|
| `auto-trade` | `auto-trade --universe full --execute` | Full cycle: scan → intraday → judge → risk → execute |
| `auto-trade` | `auto-trade --universe full` | Dry run (shows what would trade) |
| `scan` | `scan --universe full` | Signal scan across 600+ tickers |
| `trade` | `trade buy AAPL 10` | Manual trade with risk checks |

### Judgment & Intraday
| Command | Usage | Description |
|---------|-------|-------------|
| `judge` | `judge AAPL NVDA TSLA` | LLM subjective review of candidates |
| `judge` | `judge` | Review top candidates from full scan |
| `intraday` | `intraday AAPL GOOGL` | 5-min signals: VWAP, ORB, momentum, RSI, volume |

### News Daemon
| Command | Usage | Description |
|---------|-------|-------------|
| `news-daemon` | `news-daemon start` | Start real-time daemon (Alpaca WS + RSS + Finnhub) |
| `news-daemon` | `news-daemon alerts` | View pending alerts: 🟢 buy / 🔴 sell / ⚪ monitor |
| `news-daemon` | `news-daemon status` | Check if daemon is running |
| `news-daemon` | `news-daemon stop` | Stop daemon |
| `news` | `news AAPL NVDA` | Manual news check (yfinance) |

### A/B Testing
| Command | Usage | Description |
|---------|-------|-------------|
| `ab-status` | `ab-status` | Compare baseline vs judgment strategy |
| `ab-reset` | `ab-reset` | Reset A/B tracking data |

### Monitoring
| Command | Usage | Description |
|---------|-------|-------------|
| `pulse` | `pulse` | Market dashboard: SPY, VIX, sectors, regime |
| `monitor` | `monitor` | Position health: stops, P&L, alerts |
| `portfolio` | `portfolio` | Current positions + P&L |

### Analysis & Backtesting
| Command | Usage | Description |
|---------|-------|-------------|
| `analyze` | `analyze AAPL` | Deep-dive: technicals + all signals |
| `backtest` | `backtest AAPL NVDA --start 2025-06-01` | Historical backtest |
| `backtest-compare` | `backtest-compare AAPL NVDA ... --start 2022-01-01` | Baseline vs judgment comparison |
| `signals` | `signals AAPL` | All active signals |
| `earnings` | `earnings AAPL MSFT` | Earnings data |
| `whale-watch` | `whale-watch` | 13F/institutional moves |

### Other
| Command | Usage | Description |
|---------|-------|-------------|
| `risk` | `risk` | Risk dashboard |
| `report` | `report` | Daily report |
| `config` | `config` | View configuration |

## Architecture

```
scripts/
├── core/
│   ├── signal_engine.py      # Daily: RSI, MACD, Bollinger, SMA, volume
│   ├── intraday_signals.py   # 5-min: VWAP, ORB, momentum, RSI, volume profile
│   ├── conviction.py         # Weighted synthesis → conviction scores
│   ├── trader.py             # AutoTrader: scan → intraday → judge → risk → execute
│   ├── risk_manager.py       # Cumulative position tracking, limits, stops
│   ├── ab_tracker.py         # A/B test: parallel strategy comparison
│   ├── orchestrator.py       # End-to-end pipeline
│   ├── executor.py           # Alpaca integration
│   └── data_pipeline.py      # yfinance data + parquet cache
├── analysis/
│   ├── llm_judge.py          # LLM judgment: gather context → adjust conviction
│   ├── regime_detector.py    # Bull/bear/sideways/volatile detection
│   ├── sentiment_scraper.py  # Reddit scraper + trending tickers
│   ├── news_analyzer.py      # yfinance news sentiment
│   ├── debate.py             # Multi-agent bull/bear debate
│   └── ...                   # earnings, filings
├── monitoring/
│   ├── realtime_news.py      # Daemon: Alpaca WS + RSS + Finnhub
│   ├── news_monitor.py       # Breaking news + sentiment shifts
│   ├── market_pulse.py       # Market dashboard
│   └── ...                   # alerts, reports, tracking
├── strategies/               # Earnings, momentum, mean reversion, sentiment, following
├── backtest/
│   ├── engine.py             # Day-by-day backtesting
│   └── judgment_backtest.py  # Comparison backtest: baseline vs judgment
└── utils/                    # Universe, calendar
```

## Trading Pipeline

```
Daily Signals (direction)     Intraday Signals (timing)      Real-Time News
RSI, MACD, BB, SMA, Vol      VWAP, ORB, Momentum, RSI       Alpaca WS + RSS
        │                            │                            │
        └────────┬───────────────────┘                            │
                 ▼                                                │
        Combined Conviction                                       │
        60% daily + 40% intraday                                  │
                 │                                                │
                 ▼                                                ▼
        LLM Judgment Layer ◄──── News (buy/sell signals)
        PROCEED / BOOST / REDUCE / VETO
                 │
                 ▼
        Risk Manager (cumulative)
        15 max positions, 20% cash reserve
                 │
                 ▼
        Execute on Alpaca ──── A/B Test Tracking
```

## Autonomous Schedule (Mon–Fri)

| Time (PT) | Action |
|-----------|--------|
| 5:50 AM | Start news daemon |
| 6:00 AM | Market pulse + full scan |
| 6:00–12:30 PM | **Auto-trade every 30 min** |
| Every 5 min | News monitor (buy/sell signals) |
| 8/10/12 AM | Position monitor |
| 1:15 PM | Daily report + A/B comparison |
| 1:30 PM | Stop news daemon |

## Risk Controls

| Control | Default |
|---------|---------|
| Max position | 5% of portfolio |
| Max positions | 15 |
| Cash reserve | 20% |
| Stop-loss | 8% trailing |
| Daily drawdown | 3% |
| Total drawdown | 15% |
