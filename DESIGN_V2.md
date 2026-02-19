# V2 Architecture: Dual-Mode Trading System

## Overview
Two independent trading modes with separate logic, signals, and risk management.

---

## Mode A: Swing Recommendations (for Zhilong on Robinhood)

### Workflow
1. **6:00 AM PT** — Morning scan (full universe, daily signals)
2. Generate 3-5 top picks with:
   - Ticker, conviction score, entry price zone
   - Catalyst / reasoning (earnings, sector rotation, technicals)
   - Target price (5-15% upside)
   - Stop-loss level
3. Send to Telegram as formatted recommendation
4. Zhilong decides & reports what he bought
5. **Track his portfolio**: store positions, monitor daily
6. **Alert on**: stop-loss hits, target reached, negative news, exit signal change

### Signals (Daily timeframe)
- RSI, MACD, Bollinger Bands, SMA crossovers, Volume
- Momentum factor, mean reversion
- Sentiment (Reddit, news)
- Regime-adaptive weights

### Data
- `data/swing_portfolio.json` — Zhilong's Robinhood positions
- CLI: `swing-recommend`, `swing-add <ticker> <qty> <price>`, `swing-remove`, `swing-status`

---

## Mode B: Intraday Trading (Alpha on Alpaca)

### Core Principles
- **All positions opened and closed same day** — NO overnight holds
- **Selective**: 3-8 trades/day, only high-conviction setups
- **Catalyst-driven**: news, earnings, gap-ups/downs, volume spikes
- **Fast signals**: 5-min candles, VWAP, ORB, momentum

### Stock Selection (NOT the same as swing)
Must have at least one:
- **Catalyst today**: earnings, FDA, analyst upgrade, breaking news
- **Gap**: >2% gap up or down from previous close
- **Volume spike**: >3x average volume in first 30 min
- **Reddit/social buzz**: sudden mention surge

Plus requirements:
- Average daily volume > 1M shares (liquidity)
- Price > $5 (no penny stocks)
- Spread < 0.1% (tight spreads for clean entry/exit)

### Signal Engine (5-min candles)
1. **VWAP**: above/below + distance (mean reversion vs trend)
2. **Opening Range Breakout (ORB)**: first 15-min high/low as levels
3. **Intraday momentum**: 5-bar slope of close
4. **RSI(14) on 5-min**: overbought/oversold for timing
5. **Volume profile**: relative volume vs session average
6. **News sentiment**: real-time from daemon alerts

### Trade Types
1. **Gap & Go**: Stock gaps up >3% on catalyst, buy pullback to VWAP
2. **ORB Breakout**: Break above 15-min opening range on volume
3. **News Momentum**: Breaking positive news → immediate entry
4. **Mean Reversion**: Overextended move → fade back to VWAP
5. **Red-to-Green**: Opens red, reverses to green with volume

### Risk Management (Intraday-specific)
- Max position size: 10% of portfolio per trade
- Max concurrent positions: 5
- Max daily loss: 1% of portfolio ($1,000) → stop trading for day
- Per-trade stop-loss: 1-2% (tight, intraday)
- Per-trade target: 2-4% (2:1 reward/risk minimum)
- **Hard close at 12:45 PM PT** — liquidate everything before close

### Schedule
- 5:50 AM: News daemon start
- 6:00 AM: Pre-market scan — identify candidates (gaps, news, volume)
- 6:30 AM: Opening range established → first trades
- Every 15 min 6:30-12:30: Scan for new setups + manage open positions
- 12:45 PM: **Force close all positions**
- 1:00 PM: Daily intraday P&L report
- 1:15 PM: News daemon stop

### Data
- `data/intraday_state.json` — today's trades, P&L, positions
- Cleared daily at market open
- Historical: `data/intraday_history/YYYY-MM-DD.json`

---

## What Changes from V1

### Delete / Deprecate
- Current 15 swing positions on Alpaca → sell all, clean slate
- `generate_trade_ideas()` with daily signals for Alpaca → replaced by intraday logic
- Mixed conviction (60% daily + 40% intraday) → separate completely

### New Modules
- `scripts/intraday/scanner.py` — Pre-market gap/volume/catalyst scanner
- `scripts/intraday/signals.py` — 5-min candle signal engine (rewrite of intraday_signals.py)
- `scripts/intraday/trader.py` — Intraday execution: entries, exits, position management
- `scripts/intraday/risk.py` — Intraday risk: daily loss limit, position limits, hard close
- `scripts/swing/recommender.py` — Daily recommendation generator
- `scripts/swing/tracker.py` — Track Zhilong's Robinhood positions, alerts

### Keep
- News daemon (`realtime_news.py`) — shared by both modes
- Signal engine (`signal_engine.py`) — used by swing recommender
- Conviction (`conviction.py`) — used by swing recommender
- Data pipeline (`data_pipeline.py`) — shared
- Executor (`executor.py`) — used by intraday trader (Alpaca)
- LLM judge (`llm_judge.py`) — used by both

### Cron Schedule (Weekdays PT)
- 5:50 AM: News daemon start
- 6:00 AM: **Swing recommendations** → Telegram (daily signals, full universe)
- 6:00 AM: **Intraday pre-market scan** → identify day trade candidates
- 6:30 AM: First intraday trades
- Every 15 min 6:30-12:30: Intraday cycle (scan + trade + manage)
- 12:45 PM: **Hard close** all intraday positions
- 1:00 PM: Intraday P&L report → Telegram
- 1:15 PM: News daemon stop
- 9:00 PM: Swing portfolio check (Zhilong's positions) → alert if needed
- 10:00 PM: Polymarket scan
