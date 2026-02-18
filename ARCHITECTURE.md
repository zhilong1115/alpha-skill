# Phase 2: Synthesis Report

*Date: 2026-02-17 | Author: Alpha*

---

## 1. Strategy Ranking

| Rank | Strategy | Feasibility (AI Agent) | Expected Edge | Data Availability | Complexity | Overall |
|------|----------|----------------------|---------------|-------------------|------------|---------|
| 1 | **Earnings Event-Driven** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **A+** |
| 2 | **Sentiment-Driven Momentum** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Medium | **A** |
| 3 | **Systematic Options Selling (Wheel/CSP)** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Medium | **A** |
| 4 | **Notable Investor Following (13F/ARK)** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Low | **A-** |
| 5 | **Momentum Factor (Monthly Rebalance)** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Low | **B+** |
| 6 | **Mean Reversion on Liquid Equities** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **B+** |
| 7 | **Congressional Trading Following** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | Low | **B+** |
| 8 | **Regime-Aware Multi-Factor** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | High | **B** |
| 9 | **Pairs Trading / Stat Arb** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | High | **B-** |
| 10 | **WSB Crowd Picks Tracking** | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | Low | **B-** |

### Why Earnings Event-Driven Ranks #1
- **AI agent's unique edge**: LLM can parse earnings calls in real-time, detect tone shifts, hedging language, guidance changes — things traditional algos cannot do
- **Rich data**: Transcripts, whisper numbers, options IV, historical surprise data all freely available
- **Definable edge**: IV crush is a known phenomenon; earnings surprise direction can be estimated from transcript tone
- **Bounded risk**: Event-driven trades have natural time horizons (pre/post earnings)

---

## 2. Signal Taxonomy

### Fundamental Signals
| Signal | Source | Frequency | AI Agent Advantage |
|--------|--------|-----------|-------------------|
| Earnings surprise direction | Transcripts, guidance | Quarterly | LLM tone analysis |
| Revenue growth acceleration | SEC filings | Quarterly | Cross-company comparison |
| Margin expansion/contraction | 10-Q/10-K | Quarterly | Footnote detection |
| Management quality/honesty | Earnings calls | Quarterly | Lie/hedge detection |
| Buffett new positions | 13F filings | Quarterly | High-signal, parseable |
| ARK daily trades | cathiesark.com | Daily | Real-time theme signal |

### Technical Signals
| Signal | Source | Frequency | AI Agent Advantage |
|--------|--------|-----------|-------------------|
| Price momentum (12-1 mo) | OHLCV data | Daily | Regime-conditional application |
| Mean reversion (Bollinger) | OHLCV data | Daily | Combine with fundamental context |
| Volume anomalies | OHLCV data | Daily | Cross-reference with sentiment |
| Moving average crossovers | OHLCV data | Daily | Simple, robust |
| RSI extremes | OHLCV data | Daily | Contrarian entry timing |

### Sentiment Signals
| Signal | Source | Frequency | AI Agent Advantage |
|--------|--------|-----------|-------------------|
| WSB ticker mention frequency | Reddit API | Real-time | Quantifiable crowd wisdom |
| WSB sentiment polarity | Reddit API | Real-time | NLP analysis at scale |
| FinTwit consensus | X/Twitter | Real-time | Multi-account aggregation |
| Earnings call tone shift | Transcripts | Quarterly | Q-over-Q comparison |
| News sentiment momentum | News APIs | Daily | Rate of change matters |
| Chinese retail flow (小红书) | XHS/web | Weekly | Cross-cultural leading indicator |
| Inverse Cramer | X/Twitter | Per call | Documented contrarian signal |

### Event Signals
| Signal | Source | Frequency | AI Agent Advantage |
|--------|--------|-----------|-------------------|
| Earnings date + IV levels | Options data | Quarterly | Pre-position for IV crush |
| Fed statement parsing | Fed website | 8x/year | Word-by-word diff analysis |
| CPI/NFP/PMI surprises | Economic calendars | Monthly | Context-aware interpretation |
| Congressional trade filings | House/Senate | As filed | Legislative catalyst detection |
| 13F filing dates | SEC EDGAR | Quarterly | Calendar-based positioning |
| M&A announcements | News | Irregular | Second-order effect reasoning |

### Flow Signals
| Signal | Source | Frequency | AI Agent Advantage |
|--------|--------|-----------|-------------------|
| Options unusual activity | Barchart/UW | Daily | Size + direction analysis |
| 0DTE volume spikes | Broker data | Intraday | Gamma exposure prediction |
| ETF fund flows | ETF.com | Daily | Sector rotation detection |
| Insider buying/selling | SEC Form 4 | As filed | Cross-ref with sentiment |
| Burry portfolio changes | 13F | Quarterly | Bubble/excess detection |

---

## 3. Architecture Design

```
┌─────────────────────────────────────────────────────┐
│                   ALPHA TRADING AGENT                │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  DATA LAYER  │  │SIGNAL ENGINE │  │  STRATEGY  │ │
│  │              │  │              │  │  MODULES   │ │
│  │ • Price/Vol  │→ │ • Technical  │→ │            │ │
│  │ • Fundmntls  │  │ • Sentiment  │  │ • Earnings │ │
│  │ • News/Sent  │  │ • Event      │  │ • Momentum │ │
│  │ • Alt Data   │  │ • Flow       │  │ • Wheel    │ │
│  │ • 13F/Filings│  │ • Fundmntl   │  │ • 13F-Fllw │ │
│  └──────────────┘  └──────────────┘  │ • MeanRev  │ │
│                                      └─────┬─────┘ │
│                                            │       │
│  ┌──────────────┐  ┌──────────────┐        │       │
│  │   RISK MGR   │← │  CONVICTION  │← ──────┘       │
│  │              │  │  SYNTHESIZER │                  │
│  │ • Pos sizing │  │              │                  │
│  │ • Stop loss  │  │ • Weighted   │                  │
│  │ • Drawdown   │  │   combine    │                  │
│  │ • Correlation│  │ • Regime     │                  │
│  │ • Max pos    │  │   adjust     │                  │
│  └──────┬───────┘  └──────────────┘                  │
│         │                                            │
│  ┌──────▼───────┐  ┌──────────────┐                  │
│  │  EXECUTION   │  │  MONITORING  │                  │
│  │              │  │              │                  │
│  │ • Alpaca(dev)│  │ • P&L track  │                  │
│  │ • IBKR(prod) │  │ • Signal eff │                  │
│  │ • Order mgmt │  │ • Alerts     │                  │
│  │ • Slippage   │  │ • Reports    │                  │
│  └──────────────┘  └──────────────┘                  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Signal Generation Layer
- **Indicators**: pandas-ta for technical indicators (RSI, MACD, Bollinger, MA crossovers)
- **LLM Analysis**: Claude via OpenClaw for:
  - Earnings transcript tone analysis
  - News sentiment with context
  - Fed statement parsing (hawkish/dovish scoring)
  - Narrative regime detection
  - 13F filing interpretation
- **Sentiment Pipeline**: Reddit API → NLP scoring → ticker-level sentiment time series

### Risk Management
| Control | Rule | Rationale |
|---------|------|-----------|
| Max position size | 5% of portfolio | Prevent concentration |
| Per-trade risk | 1-2% of portfolio | Survive losing streaks |
| Stop loss | -8% trailing or strategy-specific | Limit drawdown per trade |
| Daily drawdown limit | -3% | Stop trading on bad days |
| Total drawdown limit | -15% | Pause all trading, review |
| Correlation limit | Max 3 positions in same sector | Diversification |
| Max open positions | 10-15 | Manageable for monitoring |
| Options: max notional | 20% of portfolio | Limit leverage |
| Cash reserve | Min 20% | Dry powder + margin safety |

### Execution Layer
- **Alpaca (Development/Paper)**:
  - `alpaca-py` SDK
  - Paper trading for all strategy validation
  - Free real-time data (IEX)
  - Commission-free
- **IBKR (Production)**:
  - `ib_insync` async Python wrapper
  - Full options chain access
  - Global market access
  - Professional execution quality
- **Order Management**:
  - Limit orders preferred (reduce slippage)
  - TWAP for larger positions
  - Pre/post market orders for earnings plays

### Monitoring & Reporting
- Daily P&L summary → Telegram notification
- Signal efficacy tracking (which signals are working?)
- Weekly strategy performance report
- Drawdown alerts (real-time)
- Monthly portfolio review with rebalancing recommendations

---

## 4. Recommended Stack

| Component | Primary | Backup | Cost |
|-----------|---------|--------|------|
| **Broker (Dev)** | Alpaca | — | Free |
| **Broker (Prod)** | IBKR | Alpaca | ~$0-10/mo |
| **Historical Data** | yfinance | Polygon.io | Free / $29/mo |
| **Real-time Data** | Alpaca WebSocket | Polygon.io WS | Free / $29/mo |
| **News/Sentiment** | Tiingo News API | NewsAPI | $10/mo |
| **Earnings Transcripts** | Financial Modeling Prep | Seeking Alpha | $20-50/mo |
| **13F Data** | SEC EDGAR (direct) | WhaleWisdom | Free |
| **Congressional Data** | QuiverQuant API | Capitol Trades | Free |
| **Reddit Sentiment** | Reddit API + PRAW | Pushshift | Free |
| **Backtesting** | VectorBT | QuantConnect | Free |
| **Technical Analysis** | pandas-ta | ta-lib | Free |
| **LLM Reasoning** | Claude (via OpenClaw) | — | Included |
| **Orchestration** | OpenClaw Skill | — | Included |
| **Notifications** | Telegram (via OpenClaw) | — | Included |

**Total estimated cost**: $0-60/month depending on data tier

---

---

## 5. Key Updates from Phase 1 Deep Dive (2026-02-17)

### TradingAgents 论文验证 (UCLA + MIT)
Multi-agent LLM 框架在 AAPL/GOOGL/AMZN 上测试(2024.6-11):
- **AAPL**: +26.62% CR, 8.21 SR (vs B&H -5.23%)
- **GOOGL**: +24.36% CR, 6.39 SR (vs B&H +7.78%)
- **AMZN**: +23.21% CR, 5.60 SR (vs B&H +17.1%)
- MDD 极低 (0.91-2.11%)
- **启示**: Multi-agent 分析确实能产生 alpha，但5个月3只股票可能过拟合。我们的 skill 应采用类似的 analyst→debate→trade→risk 流程，但需更广泛的 universe 和更长的回测期。

### Congressional Trading Alpha 确认
- CEPR 学术研究: 国会领导层上任后超额收益 **+47个百分点/年**
- Unusual Whales 2024 报告: 32% 国会议员跑赢 S&P 500, top 5 翻倍
- **策略调整**: Congressional Following 从 B+ 升级到 A-，尤其是跟踪领导层交易

### ClawHub 市场空白
- 5700+ skills 中零真正的量化交易 skill
- Stock Market Pro (最接近的) 只做数据展示
- **机会**: 我们的 skill 是 first-mover

*Phase 2 synthesis complete. All signals, strategies, and architecture designed for implementation.*
