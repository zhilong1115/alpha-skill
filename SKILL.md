---
name: us-stock-trading
description: |
  Agent-driven US stock day trading on Alpaca (paper or live). The agent IS the trader —
  it scans, decides, executes, verifies fills, manages risk, and reports P&L.
  
  NOT a trading script. The Python modules are tools the agent calls.
  The agent owns the decision loop: observe → judge → act → verify → adapt.
  
  V3.1: Intraday journal for cross-cycle memory + news interrupt system.
  
  Use when: day trading US stocks, scanning for setups, managing positions,
  checking P&L, or any intraday equities task.
---

# US Stock Day Trading — Agent Skill

You are a day trader. The Python modules are your instruments. You make the calls.

## Setup

```bash
cd /path/to/us-stock-trading
source .venv/bin/activate
```

Configure your Alpaca API keys in environment or `.env`:
```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
```

## Agent Tools

All tools in `scripts/intraday/agent_tools.py`:

```python
from scripts.intraday.agent_tools import *
```

### Intraday Journal (cross-cycle memory)
| Function | Returns |
|----------|---------|
| `read_journal()` | Today's journal entries (news, trades, observations) |
| `write_journal(type, content)` | Append entry to today's journal |

Journal entry types: `"trade"`, `"observation"`, `"decision"`, `"news"`, `"alert"`

The journal is a JSONL file (`data/journal/YYYY-MM-DD.jsonl`) that persists across
15-minute cron cycles. The news daemon writes breaking news here automatically.
Always read the journal first to know what happened earlier in the day.

### Account & Positions
| Function | Returns |
|----------|---------|
| `account()` | `{equity, cash, buying_power, portfolio_value}` |
| `positions()` | All positions with real-time P&L |
| `position("AAPL")` | Single position or None |
| `orders_today()` | All filled orders today |

### Market Data & Signals
| Function | Returns |
|----------|---------|
| `price("AAPL")` | Latest price |
| `prices(["AAPL","NVDA"])` | `{ticker: price}` dict |
| `spread("AAPL")` | `{bid, ask, spread_pct, acceptable}` |
| `market_regime()` | `{vix, size_multiplier}` |
| `scan(top_n=15)` | Ranked candidates with scores + signals |
| `signals("AAPL")` | Full intraday signals for a ticker |
| `news_alerts()` | Pending news catalysts from daemon |

### Execution (returns fill details — never fire-and-forget)
| Function | Returns |
|----------|---------|
| `buy("AAPL", 100)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `sell("AAPL", 50)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `close("AAPL")` | Close entire position |
| `close_all()` | Close everything — EOD hard close |
| `reconcile_pnl()` | True P&L from Alpaca fills (source of truth) |
| `suggest_size("AAPL", price, equity, atr)` | Advisory sizing (agent decides final qty) |

## Trading Loop (agent-driven)

```
0. READ JOURNAL
   read_journal() → today's news, earlier trades, observations
   This is your intraday memory — know what happened before this cycle.

1. CHECK STATE
   account() → buying power, equity
   positions() → current exposure

2. MANAGE EXISTING POSITIONS
   For each position: stop hit? target reached? news impact? time pressure?
   close(ticker) or sell(ticker, partial_qty) as needed

3. SCAN FOR NEW SETUPS
   scan() → ranked candidates (Alpaca Screener: most actives, gainers, losers)
   signals(ticker) → detailed view for top picks
   spread(ticker) → confirm acceptable before entry

4. EXECUTE WITH CONVICTION
   suggest_size() → advisory (agent decides final qty — size aggressively on high conviction)
   buy(ticker, qty) → verify fill from result
   write_journal("trade", {"note": "Bought 500 DOW@37.2 — ORB breakout + volume"})
   
5. RECONCILE
   reconcile_pnl() → true P&L from exchange fills (not local state)
```

## News Interrupt System

The real-time news daemon (`scripts/monitoring/realtime_news.py`) monitors:
- Alpaca WebSocket (real-time market news)
- RSS feeds (CNBC, Reuters, MarketWatch, Yahoo)
- Finnhub (additional coverage)

**How it works:**
1. All news alerts → written to intraday journal automatically
2. Critical news (Fed decisions, major earnings, crashes) → triggers an **immediate**
   trading cycle via `openclaw cron run`, so the agent reacts within seconds
3. Regular news → agent sees it next cycle when it reads the journal

**No hardcoded session IDs.** The interrupt fires the trading cron job directly.

## Sizing Philosophy

No hard caps on position size or count. The agent sizes based on conviction:
- `suggest_size()` returns a suggestion (default ~25% of equity)
- Agent can override — 20-30% per position on high conviction is fine
- Goal: maximize daily P&L
- Safety net: $2K daily loss circuit breaker (last resort only)

## Hard Constraints

- **EOD forced close**: All positions must close by 12:45 PM PST. No overnight holds.
- **Opening volatility**: Avoid trading 6:30-6:45 AM PST.

## Agent Judgment (everything else)

No hardcoded score thresholds or size multipliers. The agent reads live data and decides:
- Entry timing and signal quality
- Position sizing based on conviction + VIX / regime
- When to exit early vs. let a winner run
- Spread acceptability
- How many positions to hold at once
- How to react to breaking news

## Reporting

Report to Telegram only when trades are executed. Otherwise silent (HEARTBEAT_OK).

## Architecture

```
scripts/intraday/
├── agent_tools.py    # Agent tool functions (V3.1 — use this)
├── scanner.py        # Candidate scanning (Alpaca Screener + gap + news)
├── signals.py        # Technical signals (VWAP, ORB, RSI, momentum)
├── risk.py           # Risk calculations (advisory limits)
└── trader.py         # Legacy (deprecated for agent use)

scripts/monitoring/
├── realtime_news.py  # News daemon — writes journal + triggers interrupts
└── news_monitor.py   # News analysis

data/
├── journal/          # Intraday journal (YYYY-MM-DD.jsonl) — cross-cycle memory
└── alerts/           # News daemon pending alerts
```
