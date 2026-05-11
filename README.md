# Polymarket BTC 5-Min Bot

A trading bot for Polymarket's BTC 5-minute Up/Down markets. Built on top of the original "impossible bonds" scaffold — Telegram, dashboard, database, circuit breaker, and risk plumbing all preserved.

## Strategy

```
t=0s ────── t=90s ──────────── t=210s ────── t=300s
[opens]     [3:30 rem]         [1:30 rem]    [expires]
   ↑            ↑                  ↑
   ENTRY        last entry         FORCE CLOSE
   window       (after this:       at current price
   (0 → 90s)    ignore market)     (no holds past)
```

### Rules

- **Entry:** buy YES or NO when its price is at or below 20¢, anytime in the first 1:30 of the market
- **Profit exit:** if the bought side hits 30¢ at any point before t=210s → sell immediately
- **Force close:** at t=210s (1:30 remaining), close at the current price no matter what — could be a small win, small loss, or full stake loss
- **No hold to expiry.** Every position closes by 1:30 remaining
- **One position globally.** While a position is open, no new trades, ever, on any market
- **No re-entry.** Once a market has been traded, no bot enters it again for at least an hour
- **$2 per trade** in paper mode

### Three bots

- `btc-yes-only` — only buys YES when YES drops to ≤20¢
- `btc-no-only` — only buys NO when NO drops to ≤20¢
- `btc-either-side` — takes whichever side is cheap first

The first eligible bot wins each opportunity. With the global lock, in practice this means roughly: whichever bot's preference matches gets the trade.

## What's reused from the original

- Telegram notifier (`notifier.py`) — startup, trade placed, trade resolved, daily summary, circuit-breaker alerts. Resolved trades now show the *trigger* (TARGET_HIT / FORCE_CLOSE / STOP_LOSS).
- Dashboard (`dashboard/`)
- Database layer (`db.py`)
- Circuit breaker (`risk/circuit_breaker.py`)
- BTC crash monitor (auto-stops bots if BTC spot drops 7%)
- GitHub backup/restore
- Reconciler for resolved trades

## What changed

| File | Change |
|---|---|
| `constants.py` | New windows: `ENTRY_WINDOW_END_SEC=90`, `FORCE_CLOSE_SEC=210`. Trade size $2. |
| `scanner.py` | Finds BTC 5-min Up/Down markets where a side is ≤20¢ inside the first 90s. |
| `exit_monitor.py` | Triggers: profit-take (≥30¢), optional stop loss, force-close at 210s, expiry safety net. |
| `main.py` | `BTC5MinBot` with side preferences. Global one-position cap. |
| `executor.py` | Bug fix — uses actual fill price from broker. |
| `execution/polymarket_client.py` | Bug fix — paper mode uses the correct side's price (was always YES). |
| `notifier.py` | Startup msg + trade-resolved now shows the trigger. |
| `simulate.py` | New — 7 end-to-end scenarios with mocked Polymarket data. |

## Setup

```bash
unzip polymarket-bot-btc5min.zip
cd polymarket-bot-btc5min
pip install -r requirements.txt

# Configure
export TRADING_MODE=paper                  # paper | live
export DATA_DIR=./data
export TELEGRAM_BOT_TOKEN=...              # optional
export TELEGRAM_CHAT_ID=...                # optional
export POLYMARKET_PRIVATE_KEY=...          # only for live mode

# Verify everything works without touching real markets
python simulate.py                         # should end with 🎉 ALL SCENARIOS PASSED

# Then run paper for real
python main.py
```

## Simulation scenarios

`simulate.py` mocks the Polymarket API and runs:

1. **Profit-take** — Buy NO @ 18¢, price hits 32¢ → +$1.56 (78% ROI)
2. **Past entry deadline** — at t=120s, scanner ignores 15¢ side
3. **Force-close (small win)** — Buy YES @ 18¢, still 25¢ at t=210s → +$0.78
4. **Force-close (loss)** — Buy YES @ 18¢, tanks to 5¢ at t=215s → -$1.44
5. **Global lock** — Bot 1 holds a position, all bots blocked from new entries
6. **No re-entry** — Bot exits a market, all bots blocked from re-entering
7. **Stop loss disabled** — Price tanks, bot holds (no panic-sell)

All 7 must pass before live trading.

## Tuning knobs (`constants.py`)

| Constant | Default | Purpose |
|---|---|---|
| `ENTRY_PRICE_MAX` | 0.20 | Buy threshold |
| `EXIT_PRICE_TARGET` | 0.30 | Profit-take threshold |
| `EXIT_STOP_LOSS` | 0.0 | Loss exit (0 = disabled) |
| `ENTRY_WINDOW_END_SEC` | 90 | Last second to enter (= 3:30 remaining) |
| `FORCE_CLOSE_SEC` | 210 | Force close (= 1:30 remaining) |
| `WINDOW_DURATION_SEC` | 300 | Total market lifetime |
| `PAPER_TRADE_SIZE_USD` | 2.0 | Stake per trade |
| `PRICE_POLL_INTERVAL_SEC` | 1 | How often the loop runs |

## Logging & Notifications

### What goes to stdout (and to `data/logs/bot.log`)

```
2026-05-11 01:10:44 INFO ⚡ POLYMARKET BTC 5-MIN BOT STARTING
2026-05-11 01:10:44 INFO    Mode: PAPER
2026-05-11 01:10:44 INFO    Entry window: 0 → 90s (3:30 remaining)
2026-05-11 01:10:44 INFO    Force close:  t = 210s (1:30 remaining)
...
2026-05-11 01:11:00 INFO 📡 STATUS  open_positions=0  balance=$10000.00  recent_markets=3
2026-05-11 01:11:00 INFO       [0xabc1234567] yes_min=0.825 no_min=0.175 cheapest=0.175 ticks=42 → ⏸ another_position_open
2026-05-11 01:11:00 INFO       [0xdef9876543] yes_min=0.510 no_min=0.490 cheapest=0.490 ticks=38 → ⏸ no_side_under_20c
2026-05-11 01:11:00 INFO       [0x111aaaa222] yes_min=0.180 no_min=0.820 cheapest=0.180 ticks=12 → ✓ entered

2026-05-11 01:12:18 INFO ✅ [btc-yes-only] BUY YES $2.00 @ 0.180 → Bitcoin Up or Down — May 11, 1:10 AM-1:15 AM ET
2026-05-11 01:14:36 INFO ✅ EXIT [TARGET_HIT] YES entry=0.180 exit=0.300 P&L=$+1.33 — YES hit 0.300 at t=156s
```

A status pulse fires every ~30 seconds with the most recent markets, their cheapest side, and whether we entered or why we skipped.

The log file rotates at 10 MB, keeping 5 files (50 MB max), at `data/logs/bot.log`.

### What goes to Telegram

| When | What |
|---|---|
| **Bot start** | One-time startup banner with strategy parameters |
| **Trade placed** | `🟢 BUY NO $2 @ 0.180 on Bitcoin 1:10–1:15 AM ET` |
| **Trade resolved** | `✅ WON +$1.56 — Trigger: Profit target (≥30¢)` |
| **Every market closes** | Per-market summary (silent) — entered or not, with reason |
| **Every hour** | Hourly digest: markets observed, traded, win rate, missed entries, skip reasons |
| **Force close** | `❌ LOST -$1.40 — Trigger: Force-close at 1:30 remaining` |
| **Circuit breaker / BTC crash** | High-priority alerts (loud) |

Per-market summaries are sent **silent** so they don't notify you 288 times a day, but you can scroll through Telegram and see exactly what happened on every window.

### Example per-market summary (NO TRADE)

```
⏸️ MARKET CLOSED — NO TRADE
━━━━━━━━━━━━━━━━━━━━━━
📋 Bitcoin Up or Down — May 11, 1:10 AM-1:15 AM ET

📈 YES min: 0.510 @ t=185s
📉 NO  min: 0.480 @ t=200s
🔢 Ticks observed: 280

❓ Why no entry: No side reached ≤20¢
💡 Cheapest side: 0.480 (✗ never ≤0.20)
━━━━━━━━━━━━━━━━━━━━━━
```

### Example hourly digest

```
📊 HOURLY DIGEST
━━━━━━━━━━━━━━━━━━━━━━
🪟 Markets observed: 12
💎 Markets with side ≤20¢: 4
📈 Trades taken: 3
   ✅ Wins:   2
   ❌ Losses: 1
💰 P&L this hour: $+1.86
🎯 Win rate: 66.7%
⚠️ Missed entries: 1 (25%)

🔍 Skip reasons:
   • no side ≤20¢: 8
   • global lock: 1
━━━━━━━━━━━━━━━━━━━━━━
```

## Going live — DO NOT skip this

Paper mode works end to end. **Live mode is incomplete:**

1. **Order signing.** Polymarket requires EIP-712 signed orders. The existing `_live_order` posts unsigned JSON to `/order`. Use `py_clob_client` from Polymarket's SDK before going live.
2. **Live sell.** `exit_monitor._exit_position` currently logs `"⚠️ LIVE SELL not yet implemented"` instead of placing a real sell order in live mode.

Run paper for at least a week. Verify the trade log looks plausible against actual market behavior. Only then wire up live order placement.

## Honest reality check

- 5-min markets are noisy. The 20¢→30¢ swing is a real market move, not free money.
- Polymarket fees + slippage will eat ~2-5¢ off every trade.
- The force-close at 1:30 remaining means you're often closing at a loss when 30¢ doesn't hit. That's the cost of *not* holding to expiry.
- 99 of 100 strategies die when paper-tested honestly. This one might too. Test before scaling.
