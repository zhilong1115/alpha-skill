# Alpha Skill 📈

AI-powered US stock trading agent with **dual-layer signals** (daily + intraday), **LLM judgment**, **real-time news**, and **automated execution**. Scans 600+ tickers (S&P 500 + Reddit trending + volume spikes), trades every 30 minutes during market hours via Alpaca paper/live trading. Built as an [OpenClaw](https://github.com/openclaw/openclaw) skill.

## Features

### Signal Engine (Dual-Layer)
- **Daily Signals** — RSI, MACD, Bollinger Bands, SMA crossover, volume anomaly
- **Intraday Signals** — VWAP deviation, Opening Range Breakout, 5-min momentum, intraday RSI, volume profile
- **Combined Conviction** — 60% daily (direction) + 40% intraday (timing) → enter_now / wait / exit_now / hold

### LLM Judgment Layer
- Reads news headlines, price action, and volume for each trade candidate
- Adjusts conviction: BOOST / REDUCE / VETO with reasoning
- Regime-adaptive: less aggressive in bull markets, more cautious in bear
- Rule-based heuristics for macro shocks (Fed, tariffs, war), catalysts, falling knives

### Real-Time News Daemon
- **Alpaca WebSocket** — Sub-second latency, Benzinga news stream
- **RSS Feeds** — CNBC, Reuters, MarketWatch, Yahoo Finance (60s polling)
- **Finnhub REST** — General market news (120s polling, optional)
- **Bidirectional Signals** — Buy opportunities (FDA approvals, earnings beats, rate cuts) AND sell warnings (macro shocks, fraud, downgrades)
- **Dynamic Watchlist** — Auto-includes current holdings + A/B test positions

### A/B Test Framework
- Parallel tracking: Strategy A (quant-only on Alpaca) vs Strategy B (quant + judgment, virtual portfolio)
- Logs all divergences: vetoed, boosted, reduced trades
- Persistent state with real-time comparison dashboard

### Trading Infrastructure
- **Full Market Scanning** — 600+ tickers: S&P 500 + Reddit trending + unusual volume
- **30-Minute Trading Cycles** — Active trading throughout market hours
- **Sentiment Analysis** — Reddit scraping + yfinance news sentiment
- **Event-Driven Strategies** — Earnings, momentum, mean reversion, institutional following
- **Multi-Agent Debate** — Bull vs. bear case synthesis
- **Regime Detection** — Bull/bear/sideways/volatile with adaptive weights
- **Risk Management** — Position sizing, trailing stops, drawdown limits, cumulative tracking
- **Backtesting** — Historical comparison: baseline vs judgment-enhanced strategy

## Architecture

```
us-stock-trading/
├── cli.py                        # Click CLI (20 commands)
├── config.yaml                   # Configuration
├── requirements.txt              # Python dependencies
│
├── scripts/
│   ├── core/
│   │   ├── data_pipeline.py      # yfinance data fetcher with parquet caching
│   │   ├── signal_engine.py      # Daily technical indicator computation
│   │   ├── intraday_signals.py   # 5-min VWAP, ORB, momentum, RSI, volume profile
│   │   ├── conviction.py         # Weighted signal synthesis → conviction scores
│   │   ├── risk_manager.py       # Position sizing, limits, stop-loss
│   │   ├── executor.py           # Alpaca broker integration
│   │   ├── orchestrator.py       # End-to-end trading pipeline
│   │   ├── trader.py             # AutoTrader: scan → intraday → judge → risk → execute
│   │   └── ab_tracker.py         # A/B test: baseline vs judgment comparison
│   │
│   ├── strategies/
│   │   ├── earnings_event.py     # Earnings-driven signals
│   │   ├── sentiment_momentum.py # Reddit/news → contrarian/momentum
│   │   ├── investor_following.py # 13F/ARK/congressional following
│   │   ├── momentum_factor.py    # 12-1 month momentum factor
│   │   └── mean_reversion.py     # Bollinger + RSI reversion
│   │
│   ├── analysis/
│   │   ├── llm_judge.py          # LLM subjective judgment layer
│   │   ├── sentiment_scraper.py  # Reddit scraper + trending tickers
│   │   ├── news_analyzer.py      # yfinance news sentiment
│   │   ├── earnings_analyzer.py  # Earnings keyword analysis
│   │   ├── filing_parser.py      # SEC EDGAR 13F parser
│   │   ├── regime_detector.py    # Market regime classification
│   │   └── debate.py             # Multi-agent bull/bear debate
│   │
│   ├── monitoring/
│   │   ├── realtime_news.py      # News daemon: Alpaca WS + RSS + Finnhub
│   │   ├── news_monitor.py       # Breaking news + sentiment shifts
│   │   ├── market_pulse.py       # Market-wide health dashboard
│   │   ├── portfolio_tracker.py  # P&L tracking
│   │   ├── report_generator.py   # Daily/weekly reports
│   │   ├── alert_system.py       # Drawdown, stop-loss, signal alerts
│   │   └── signal_efficacy.py    # Signal performance tracking
│   │
│   ├── backtest/
│   │   ├── engine.py             # Day-by-day backtesting engine
│   │   ├── judgment_backtest.py  # Comparison: baseline vs judgment-enhanced
│   │   └── optimizer.py          # Random search weight optimization
│   │
│   └── utils/
│       ├── universe.py           # S&P 500 + Reddit trending + volume screener
│       └── calendar.py           # Market hours + earnings calendar
│
├── tests/                        # 24 tests, all passing
└── data/
    ├── cache/                    # Parquet price data cache
    ├── alerts/                   # Real-time news daemon alerts
    ├── judgments/                 # LLM judgment logs
    ├── trades/                   # Trade cycle logs
    └── ab_test.json              # A/B test state
```

## Quick Start

### Prerequisites

- Python 3.13+
- [Alpaca](https://alpaca.markets/) account (free, paper trading supported)

### Installation

```bash
git clone https://github.com/zhilong1115/alpha-skill.git
cd alpha-skill
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Add: ALPACA_API_KEY, ALPACA_SECRET_KEY
# Optional: FINNHUB_API_KEY (for extra news source)
```

## CLI Commands

```bash
source .venv/bin/activate

# === TRADING ===
python cli.py auto-trade --universe full --execute  # Full cycle: scan → judge → trade
python cli.py auto-trade --universe full             # Dry run
python cli.py scan --universe full                    # Signal scan only
python cli.py trade buy AAPL 10                       # Manual trade

# === JUDGMENT ===
python cli.py judge AAPL NVDA TSLA                    # LLM judgment review
python cli.py judge                                    # Review top candidates from scan

# === INTRADAY ===
python cli.py intraday AAPL GOOGL NVDA                # 5-min intraday signals

# === NEWS ===
python cli.py news-daemon start                        # Start real-time news daemon
python cli.py news-daemon status                       # Check daemon status
python cli.py news-daemon alerts                       # View pending alerts (buy/sell/monitor)
python cli.py news-daemon stop                         # Stop daemon
python cli.py news AAPL NVDA                           # Manual news check

# === A/B TEST ===
python cli.py ab-status                                # Compare baseline vs judgment strategy
python cli.py ab-reset                                 # Reset A/B tracking

# === MONITORING ===
python cli.py pulse                                    # Market dashboard
python cli.py monitor                                  # Position health check
python cli.py portfolio                                # Current positions + P&L

# === ANALYSIS ===
python cli.py analyze AAPL                             # Deep-dive analysis
python cli.py signals AAPL --period 1y                 # All active signals
python cli.py earnings AAPL MSFT                       # Earnings data
python cli.py whale-watch                              # Institutional moves

# === BACKTESTING ===
python cli.py backtest AAPL NVDA --start 2025-06-01 --end 2025-12-31
python cli.py backtest-compare AAPL NVDA TSLA GOOGL MSFT AMZN META \
  --start 2022-01-01 --end 2025-12-31                 # Baseline vs judgment comparison

# === OTHER ===
python cli.py risk                                     # Risk dashboard
python cli.py report                                   # Daily report
python cli.py config                                   # View configuration
```

## Trading Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    TRADING CYCLE (every 30 min)              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Universe (600+)                                            │
│  S&P 500 + Reddit + Volume                                  │
│       │                                                     │
│       ▼                                                     │
│  Daily Signals (direction)                                  │
│  RSI, MACD, Bollinger, SMA, Volume                         │
│       │                                                     │
│       ▼                                                     │
│  Intraday Signals (timing)           Real-Time News         │
│  VWAP, ORB, Momentum, RSI, Vol      Alpaca WS + RSS        │
│       │                                    │                │
│       ▼                                    ▼                │
│  Combined Conviction              News Classification       │
│  60% daily + 40% intraday        bullish → BUY signal       │
│       │                          bearish → SELL signal       │
│       ▼                                    │                │
│  LLM Judgment Layer ◄─────────────────────┘                │
│  Read news + price action + volume                          │
│  → PROCEED / BOOST / REDUCE / VETO                         │
│       │                                                     │
│       ▼                                                     │
│  Risk Manager (cumulative tracking)                         │
│  15 positions max, 20% cash reserve, 5% per position       │
│       │                                                     │
│       ▼                                                     │
│  Execute on Alpaca ──── Track in A/B Test                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Autonomous Schedule (Mon–Fri)

| Time (PT) | Action | Notification |
|-----------|--------|-------------|
| 5:50 AM | 🚀 Start news daemon (Alpaca WS + RSS) | Silent |
| 6:00 AM | 📊 Market pulse + full scan | ✅ Telegram |
| 6:00 AM – 12:30 PM | 💰 **Auto-trade every 30 min** | ✅ When trades execute |
| Every 5 min | 📰 News monitor (buy/sell signals) | ⚠️ Critical only |
| 8/10/12 AM | 🔍 Position monitor | ⚠️ Alerts only |
| 1:15 PM | 📋 Daily report + A/B comparison | ✅ Telegram |
| 1:30 PM | 🛑 Stop news daemon | Silent |

## Risk Controls

| Control | Default | Action on Breach |
|---------|---------|-----------------|
| Max position size | 5% of portfolio | Size down or reject |
| Max open positions | 15 | Reject new buys |
| Minimum cash reserve | 20% | Reject buys |
| Trailing stop-loss | 8% | Auto-exit |
| Daily drawdown limit | 3% | Halt trading |
| Total drawdown limit | 15% | Halt + alert |

## Backtesting: Baseline vs Judgment

```bash
python cli.py backtest-compare AAPL NVDA TSLA GOOGL MSFT AMZN META \
  --start 2022-01-01 --end 2022-12-31
```

| Period | Environment | Baseline | + Judgment | Drawdown Improvement |
|--------|------------|----------|------------|---------------------|
| 2022 | 🐻 Bear | -5.83% | **-5.03%** | ✅ +0.79% |
| 2023-25 | 🐂 Bull | +27.54% | +25.43% | — |
| 2020-25 | 🔄 Full cycle | +57.65% | +40.71% | ✅ +0.33% |

Judgment layer adds value in bear markets (risk reduction), slightly costs in bull markets (over-caution). A/B testing in live trading validates real-world performance.

## Stats

- **~7,000+ lines** of Python
- **24 tests**, all passing
- **45+ Python modules** across 7 packages
- **20 CLI commands**
- **600+ tickers** scanned per cycle
- **3 news sources** (Alpaca WebSocket, RSS ×5, Finnhub)

## Disclaimer

This software is for educational and research purposes. Trading stocks involves risk of financial loss. Paper trade extensively before using real money. Past backtesting performance does not guarantee future results.

## License

MIT
