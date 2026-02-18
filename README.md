# Alpha Skill 📈

AI-powered US stock trading agent that combines quantitative signals, sentiment analysis, and multi-agent reasoning for automated trade decisions. Built as an [OpenClaw](https://github.com/openclaw/openclaw) skill.

## Features

- **Technical Signal Engine** — RSI, MACD, Bollinger Bands, SMA crossover, volume anomaly detection
- **Sentiment Analysis** — Reddit (WSB, r/stocks) scraping + yfinance news sentiment scoring
- **Event-Driven Strategies** — Earnings surprise detection, pre/post-earnings analysis
- **Institutional Following** — SEC 13F filing parser, ARK daily trades, congressional trade tracking
- **Momentum Factor** — 12-1 month momentum ranking with monthly rebalancing
- **Mean Reversion** — Bollinger + RSI-based reversion candidates
- **Multi-Agent Debate** — Bull vs. bear case synthesis with confidence-weighted verdict
- **Regime Detection** — Bull/bear/sideways classification with adaptive signal weights
- **Risk Management** — Position sizing, trailing stops, drawdown limits, sector exposure caps
- **Backtesting** — Historical strategy backtesting with Sharpe ratio optimization
- **Signal Efficacy Tracking** — Monitors which signals are actually working over time
- **Automated Execution** — Paper and live trading via Alpaca API

## Architecture

```
us-stock-trading/
├── SKILL.md                      # OpenClaw skill manifest
├── cli.py                        # Click CLI (11 commands)
├── config.yaml                   # Configuration
├── requirements.txt              # Python dependencies
│
├── scripts/
│   ├── core/
│   │   ├── data_pipeline.py      # yfinance data fetcher with parquet caching
│   │   ├── signal_engine.py      # Technical indicator computation
│   │   ├── conviction.py         # Weighted signal synthesis → conviction scores
│   │   ├── risk_manager.py       # Position sizing, limits, stop-loss
│   │   ├── executor.py           # Alpaca broker integration
│   │   └── orchestrator.py       # End-to-end trading pipeline
│   │
│   ├── strategies/
│   │   ├── earnings_event.py     # Earnings-driven signals
│   │   ├── sentiment_momentum.py # Reddit/news sentiment → contrarian/momentum trades
│   │   ├── investor_following.py # 13F/ARK/congressional trade following
│   │   ├── momentum_factor.py    # 12-1 month momentum factor
│   │   └── mean_reversion.py     # Bollinger band + RSI reversion
│   │
│   ├── analysis/
│   │   ├── sentiment_scraper.py  # Reddit public API scraper
│   │   ├── news_analyzer.py      # yfinance news sentiment scoring
│   │   ├── earnings_analyzer.py  # Earnings transcript keyword analysis
│   │   ├── filing_parser.py      # SEC EDGAR 13F XML parser
│   │   ├── regime_detector.py    # Market regime classification (HMM-inspired)
│   │   └── debate.py             # Multi-agent bull/bear debate framework
│   │
│   ├── monitoring/
│   │   ├── portfolio_tracker.py  # Real-time P&L and exposure tracking
│   │   ├── report_generator.py   # Daily/weekly report generation
│   │   ├── alert_system.py       # Drawdown, stop-loss, signal alerts
│   │   └── signal_efficacy.py    # Signal performance tracking
│   │
│   ├── backtest/
│   │   ├── engine.py             # Day-by-day backtesting engine
│   │   └── optimizer.py          # Random search weight optimization
│   │
│   └── utils/
│       ├── universe.py           # S&P 500 ticker fetcher + custom universe
│       └── calendar.py           # Market hours + earnings calendar
│
├── tests/
│   ├── test_signals.py           # Signal engine + conviction tests
│   ├── test_risk.py              # Risk manager tests
│   └── test_backtest.py          # Backtesting engine tests
│
└── data/
    ├── cache/                    # Parquet price data cache
    ├── signals/                  # Signal log for efficacy tracking
    └── trades/                   # Trade log
```

## Quick Start

### Prerequisites

- Python 3.13+
- [Alpaca](https://alpaca.markets/) account (free, paper trading supported)

### Installation

```bash
git clone https://github.com/zhilongzheng/alpha-skill.git
cd alpha-skill

# Create virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

```bash
# Copy example env file
cp .env.example .env

# Add your Alpaca API keys
# ALPACA_API_KEY=your_key
# ALPACA_SECRET_KEY=your_secret
```

Edit `config.yaml` to customize:
- Broker mode (`paper` / `live`)
- Stock universe (`sp500` / `custom`)
- Enabled strategies
- Risk limits
- Notification preferences

## CLI Commands

```bash
source .venv/bin/activate

# Scan stocks for trading signals
python cli.py scan AAPL NVDA TSLA MSFT

# Deep-dive analysis on a single ticker
python cli.py analyze AAPL

# Show all active signals (technical + sentiment + strategy)
python cli.py signals AAPL NVDA --period 1y

# Upcoming earnings with surprise data
python cli.py earnings AAPL MSFT GOOGL

# Latest institutional/congressional moves
python cli.py whale-watch

# Execute a trade (with risk checks)
python cli.py trade buy AAPL 10

# Portfolio overview
python cli.py portfolio

# Risk dashboard
python cli.py risk

# Generate daily report
python cli.py report

# Run backtest
python cli.py backtest AAPL NVDA TSLA --strategy technical --start 2025-06-01 --end 2025-12-31

# View configuration
python cli.py config
```

## Signal Pipeline

```
┌─────────────┐    ┌──────────────┐    ┌────────────┐    ┌──────────┐    ┌───────────┐
│  Data Layer  │───▶│   Signals    │───▶│ Conviction │───▶│   Risk   │───▶│ Execution │
│  (yfinance)  │    │  (5 types)   │    │  (weighted) │    │ (checks) │    │ (Alpaca)  │
└─────────────┘    └──────────────┘    └────────────┘    └──────────┘    └───────────┘
                          │                   ▲
                   ┌──────┴──────┐     ┌──────┴──────┐
                   │  Strategies │     │   Regime    │
                   │ (5 modules) │     │  Detector   │
                   └─────────────┘     └─────────────┘
```

1. **Data** — Fetch OHLCV via yfinance, cache as parquet (configurable TTL)
2. **Technical Signals** — RSI(14), MACD(12,26,9), Bollinger(20,2), SMA(50/200), Volume Anomaly
3. **Strategy Signals** — Earnings, sentiment, momentum, mean reversion, institutional following
4. **Regime Detection** — Classify bull/bear/sideways → adapt signal weights
5. **Conviction** — Weighted synthesis of all signals → score per ticker `[-1, 1]`
6. **Risk Check** — Position size, cash reserve, max positions, sector limits, stop-loss
7. **Execution** — Submit orders to Alpaca (paper or live)

## Strategies

### Earnings Event
Pre-earnings setup analysis (price action, gap risk) and post-earnings surprise scoring. Compares actual vs estimated EPS via yfinance.

### Sentiment Momentum
Scrapes Reddit (WSB, r/stocks) for ticker mentions and sentiment. Combines with yfinance news sentiment. Uses contrarian logic: extreme bullish sentiment → slight bearish signal, and vice versa.

### Investor Following
Parses SEC EDGAR 13F filings to track institutional investors (Berkshire, Bridgewater, etc.). Detects new positions, increases, and exits. Also tracks ARK daily trade CSVs.

### Momentum Factor
Classic 12-1 month momentum factor. Ranks universe by trailing returns (skipping most recent month), selects top N. Rebalances monthly.

### Mean Reversion
Screens for stocks >2σ below 20-day moving average with RSI < 30. Targets 20-day MA as exit, 8% trailing stop.

## Risk Controls

| Control | Default | Action on Breach |
|---------|---------|-----------------|
| Max position size | 5% of portfolio | Size down or reject |
| Max open positions | 15 | Reject new buys |
| Minimum cash reserve | 20% | Reject buys |
| Trailing stop-loss | 8% | Auto-exit |
| Daily drawdown limit | 3% | Halt trading |
| Total drawdown limit | 15% | Halt + alert |

## Backtesting

```bash
# Basic backtest
python cli.py backtest AAPL NVDA --strategy technical

# Custom date range and capital
python cli.py backtest AAPL NVDA TSLA MSFT GOOGL \
  --strategy combined \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --capital 50000
```

Available strategies: `technical`, `momentum`, `mean_reversion`, `combined`

**Sample result** (AAPL + NVDA + TSLA, Jun–Dec 2025, technical):
```
Total Return:  +2.01%
Sharpe Ratio:  1.198
Max Drawdown:  -1.33%
Win Rate:      54.5%
Trades:        24
```

## Orchestrator (Programmatic Usage)

```python
from scripts.core.orchestrator import TradingOrchestrator

orch = TradingOrchestrator()

# Full market scan with regime-adaptive weights
result = orch.run_scan(tickers=["AAPL", "NVDA", "TSLA"])

# Deep analysis on a single ticker
analysis = orch.run_analysis("AAPL")

# Generate filtered trade recommendations
ideas = orch.generate_trade_ideas(min_conviction=0.3)
```

## Multi-Agent Debate

The debate module creates structured bull and bear cases from available data:

```python
from scripts.analysis.debate import create_bull_case, create_bear_case, resolve_debate

bull = create_bull_case("AAPL", signals_df, news, sentiment)
bear = create_bear_case("AAPL", signals_df, news, sentiment)
verdict = resolve_debate(bull, bear)
# → {"verdict": "buy", "confidence": 0.72, "reasoning": "..."}
```

## OpenClaw Integration

This project is packaged as an OpenClaw skill. To use with OpenClaw:

1. Place the project directory in your OpenClaw skills path
2. The skill triggers on: "trade stocks", "stock trading", "market analysis", "portfolio", "earnings analysis", "check positions"

Recommended cron schedule for autonomous operation:
```yaml
- cron: "0 6 * * 1-5"    # Pre-market scan (6 AM PT)
- cron: "0 13 * * 1-5"   # Post-close report (1 PM PT / 4 PM ET)
- cron: "0 9 * * 6"      # Weekly review (Saturday 9 AM)
```

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```

24 tests covering signal computation, risk management, and backtesting.

## Tech Stack

- **Data**: [yfinance](https://github.com/ranaroussi/yfinance) — free market data
- **Indicators**: [pandas-ta](https://github.com/twopirllc/pandas-ta) — 130+ technical indicators
- **Broker**: [Alpaca](https://alpaca.markets/) via [alpaca-py](https://github.com/alpacahq/alpaca-py) — commission-free trading
- **Analysis**: pandas, numpy, numba
- **CLI**: Click
- **Filing Data**: SEC EDGAR API, BeautifulSoup4

## Stats

- **4,138 lines** of Python
- **24 tests**, all passing
- **30+ Python modules** across 6 packages
- **11 CLI commands**

## Disclaimer

This software is for educational and research purposes. Trading stocks involves risk of financial loss. Paper trade extensively before using real money. Past backtesting performance does not guarantee future results. The authors are not responsible for any financial losses incurred from using this software.

## License

MIT
