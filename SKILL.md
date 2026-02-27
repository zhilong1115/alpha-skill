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

## Setup

```bash
cd /Users/zhilongzheng/Projects/us-stock-trading
source .venv/bin/activate
```

## Agent Tools

All tools are in `scripts/intraday/agent_tools.py`. Import and call from Python:

```python
from scripts.intraday.agent_tools import *
```

### Account & Positions (check these first, always)
| Function | Returns |
|----------|---------|
| `account()` | `{equity, cash, buying_power, portfolio_value}` |
| `positions()` | All Alpaca positions with real-time P&L |
| `position("AAPL")` | Single position or None |

### Market Data
| Function | Returns |
|----------|---------|
| `price("AAPL")` | Latest price (float) |
| `prices(["AAPL","NVDA"])` | `{ticker: price}` dict |
| `spread("AAPL")` | `{bid, ask, spread_pct, acceptable}` |
| `market_regime()` | `{vix, size_multiplier}` |

### Scanning & Signals
| Function | Returns |
|----------|---------|
| `scan(top_n=15)` | Ranked candidates with scores + signals |
| `signals("AAPL")` | Intraday signals for a specific ticker |
| `news_alerts()` | Pending news catalysts |

### Order Execution (returns fill details, never fire-and-forget)
| Function | Returns |
|----------|---------|
| `buy("AAPL", 100)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `sell("AAPL", 50)` | `{status, filled_qty, filled_avg_price, order_id}` |
| `close("AAPL")` | Close entire position, returns `{pnl, filled_avg_price}` |
| `close_all()` | Close everything — hard EOD |

### P&L Reconciliation (source of truth = Alpaca, not local state)
| Function | Returns |
|----------|---------|
| `orders_today()` | All filled orders from Alpaca today |
| `reconcile_pnl()` | True P&L per symbol from actual fills |

### Risk Sizing (advisory — you decide)
| Function | Returns |
|----------|---------|
| `suggest_size("AAPL", price, equity, atr)` | `{qty, stop_price, target_price, notes}` |

## Trading Loop (agent-driven)

This is what YOU do every cycle. Not a script — your judgment at every step.

```
1. CHECK STATE
   acct = account()
   pos = positions()
   → Do I have buying power? Any positions to manage?

2. MANAGE EXISTING POSITIONS
   For each position:
   - Check current price vs my stop / target
   - Has it hit my stop? → close(ticker), verify fill, record loss
   - Has it hit target? → close(ticker) or partial sell, verify fill
   - Has it been open too long with no movement? → consider closing
   - Should I move my stop (trail)? → that's a mental note, not an order

3. SCAN FOR NEW SETUPS (if I have capacity)
   candidates = scan()
   → Filter: direction=long, score>0.3, signal_score>0.1
   → Check spread for each: spread(ticker)
   → Check signals(ticker) for more detail if needed

4. DECIDE AND EXECUTE
   For each pick:
   - suggest_size(ticker, px, equity, atr) → advisory
   - I decide final qty (maybe less, maybe staged)
   - result = buy(ticker, qty)
   - VERIFY: result["status"] == "filled"?
     - Yes → record entry price from result["filled_avg_price"]
     - No → decide: retry? skip? adjust qty?

5. MONITOR FILLS
   After any sell:
   - Verify with position(ticker) that it's actually closed
   - Record P&L from fill price, not snapshot

6. RECONCILE
   At end of day: reconcile_pnl() → true P&L from Alpaca fills
   This is what you report. Not local state.

7. REPORT
   Post to Telegram: trades, P&L, win rate
   Use reconcile_pnl() numbers, not internal state
```

## Risk Rules

- **Max 5 positions** at once
- **6.5% of portfolio** per position (max)
- **Stop-loss**: 1.5× ATR (floor 0.5%, cap 5%)
- **Take-profit**: 3× ATR (2:1 R/R)
- **Daily loss cap**: $500 or 0.5% of portfolio → stop trading
- **Spread filter**: Skip if bid-ask > 0.5%
- **Time windows** (ET):
  - 9:30-10:30: Power hour (1.5× size)
  - 10:30-11:30: Normal (1.0×)
  - 11:30-13:00: Midday chop (0.3×)
  - 13:00-15:00: Afternoon (0.8×)
  - After 14:15: No new entries (90 min before close)
  - 15:45: HARD CLOSE everything
- **Per-symbol**: Max 2 trades/day, half size on re-entry after loss
- **3 consecutive losses**: 30 min pause + 50% size

## Schedule (Mon–Fri PT)

| Time | Action |
|------|--------|
| 6:00 AM | Pre-market scan, plan the day |
| 6:30 AM | Market open — start trading loop |
| 6:30 AM – 12:30 PM | Active trading (every 15 min or as needed) |
| 12:45 PM | Hard close all positions |
| 1:00 PM | Reconcile P&L, post daily report |

## Legacy CLI (still works, but prefer agent_tools)

```bash
python cli.py day-scan          # Scan for candidates
python cli.py day-trade         # Dry run
python cli.py day-trade --execute  # Execute (old script mode)
python cli.py day-status        # Today's status
python cli.py day-close         # Force close all
python cli.py pulse             # Market dashboard
```

## Architecture

```
scripts/intraday/
├── agent_tools.py    # ← Agent tool functions (V3.0 — use this)
├── scanner.py        # Pre-market scanning (called by agent_tools.scan)
├── signals.py        # Technical signals (called by agent_tools.signals)
├── risk.py           # Risk calculations (called by agent_tools.suggest_size)
└── trader.py         # Legacy V2.x trader class (deprecated for agent use)
```
