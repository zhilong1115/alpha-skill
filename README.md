# Alpha Skill 📈

AI-powered **dual-mode** US stock trading system: **Intraday day-trading** (automated on Alpaca) + **Swing recommendations** (for manual trading on Robinhood). Real-time market scanning via Alpaca Screener API, news-driven catalysts, and strict intraday risk management. Built as an [OpenClaw](https://github.com/openclaw/openclaw) skill.

## Architecture: Two Independent Modes

### Mode A: Intraday Day Trading (Alpaca, Automated)
- **Full market scanning** via Alpaca Screener API — top gainers, losers, most active stocks across entire market
- **5-minute candle signals**: VWAP, Opening Range Breakout, momentum slope, RSI, volume profile
- **Catalyst-driven**: gaps >2%, unusual volume, breaking news
- **Strict risk**: max 5 positions, 2% stop-loss, 4% take-profit, 1% daily loss cap
- **12:45 PM PT hard close** — all positions liquidated, no overnight holds
- Target: 3-8 selective trades/day

### Mode B: Swing Recommendations (Robinhood, Manual)
- Daily morning scan of S&P 500 + Reddit trending + volume spikes (600+ tickers)
- Top 3-5 picks with conviction score, target price, stop-loss, risk/reward ratio
- Portfolio tracking: monitors your positions, alerts on stop-loss/target hits
- Daily signals: RSI, MACD, Bollinger Bands, SMA crossovers, volume anomaly

## Features

### Intraday Scanner (3 Sources)
- **Alpaca Screener API** — Real-time market-wide: top gainers, losers, most active
- **Gap Scanner** — Pre-market gaps >2% from liquid universe (~150 stocks)
- **News Daemon** — Alpaca WebSocket (sub-second) + RSS feeds (CNBC, Reuters, MarketWatch, Yahoo Finance)

### Signal Engines
- **Daily Signals** — RSI, MACD, Bollinger Bands, SMA crossover, volume anomaly (for swing)
- **Intraday Signals** — VWAP deviation, ORB breakout, 5-min momentum, RSI(14), relative volume (for day trading)

### LLM Judgment Layer
- Rule-based heuristics: macro shocks, catalysts, falling knives, volume confirmation
- Regime-adaptive: less aggressive in bull markets, more cautious in bear
- Actions: PROCEED / BOOST / REDUCE / VETO

### Risk Management

#### Intraday Risk
| Control | Value |
|---------|-------|
| Max positions | 5 |
| Position size | 10% of portfolio |
| Stop-loss | 2% per trade |
| Take-profit | 4% per trade (2:1 R/R) |
| Daily loss cap | 1% of portfolio |
| Hard close | 12:45 PM PT |

#### Swing Risk (Recommendation-level)
| Control | Value |
|---------|-------|
| Min R/R ratio | 1.5:1 |
| Min conviction | 0.4 |
| Stop-loss | Below 20-day low |
| Target | Near resistance / +10% |

## Quick Start

```bash
git clone https://github.com/zhilong1115/alpha-skill.git
cd alpha-skill
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add: ALPACA_API_KEY, ALPACA_SECRET_KEY
# Optional: FINNHUB_API_KEY
```

## CLI Commands

```bash
source .venv/bin/activate

# === INTRADAY DAY TRADING ===
python cli.py day-scan                          # Scan: Alpaca screener + gaps + news
python cli.py day-trade --execute               # Full cycle: scan → signal → trade
python cli.py day-trade                         # Dry run
python cli.py day-status                        # Today's trades, P&L, open positions
python cli.py day-close                         # Force close all positions

# === SWING RECOMMENDATIONS ===
python cli.py swing-recommend --universe sp500  # Generate daily top 5 picks
python cli.py swing-add AAPL 10 185.50 --stop 178 --target 205  # Track a position
python cli.py swing-remove AAPL --price 200     # Record a sale
python cli.py swing-status                      # Portfolio status + alerts

# === NEWS ===
python cli.py news-daemon start                 # Start real-time daemon
python cli.py news-daemon alerts                # View pending alerts
python cli.py news-daemon status                # Check daemon
python cli.py news-daemon stop                  # Stop daemon

# === SCANNING & ANALYSIS ===
python cli.py scan --universe full              # Full signal scan (600+ tickers)
python cli.py pulse                             # Market dashboard: SPY, VIX, regime
python cli.py analyze AAPL                      # Deep-dive analysis
python cli.py judge AAPL NVDA                   # LLM judgment review
python cli.py signals AAPL                      # All active signals

# === PORTFOLIO & MONITORING ===
python cli.py portfolio                         # Alpaca positions + P&L
python cli.py monitor                           # Position health check
python cli.py report                            # Daily report

# === BACKTESTING ===
python cli.py backtest AAPL --start 2025-06-01
python cli.py backtest-compare AAPL NVDA TSLA --start 2022-01-01

# === LEGACY ===
python cli.py auto-trade --universe smart --execute  # V1 swing trading (deprecated)
python cli.py ab-status                         # A/B test comparison
```

## Architecture

```
us-stock-trading/
├── cli.py                          # Click CLI (28 commands)
├── config.yaml
├── DESIGN_V2.md                    # V2 architecture design doc
│
├── scripts/
│   ├── intraday/                   # 🆕 V2: Day trading system
│   │   ├── scanner.py             # Alpaca Screener API + gap scan + news catalysts
│   │   ├── signals.py             # 5-min: VWAP, ORB, momentum, RSI, volume
│   │   ├── risk.py                # 5 pos max, 1% daily cap, 2% stop, hard close
│   │   └── trader.py             # Full lifecycle: scan → signal → execute → manage
│   │
│   ├── swing/                      # 🆕 V2: Swing recommendations
│   │   ├── recommender.py         # Daily picks: conviction + target + stop + R/R
│   │   └── tracker.py            # Track Robinhood positions, alert on stops/targets
│   │
│   ├── core/
│   │   ├── signal_engine.py       # Daily technical signals
│   │   ├── conviction.py          # Weighted conviction scoring
│   │   ├── risk_manager.py        # Position sizing & limits
│   │   ├── executor.py            # Alpaca broker integration
│   │   ├── orchestrator.py        # Pipeline orchestration
│   │   ├── trader.py              # AutoTrader (V1, legacy)
│   │   ├── data_pipeline.py       # yfinance + parquet cache
│   │   ├── intraday_signals.py    # V1 intraday (legacy)
│   │   └── ab_tracker.py          # A/B test framework
│   │
│   ├── analysis/
│   │   ├── llm_judge.py           # LLM judgment layer
│   │   ├── regime_detector.py     # Bull/bear/sideways detection
│   │   ├── sentiment_scraper.py   # Reddit scraping
│   │   ├── news_analyzer.py       # News sentiment
│   │   └── debate.py              # Bull vs bear debate
│   │
│   ├── monitoring/
│   │   ├── realtime_news.py       # Daemon: Alpaca WS + RSS + Finnhub
│   │   ├── market_pulse.py        # Market dashboard
│   │   └── portfolio_tracker.py   # P&L tracking
│   │
│   ├── strategies/                 # Earnings, momentum, mean reversion, sentiment
│   ├── backtest/                   # Backtesting engines
│   └── utils/
│       └── universe.py            # S&P 500 + Reddit + volume + smart universe
│
├── tests/                          # 24 tests
└── data/
    ├── intraday_state.json         # Today's day trades
    ├── intraday_history/           # Daily archives
    ├── swing_portfolio.json        # Robinhood position tracking
    ├── alerts/pending.json         # News daemon alerts
    ├── premarket_picks.json        # Morning scan top picks
    └── cache/                      # Price data cache
```

## Trading Pipeline

```
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│    INTRADAY (Alpaca, Auto)       │  │    SWING (Robinhood, Manual)     │
├──────────────────────────────────┤  ├──────────────────────────────────┤
│                                  │  │                                  │
│  Alpaca Screener API             │  │  Full Universe (600+)            │
│  Gainers / Losers / Most Active  │  │  S&P 500 + Reddit + Volume      │
│       │                          │  │       │                          │
│       ▼                          │  │       ▼                          │
│  + Gap Scanner + News Catalysts  │  │  Daily Signals (RSI, MACD, BB)  │
│       │                          │  │       │                          │
│       ▼                          │  │       ▼                          │
│  5-min Signals                   │  │  Conviction Scoring              │
│  VWAP, ORB, Momentum, RSI       │  │  + LLM Judgment                  │
│       │                          │  │       │                          │
│       ▼                          │  │       ▼                          │
│  Intraday Risk                   │  │  Top 5 Recommendations           │
│  5 max, 2% stop, 1% daily cap   │  │  Target / Stop / R:R ratio       │
│       │                          │  │       │                          │
│       ▼                          │  │       ▼                          │
│  Execute on Alpaca               │  │  → Telegram to Zhilong           │
│  Close by 12:45 PM              │  │  → Track positions + alerts       │
│                                  │  │                                  │
└──────────────────────────────────┘  └──────────────────────────────────┘
```

## Autonomous Schedule (Mon–Fri PT)

| Time | Action | Mode |
|------|--------|------|
| 5:50 AM | Start news daemon | Shared |
| 6:00 AM | Market pulse + Swing recommendations + Day scan | Both |
| 7:00 AM – 12:30 PM | **Intraday trading every 15 min** | Intraday |
| 12:45 PM | **Hard close all day positions** | Intraday |
| 1:15 PM | Daily report (intraday P&L + swing status) | Both |
| 1:15 PM | Stop news daemon | Shared |
| 9:00 PM | Swing portfolio check | Swing |
| 9:00 PM / 3:00 AM | Polymarket monitoring | Other |
| 10:00 PM | Polymarket market scan | Other |

## Stats

- **~8,000+ lines** of Python
- **24 tests**, all passing
- **28 CLI commands**
- **Alpaca Screener API** — real-time full-market scanning
- **3 news sources** (Alpaca WebSocket, RSS ×5, Finnhub)

## Disclaimer

Educational and research purposes only. Trading stocks involves risk of financial loss. Paper trade extensively before using real money. Past performance does not guarantee future results.

## License

MIT
