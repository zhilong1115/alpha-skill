# Phase 3: Skill Design — us-stock-trading

*Date: 2026-02-17 | Author: Alpha*

---

## 1. SKILL.md Draft

```yaml
---
name: us-stock-trading
version: 0.1.0
description: |
  AI-powered US stock trading agent. Combines quantitative signals (technical, 
  sentiment, event-driven) with LLM reasoning for trade decisions. Supports 
  paper trading (Alpaca) and live trading (IBKR). Includes risk management, 
  portfolio monitoring, and automated reporting.
author: Alpha / Zhilong
tags: [trading, stocks, quantitative, ai-agent, options]
triggers:
  - "trade stocks"
  - "stock trading"
  - "market analysis"
  - "portfolio"
  - "earnings analysis"
  - "check positions"
commands:
  - scan          # Run signal scan across universe
  - analyze       # Deep-dive analysis on a specific ticker
  - trade         # Execute a trade (with confirmation)
  - portfolio     # Show current positions and P&L
  - earnings      # Analyze upcoming/recent earnings
  - signals       # Show active signals across all modules
  - backtest      # Run backtest on a strategy
  - report        # Generate daily/weekly report
  - whale-watch   # Check latest 13F/ARK/Congressional trades
  - risk          # Risk dashboard
  - config        # View/modify configuration
schedule:
  - cron: "0 6 * * 1-5"    # Pre-market scan (6 AM PT)
    action: scan
  - cron: "0 13 * * 1-5"   # Post-close report (1 PM PT = 4 PM ET)
    action: report
  - cron: "0 9 * * 6"      # Weekly review (Saturday morning)
    action: report --weekly
---
```

---

## 2. Key Scripts & Purposes

```
us-stock-trading/
├── SKILL.md                  # Skill manifest
├── config.yaml               # User configuration
├── scripts/
│   ├── core/
│   │   ├── data_pipeline.py      # Fetch & cache market data (yfinance, Alpaca, Polygon)
│   │   ├── signal_engine.py      # Compute all signals, output signal matrix
│   │   ├── conviction.py         # Weighted signal synthesis → trade conviction scores
│   │   ├── risk_manager.py       # Position sizing, limits, stop-loss logic
│   │   └── executor.py           # Broker abstraction (Alpaca/IBKR), order management
│   │
│   ├── strategies/
│   │   ├── earnings_event.py     # Earnings-driven: transcript analysis, IV crush, surprise
│   │   ├── sentiment_momentum.py # Reddit/FinTwit sentiment → momentum trades
│   │   ├── wheel_options.py      # Systematic CSP → CC wheel on quality stocks
│   │   ├── investor_following.py # 13F/ARK/Congressional trade following
│   │   ├── mean_reversion.py     # Bollinger/RSI mean reversion on liquid names
│   │   └── momentum_factor.py    # Monthly momentum factor rebalancing
│   │
│   ├── analysis/
│   │   ├── earnings_analyzer.py  # LLM earnings call transcript analysis
│   │   ├── sentiment_scraper.py  # Reddit/Twitter sentiment scoring
│   │   ├── filing_parser.py      # 13F, Form 4, congressional filings
│   │   ├── regime_detector.py    # HMM/LLM market regime classification
│   │   └── news_analyzer.py      # News sentiment with LLM context
│   │
│   ├── monitoring/
│   │   ├── portfolio_tracker.py  # Real-time P&L, exposure tracking
│   │   ├── alert_system.py       # Drawdown, signal, event alerts → Telegram
│   │   ├── signal_efficacy.py    # Track which signals are working
│   │   └── report_generator.py   # Daily/weekly/monthly reports
│   │
│   └── utils/
│       ├── broker_factory.py     # Alpaca/IBKR factory pattern
│       ├── calendar.py           # Earnings, Fed, economic event calendar
│       └── universe.py           # Stock universe management (S&P 500, custom)
│
├── data/
│   ├── cache/                    # Cached market data
│   ├── signals/                  # Signal history for efficacy tracking
│   └── trades/                   # Trade log
│
└── tests/
    ├── test_signals.py
    ├── test_risk.py
    └── test_backtest.py
```

### Script Purposes

| Script | Purpose |
|--------|---------|
| `data_pipeline.py` | Unified data fetcher. Abstracts yfinance/Alpaca/Polygon behind single interface. Handles caching, rate limiting, data quality checks. |
| `signal_engine.py` | Computes all technical, sentiment, event, flow signals for the stock universe. Outputs a signal matrix (ticker × signal_type → score). |
| `conviction.py` | Takes signal matrix + regime state → weighted conviction score per ticker. Regime-adaptive weights. |
| `risk_manager.py` | Enforces all risk rules (position size, drawdown, correlation, sector limits). Approves/rejects/sizes trades. |
| `executor.py` | Broker-agnostic order execution. Supports market, limit, stop orders. Handles fill tracking and slippage logging. |
| `earnings_event.py` | Pre-earnings: analyze IV, whisper numbers, transcript tone of prior quarter. Post-earnings: parse transcript, score surprise. |
| `sentiment_momentum.py` | Aggregate Reddit + Twitter sentiment. Compute sentiment momentum (rate of change). Generate long/short signals on extremes. |
| `wheel_options.py` | Select quality stocks for wheel. Sell CSPs at support levels. If assigned, sell CCs at resistance. Manage assignments. |
| `investor_following.py` | Parse ARK daily trades, 13F filings, congressional disclosures. Score conviction based on who's buying and position size. |
| `regime_detector.py` | HMM on S&P 500 returns + VIX → classify bull/bear/sideways + low-vol/high-vol. Also LLM-based narrative regime. |

---

## 3. Configuration Schema

```yaml
# config.yaml
broker:
  mode: paper              # paper | live
  primary: alpaca           # alpaca | ibkr
  alpaca:
    api_key: ${ALPACA_API_KEY}
    secret_key: ${ALPACA_SECRET_KEY}
    base_url: https://paper-api.alpaca.markets  # paper URL
  ibkr:
    host: 127.0.0.1
    port: 7497              # 7497=paper, 7496=live
    client_id: 1

data:
  primary: yfinance         # yfinance | polygon | alpaca
  polygon_api_key: ${POLYGON_API_KEY}
  cache_dir: ./data/cache
  cache_ttl_hours: 1

universe:
  type: sp500               # sp500 | custom | etf
  custom_tickers: []        # Override list
  exclude: []               # Tickers to exclude
  min_market_cap: 1e9       # $1B minimum
  min_avg_volume: 500000    # Shares/day

strategies:
  enabled:
    - earnings_event
    - sentiment_momentum
    - investor_following
    - momentum_factor
    # - wheel_options       # Requires options approval
    # - mean_reversion      # Enable after validation

  earnings_event:
    lookback_quarters: 4
    min_iv_percentile: 50
    position_type: equity   # equity | options
    
  sentiment_momentum:
    reddit_subs: [wallstreetbets, stocks]
    twitter_accounts: []
    lookback_days: 7
    extreme_threshold: 2.0  # Std devs from mean
    
  investor_following:
    watch_list:
      - ark_daily            # ARK daily trades
      - buffett_13f          # Berkshire 13F
      - burry_13f            # Scion 13F (contrarian)
      - congress             # Congressional trades
    min_conviction: 0.6
    
  momentum_factor:
    lookback_months: 12
    skip_months: 1           # Skip most recent month
    rebalance_frequency: monthly
    top_n: 20

risk:
  max_position_pct: 5.0      # Max 5% per position
  max_portfolio_risk_pct: 2.0 # Max 2% risk per trade
  stop_loss_pct: 8.0          # Trailing stop
  daily_drawdown_limit_pct: 3.0
  total_drawdown_limit_pct: 15.0
  max_open_positions: 15
  max_sector_positions: 3
  min_cash_pct: 20.0
  max_options_notional_pct: 20.0

notifications:
  telegram: true
  daily_report: true
  trade_alerts: true
  drawdown_alerts: true

schedule:
  pre_market_scan: "06:00"   # PT
  post_close_report: "13:30" # PT
  weekly_review: "SAT 09:00" # PT
```

---

## 4. Strategy Module Details

### 4.1 Earnings Event-Driven (`earnings_event.py`)
**How it works:**
1. Maintain earnings calendar (next 2 weeks)
2. Pre-earnings (T-5 to T-1):
   - Fetch prior quarter transcript → LLM tone analysis
   - Check options IV percentile → high IV = sell premium, low IV = buy direction
   - Analyze whisper numbers vs consensus
   - Score: bull/bear/neutral with confidence
3. Post-earnings (T+0):
   - Parse new transcript in real-time
   - Compare guidance language to prior quarter
   - Detect tone shifts, hedging, evasion
   - Generate trade signal within 1 hour of release
4. Position: Long equity on positive surprise + positive tone; short via puts on negative

### 4.2 Sentiment Momentum (`sentiment_momentum.py`)
**How it works:**
1. Scrape WSB/r/stocks every 4 hours → ticker mention count + sentiment score
2. Compute 7-day sentiment momentum (rate of change)
3. Extreme bullish sentiment (>2σ) → contrarian short signal
4. Extreme bearish sentiment (< -2σ) → contrarian long signal
5. Rising sentiment + rising price → momentum confirmation → long
6. Cross-reference with options unusual activity for confirmation

### 4.3 Systematic Wheel (`wheel_options.py`)
**How it works:**
1. Universe: Top 50 S&P 500 by quality score (high ROE, low debt, stable earnings)
2. Screen for elevated IV percentile (>50th)
3. Sell cash-secured put at ~0.30 delta, 30-45 DTE
4. If assigned: hold stock, sell covered call at ~0.30 delta
5. If called away: restart cycle
6. Risk: Max 3 wheel positions, max 20% of portfolio in options notional

### 4.4 Investor Following (`investor_following.py`)
**How it works:**
1. **ARK Daily**: Parse cathiesark.com daily trade email → buy/sell signals
2. **13F Quarterly**: On filing dates (Feb 14, May 15, Aug 14, Nov 14), parse new filings for Buffett, Burry, etc.
3. **Congressional**: Monitor QuiverQuant for new Pelosi/top-trader filings
4. **Scoring**: Convergence = higher conviction (e.g., Buffett + ARK both buying = strong signal)
5. Position within 1-2 days of signal, size based on conviction

### 4.5 Mean Reversion (`mean_reversion.py`)
**How it works:**
1. Screen S&P 500 for stocks >2σ below 20-day moving average
2. Confirm with RSI < 30 and volume spike
3. Fundamental check: no negative catalyst (earnings miss, downgrade)
4. Enter long with limit order at lower Bollinger band
5. Target: 20-day MA; Stop: -8% from entry
6. Hold max 10 trading days

### 4.6 Momentum Factor (`momentum_factor.py`)
**How it works:**
1. Monthly: compute 12-1 month price momentum for universe
2. Rank all stocks; go long top 20, avoid bottom 20
3. Equal-weight or inverse-volatility weight
4. Rebalance monthly on first trading day
5. Regime filter: reduce exposure in bear regime (HMM)

---

## 5. Risk Controls & Guardrails

### Hard Limits (Non-Overridable)
| Control | Limit | Action on Breach |
|---------|-------|-----------------|
| Daily drawdown | -3% | Halt all trading for the day |
| Total drawdown | -15% | Halt all trading, notify Zhilong, require manual restart |
| Max position | 5% of portfolio | Reject order |
| Max open positions | 15 | Reject new orders |
| Min cash | 20% | Reject buy orders |
| Max options notional | 20% | Reject options orders |
| Max sector exposure | 3 positions per sector | Reject same-sector orders |

### Soft Limits (Warning + Confirmation)
| Control | Threshold | Action |
|---------|-----------|--------|
| Single trade risk | >1.5% of portfolio | Warn + require confirmation |
| Correlated positions | >0.7 correlation | Warn |
| Low liquidity | <100K avg daily volume | Warn |
| After-hours trading | Any | Warn + wider stops |
| Earnings within 2 days | Any position | Warn about event risk |

### Circuit Breakers
- **VIX > 35**: Reduce all position sizes by 50%, no new positions
- **3 consecutive losing trades**: Pause strategy module for 24 hours, review
- **Signal efficacy < 45% hit rate (30-day)**: Disable signal, notify for review
- **Broker API errors**: 3 failures → halt trading, alert

### Audit Trail
- Every trade logged with: timestamp, signal source, conviction score, risk check results, execution details, P&L
- Weekly signal efficacy report
- Monthly strategy attribution report

---

## 6. User Guide

### Getting Started
```bash
# 1. Install the skill
openclaw skill install us-stock-trading

# 2. Configure API keys
openclaw config set ALPACA_API_KEY=your_key
openclaw config set ALPACA_SECRET_KEY=your_secret

# 3. Start in paper mode (default)
alpha trade config --mode paper

# 4. Run your first scan
alpha scan
```

### Daily Workflow
```
6:00 AM PT  → Auto pre-market scan (signals + earnings calendar)
             → Telegram notification with trade ideas
9:30 AM     → Market opens; pending orders execute
Throughout  → Monitoring: stops, alerts, signal updates
1:00 PM PT  → Market closes; auto daily report
             → P&L summary → Telegram
```

### Key Commands
| Command | Example | What it does |
|---------|---------|-------------|
| `scan` | "alpha scan" | Run all signal modules, show actionable ideas |
| `analyze AAPL` | "analyze Apple" | Deep-dive: technicals, sentiment, fundamentals, LLM thesis |
| `trade` | "buy 100 shares AAPL" | Execute with risk checks and confirmation |
| `portfolio` | "show portfolio" | Current positions, P&L, exposure |
| `earnings` | "upcoming earnings" | Next week's earnings with analysis |
| `signals` | "active signals" | All current signals across modules |
| `whale-watch` | "what are whales buying" | Latest 13F/ARK/Congressional trades |
| `risk` | "risk dashboard" | Current risk metrics, limit utilization |
| `report` | "weekly report" | Performance, attribution, signal efficacy |
| `backtest` | "backtest momentum 2024" | Historical strategy performance |

### Going Live
1. Paper trade for minimum 30 days
2. Review signal efficacy and strategy performance
3. Start with $5K-$10K maximum
4. Set `config --mode live --broker alpaca` (or ibkr)
5. Monitor daily for first 2 weeks
6. Gradually increase allocation as confidence builds

### Safety Features
- **Paper mode by default** — must explicitly enable live trading
- **Trade confirmation** — every live trade requires explicit confirmation
- **Daily P&L alerts** — know immediately if something goes wrong
- **Automatic halt** — circuit breakers stop trading on excessive losses
- **Full audit trail** — every decision logged and reviewable

---

---

## 7. Implementation Priority (Based on Phase 1 Deep Dive)

### Sprint 1 (Week 1-2): Foundation
1. Alpaca paper trading 接入 (`executor.py` + `broker_factory.py`)
2. 数据管道 (`data_pipeline.py` — yfinance + Alpaca)
3. 基础信号引擎 (`signal_engine.py` — RSI, MACD, Bollinger)
4. Portfolio tracker + Telegram reporting

### Sprint 2 (Week 3-4): Core Strategies
5. Earnings Event strategy (`earnings_analyzer.py` + `earnings_event.py`)
6. Congressional/13F Following (`filing_parser.py` + `investor_following.py` — Quiver API)
7. Risk manager 完整实现

### Sprint 3 (Week 5-6): AI Layer
8. Sentiment pipeline (`sentiment_scraper.py` — Reddit + Twitter)
9. LLM earnings transcript analysis
10. Multi-agent debate (参考 TradingAgents 架构: analyst→researcher debate→trader→risk)

### Sprint 4 (Week 7-8): Polish
11. Regime detector (HMM + LLM narrative)
12. Signal efficacy tracking
13. Backtesting suite
14. Documentation + ClawHub 发布准备

*Phase 3 skill design complete. Ready for implementation.*
