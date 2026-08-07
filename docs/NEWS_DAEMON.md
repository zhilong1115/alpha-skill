# Friday AI Market News Daemon

## What it does

1. Collects real-time market news from Alpaca/Benzinga, Jin10 (金十数据),
   CNBC, MarketWatch, Reuters, Yahoo Finance, CoinDesk, CoinTelegraph,
   The Block, Decrypt, and Finnhub.
2. Applies a cheap rule filter, de-duplicates syndicated headlines, and batches
   related items.
3. A no-model OpenClaw trigger reads a signed, read-only batch and wakes an
   isolated tool-free Friday analysis task only when the material batch changes.
   The task decides whether the batch is materially market
   moving and, if so, produces a concise Chinese impact brief.
4. Maps first- and second-order effects to stocks/ETFs, futures/commodities/FX,
   and crypto, including direction, horizon, confidence, and invalidation.
5. Delivers the brief to Telegram. It never places a trade.

## Safety and cost controls

- Trading interrupts are disabled unless `NEWS_ENABLE_TRADING_INTERRUPT=1`.
- Neutral, unattributed whale transfers are suppressed.
- AI analysis batches have a ten-minute cooldown and are capped at 16 per day
  by the scheduler trigger.
- Telegram delivery therefore cannot exceed 16 material briefings per day.
- A model may return `NO_REPLY`; nothing is sent in that case.
- External news is explicitly treated as untrusted content.

## Runtime

The macOS LaunchAgent `com.friday.market-news-daemon` keeps collection running
24/7, including weekends for crypto coverage. An OpenClaw automation named
`Friday AI Market News Briefing` checks the local queue every five minutes with
a zero-token trigger and starts analysis only when needed.

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
