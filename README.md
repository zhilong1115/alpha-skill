# Alpha Skill 📈

AI-powered **dual-mode** US stock trading system: **Intraday day-trading** (automated on Alpaca) + **Swing recommendations** (for manual trading on Robinhood). Real-time market scanning via Alpaca Screener API, news-driven catalysts, and strict intraday risk management. Built as an [OpenClaw](https://github.com/openclaw/openclaw) skill.

## Architecture: Two Independent Modes

### Mode A: Intraday Day Trading (Alpaca, Agent-Driven)
- **Agent-driven**: LLM agent owns the decision loop — no hardcoded rules for sizing or entry
- **Full market scanning** via Alpaca Screener API — top gainers, losers, most active stocks
- **5-minute candle signals**: VWAP, Opening Range Breakout, momentum slope, RSI, volume profile
- **Catalyst-driven**: gaps >3%, unusual volume, breaking news
- **News interrupt system**: Critical news triggers immediate trading cycle (not just next 15-min cron)
- **Intraday journal**: Cross-cycle memory via JSONL — agent remembers earlier trades, news, observations
- **Aggressive sizing**: Agent sizes based on conviction (20-30% per position on high conviction)
- **Safety net**: $2K daily loss circuit breaker
- **12:45 PM PT hard close** — all positions liquidated, no overnight holds

### Mode C: Crypto Trading (Alpaca Paper, Conservative)
- **Symbols**: BTC/USD, ETH/USD, SOL/USD on Alpaca paper trading
- **Strategy**: Conservative E+G Hybrid — TSI, OBV+EMA9, WaveTrend, USDT.D TSI scored against SMA200 regime
- **Position sizing**: Bull regime 4/4=50%, 3/4=30%, 2/4=15% | Bear regime 4/4=30%, 3/4=15%, 2/4=0%
- **Capital**: $50K allocation (of $100K paper account), max $25K per coin
- **Stops**: Agent-driven — `stop_context()` provides raw data (ATR, PnL%, reference levels); agent decides placement. No hardcoded tiers.
- **Commands**: `crypto-scan`, `crypto-trade`, `crypto-status`, `crypto-close`
- 24/7 trading (crypto markets never close)

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
- **Intraday Signals** — VWAP deviation, ORB breakout, 5-min momentum, RSI(14), relative volume (for day trading). Data via **Alpaca API** (primary); yfinance as fallback (yfinance 5-min volume data was unreliable).

### LLM Judgment Layer
- Rule-based heuristics: macro shocks, catalysts, falling knives, volume confirmation
- Regime-adaptive: less aggressive in bull markets, more cautious in bear
- Actions: PROCEED / BOOST / REDUCE / VETO

### Risk Management

#### Intraday Risk (V2.1)
| Control | Value |
|---------|-------|
| Sizing | Agent-driven, ~25% default suggestion, no hard cap |
| Stop-loss | ATR-based (1.5× ATR), agent can override |
| Take-profit | ATR-based (3.0× ATR, 1:2 R/R), agent decides |
| Daily loss cap | $2,000 circuit breaker (safety net) |
| Hard close | 12:45 PM PT — no overnight holds |
| News interrupt | Critical news triggers immediate cycle |
| Journal | Cross-cycle memory via `data/journal/` |

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
    ├── journal/                    # Intraday journal (YYYY-MM-DD.jsonl) — only intraday state
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
