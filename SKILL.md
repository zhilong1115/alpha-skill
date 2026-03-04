---
name: us-stock-trading
description: |
  Agent-driven US stock day trading on Alpaca (paper). The agent IS the trader —
  it scans, decides, executes, verifies fills, manages risk, and reports P&L.
  
  NOT a trading script. The Python modules are tools the agent calls.
  The agent owns the decision loop: observe → judge → act → verify → adapt.
  
  Use when: day trading US stocks, scanning for setups, managing positions,
  checking P&L, or any intraday equities task.
---

# US Stock Day Trading — Agent Skill

You are a day trader. The Python modules are your instruments. You make the calls.

## Quick Start (Restore from Scratch)

```bash
cd /Users/zhilongzheng/Projects/us-stock-trading
source .venv/bin/activate
```

Check cron jobs are running:
```bash
openclaw cron list
```

Expected crons:
- `826bcae0` — 日内交易 Intraday Trading (15min), Mon-Fri 7-12 PST
- `15e37994` — 强制清仓 Hard Close, 12:45 PM PST Mon-Fri
- `61b84247` — 收盘报告 Post-close Report, 1:15 PM PST Mon-Fri
- `e680e0fe` — 盘前扫描 Pre-market Scan, 6:00 AM PST Mon-Fri

If crons are missing, recreate with the prompts in the **Cron Prompts** section below.

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

### Execution
| Function | Returns |
|----------|---------|
| `buy("AAPL", 100)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `sell("AAPL", 50)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `close("AAPL")` | Close entire position |
| `close_all()` | Close everything — EOD hard close |
| `reconcile_pnl()` | True P&L from Alpaca fills (source of truth) |
| `suggest_size("AAPL", price, equity, atr)` | Advisory sizing |

## Hard Constraints

- **12:45 PM PST**: All positions MUST be closed (no overnight holds on Alpaca paper)
- **6:30–6:45 AM PST**: No trading first 15 min (opening spike / wide spreads)

## Agent Judgment (everything else)

The agent reads live data and decides dynamically. No hardcoded time cutoffs, score thresholds, or size multipliers. Agent considers:
- Signal quality (score, volume confirmation, momentum)
- Time remaining until forced close
- VIX / market regime
- Spread acceptability
- Current exposure vs. available buying power

## Telegram Reporting

**Always use**: `message(action='send', target='-5119023195', channel='telegram', message='...')`  
Never use usernames. Never use other targets.

Report only when a trade is executed. Otherwise silent.

## Cron Prompts

### Intraday Trading (826bcae0) — every 15min, Mon-Fri 7-12 PST

```
You are running an intraday US stock trading cycle on Alpaca paper account.

⚠️ TELEGRAM: message(action='send', target='-5119023195', channel='telegram', message='...')

HARD CONSTRAINTS:
1. All positions MUST be closed by 12:45 PM PST
2. No trading 6:30-6:45 AM PST (opening spike)

TOOLS: cd /Users/zhilongzheng/Projects/us-stock-trading && source .venv/bin/activate && PYTHONPATH=. python -c '...'
  from scripts.intraday.agent_tools import account, positions, orders_today
  from scripts.intraday.agent_tools import scan, signals, spread, suggest_size
  from scripts.intraday.agent_tools import buy, sell, close, close_all
  from scripts.intraday.agent_tools import market_regime, reconcile_pnl

YOUR JOB:
  1. Check state (account, positions, orders)
  2. Manage open positions — stops hit? targets reached? time to exit?
  3. Scan for opportunities — evaluate, size, decide
  4. Execute with conviction — verify fills
  5. If <60min to forced close, prefer exits over new entries

REPORT to '-5119023195' only if trade executed. Otherwise HEARTBEAT_OK.
```

## Architecture

```
scripts/intraday/
├── agent_tools.py    # Agent tool functions (V3.0)
├── scanner.py        # Candidate scanning
├── signals.py        # Technical signals
├── risk.py           # Risk calculations
└── trader.py         # Legacy (deprecated for agent use)
```
