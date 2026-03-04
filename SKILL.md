---
name: us-stock-trading
description: |
  Agent-driven US stock day trading on Alpaca (paper or live). The agent IS the trader —
  it scans, decides, executes, verifies fills, manages risk, and reports P&L.
  
  NOT a trading script. The Python modules are tools the agent calls.
  The agent owns the decision loop: observe → judge → act → verify → adapt.
  
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
| `news_alerts()` | Pending news catalysts |

### Execution (returns fill details — never fire-and-forget)
| Function | Returns |
|----------|---------|
| `buy("AAPL", 100)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `sell("AAPL", 50)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `close("AAPL")` | Close entire position |
| `close_all()` | Close everything — EOD hard close |
| `reconcile_pnl()` | True P&L from Alpaca fills (source of truth) |
| `suggest_size("AAPL", price, equity, atr)` | Advisory sizing |

## Trading Loop (agent-driven)

```
1. CHECK STATE
   account() → buying power, equity
   positions() → current exposure

2. MANAGE EXISTING POSITIONS
   For each position: stop hit? target reached? time pressure?
   close(ticker) or sell(ticker, partial_qty) as needed

3. SCAN FOR NEW SETUPS
   scan() → ranked candidates
   signals(ticker) → detailed view for top picks
   spread(ticker) → confirm acceptable before entry

4. EXECUTE WITH CONVICTION
   suggest_size() → advisory sizing
   buy(ticker, qty) → verify fill from result
   
5. RECONCILE
   reconcile_pnl() → true P&L from exchange fills (not local state)
```

## Hard Constraints

- **EOD forced close**: All positions must close before market close (configure your own cutoff time). No overnight holds on paper accounts.
- **Opening volatility**: Avoid trading the first 10-15 minutes after open.

## Agent Judgment (everything else)

No hardcoded time cutoffs, score thresholds, or size multipliers. The agent reads live data and decides:
- Entry timing and signal quality
- Position sizing based on VIX / regime
- When to exit early vs. let a winner run
- Spread acceptability
- How many positions to hold at once

## Reporting

Configure your own notification channel (Telegram group, Slack, etc.).
Report only when trades are executed. Otherwise silent (HEARTBEAT_OK).

## Cron Setup

Example cron prompt for 15-min intraday cycle:

```
You are running an intraday US stock trading cycle on Alpaca.

HARD CONSTRAINTS:
1. All positions MUST be closed by [YOUR EOD TIME] — no overnight holds
2. Avoid trading the first 15 minutes after open

TOOLS: cd [PROJECT_DIR] && source .venv/bin/activate && PYTHONPATH=. python -c '...'
  from scripts.intraday.agent_tools import account, positions, orders_today
  from scripts.intraday.agent_tools import scan, signals, spread, suggest_size
  from scripts.intraday.agent_tools import buy, sell, close, close_all
  from scripts.intraday.agent_tools import market_regime, reconcile_pnl

YOUR JOB:
  1. Check state
  2. Manage open positions
  3. Scan for opportunities — evaluate, size, decide
  4. Execute with conviction — verify fills
  5. If close to EOD, prefer exits over new entries

Report to [YOUR CHANNEL] only if trade executed. Otherwise HEARTBEAT_OK.
```

## Architecture

```
scripts/intraday/
├── agent_tools.py    # Agent tool functions (V3.0 — use this)
├── scanner.py        # Candidate scanning
├── signals.py        # Technical signals
├── risk.py           # Risk calculations
└── trader.py         # Legacy (deprecated for agent use)
```
