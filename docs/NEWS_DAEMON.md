# Friday AI Market News Daemon

## What it does

1. Collects real-time market news from Alpaca/Benzinga, Jin10 (金十数据),
   CNBC, MarketWatch, Reuters, Yahoo Finance, CoinDesk, CoinTelegraph,
   The Block, Decrypt, and Finnhub.
2. Applies a cheap rule filter, de-duplicates syndicated headlines, and keeps a
   daily event ledger under `data/alerts/event_log/`. Cross-source versions of
   the same event are sent once; materially revised numbers can become updates.
3. The daemon wakes the isolated, tool-free Friday Flash agent only when a new
   material batch is ready.
4. For scheduled releases, Friday Flash compares actual/consensus/prior values,
   traces the Fed/yields/USD/risk-appetite transmission chain, and maps first-
   and second-order effects to stocks/ETFs, futures/commodities/FX, gold/silver,
   and crypto. Each view includes direction, horizon, confidence, catalyst,
   invalidation, and scenario-based strategy references.
5. Delivers the brief to Telegram. It never places a trade.

## Safety and cost controls

- Trading interrupts are disabled unless `NEWS_ENABLE_TRADING_INTERRUPT=1`.
- Neutral, unattributed whale transfers are suppressed.
- AI analysis batches have a five-minute cooldown and are capped at 40 per day.
- The five-minute hard event window and daily ledger suppress duplicates across
  Alpaca, Jin10, RSS, and Finnhub without hiding genuinely revised data.
- A model may return `NO_REPLY`; nothing is sent in that case.
- External news is explicitly treated as untrusted content.

## Runtime

The macOS LaunchAgent `com.friday.market-news-daemon` keeps collection and the
Friday Flash briefing loop running 24/7, including weekends for crypto coverage.

Useful commands:

```bash
launchctl print gui/$(id -u)/com.friday.market-news-daemon
tail -f data/news_daemon.launch.log
tail -f data/news_briefing.log
.news-venv/bin/python -m scripts.monitoring.news_briefing --once --dry-run
```

Runtime state is stored under `data/alerts/` and is ignored by Git.

## Jin10 settings

Jin10's Chinese flash feed is polled every 20 seconds. By default only its
editor-starred important items enter AI analysis, which limits duplicate and
low-value notifications.

- `JIN10_ENABLED=0` disables the source.
- `JIN10_IMPORTANT_ONLY=0` also admits non-starred items that match the local
  macro/crypto classifier.
- `JIN10_POLL_SECONDS=20` controls polling frequency.
- `JIN10_APP_ID` overrides the compatible Flash API application identifier.

The Flash API is used for the daemon because it needs no personal token. Jin10's
official MCP token can be added later for quotes, K-lines, articles, and the
economic calendar without changing the briefing pipeline.
