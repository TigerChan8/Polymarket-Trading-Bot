# Polymarket Trading Bot — Technical Roadmap
_Last updated: 2026-04-07_

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Module Deep-Dive & Line-by-Line Logic](#2-module-deep-dive--line-by-line-logic)
3. [Bugs & Logic Issues Found](#3-bugs--logic-issues-found)
4. [Backtest Results Analysis (logs/)](#4-backtest-results-analysis-logs)
5. [Next Steps & Roadmap](#5-next-steps--roadmap)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Polymarket Trading Bot                           │
│                                                                     │
│  ┌─────────────┐   ┌─────────────────┐   ┌──────────────────────┐  │
│  │   bot.py    │──▶│ strategy_engine │──▶│    data_logger.py    │  │
│  │ (arbitrage) │   │  (indicators +  │   │  (CSV + SQLite log)  │  │
│  └─────────────┘   │   rule engine)  │   └──────────────────────┘  │
│                    └─────────────────┘                             │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Weather Intelligence Layer                   │  │
│  │                                                               │  │
│  │  weather_markets.py          ← market universe builder        │  │
│  │      ↓                                                        │  │
│  │  weather_whale_monitor.py    ← Ideas 1 & 2 (whale + burst)   │  │
│  │  weather_accuracy.py         ← Idea 5  (accuracy scorer)      │  │
│  │  weather_snapshot_daemon.py  ← Idea 3  (rank-velocity seed)   │  │
│  │      ↓                                                        │  │
│  │  leaderboard_analytics.py   ← velocity + rank-change alerts  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  practice.ipynb  ← interactive exploration + 3-month backtest      │
└─────────────────────────────────────────────────────────────────────┘
```

### API Endpoints Used

| Endpoint | Base URL | Purpose |
|---|---|---|
| Gamma Events / Markets | `https://gamma-api.polymarket.com` | Market metadata, prices, outcomes |
| Data API | `https://data-api.polymarket.com` | Trades, positions, leaderboard |
| CLOB | `https://clob.polymarket.com` | Live orderbooks (bids/asks) |
| Relayer v2 | `https://relayer-v2.polymarket.com` | Order submission |

---

## 2. Module Deep-Dive & Line-by-Line Logic

### 2.1 `bot.py` — Arbitrage Core

**Constructor**
- Loads `MIN_PROFIT_MARGIN` (default 1%), `SCAN_INTERVAL` (1 sec), `MAX_MARKETS_TO_MONITOR` (100).
- Optionally initialises a `DataLogger` (CSV + SQLite) and `StrategyPipeline`.
- Attempts Web3 connection; falls back to data-logging mode if `PRIVATE_KEY` is absent.

**`get_active_markets()`**
- Hits `GET /markets?active=true&closed=false&limit=N` on the Gamma API.
- Normalises the response (could be a bare list or a dict with `.data`).
- Returns `[{id, question, slug}, ...]`.

**`get_market_prices(market_id)`**  
Key flow:
1. Fetches `GET /markets/{id}` from Gamma → parses `outcomePrices` + `clobTokenIds`.
2. Uses CLOB token IDs to call `get_market_orderbook()` → derives `best_ask` and `best_bid` per side.
3. Falls back to Gamma mid-prices if the CLOB is unavailable.
4. Returns `{yes_price, no_price, yes_ask, no_ask, yes_bid, no_bid, ...}`.

**`check_arbitrage(yes_price, no_price)`**
- Computes `total_cost = yes_price + no_price`.
- Signals an opportunity when `total_cost < 1.0 - min_profit_margin`.
- Returns `(True, profit)` or `(False, 0.0)`.

### 2.2 `strategy_engine.py` — Pluggable Indicators & Rules

| Class | Role | Key formula |
|---|---|---|
| `TotalAskCostIndicator` | Sum of best ask prices | `yes_ask + no_ask` |
| `EdgeIndicator` | Profit potential | `1.0 − (yes_ask + no_ask)` |
| `SpreadSumIndicator` | Liquidity proxy | `(yes_ask − yes_bid) + (no_ask − no_bid)` |
| `PureArbitrageRule` | Fire if edge ≥ min_edge | `edge ≥ 0.01` |
| `TightExecutionRule` | Fire if spreads are tight | `spread_sum ≤ 0.03` |

`StrategyPipeline.evaluate(snapshot)` runs all indicators → all rules → returns `{indicators, signals}`.

### 2.3 `weather_markets.py` — Market Universe Builder

**`WeatherMarketFetcher`**
- Paginates `GET /events?tag_slug=weather` (NOT `category=weather` — the category param behaves differently on the Gamma endpoint; `tag_slug=weather` is the correct filter).
- `_parse_market()` normalises each market to a flat dict: `conditionId`, `question`, `outcomes`, `outcomePrices`, `clobTokenIds`, `resolvedOutcome`, `volume`, `liquidityClob`, `tags`.
- `_determine_winner(outcomes, prices)` returns the outcome with `price ≥ 0.9` (resolution threshold). Returns `None` if no outcome crosses the threshold — handles pre-resolution and very-close markets.
- `fetch_active()` → only markets where `active=True AND closed=False`, sorted by volume.
- `fetch_closed()` → closed events, sorted by volume; `resolvedOutcome` populated where determinable.
- `get_active_condition_ids()` → set of conditionId strings for fast O(1) membership checks.

### 2.4 `weather_whale_monitor.py` — Whale + Consensus Burst (Ideas 1 & 2)

**`fetch_weather_leaderboard(limit)`**
- Calls `GET /v1/leaderboard?category=WEATHER&timePeriod=ALL&orderBy=PNL`.
- Returns ranked traders with wallet, PnL, volume.

**`poll_weather_wallets(wallets, weather_cids, min_notional)`**
- For each wallet → `GET /trades?user=...&takerOnly=true&limit=100`.
- Filters to weather conditionIds (or keyword fallback if fetcher failed).
- Keeps only trades where `price * size ≥ min_notional`.
- Enriches with conditionId, outcome, notional, tx hash.
- Rate-limited at 0.12 s/wallet to avoid 429s.

**`detect_consensus_burst(alerts, min_traders=3, window_minutes=60)`**
1. Groups alerts by `(conditionId, outcome)`.
2. Deduplicates by wallet (keeps highest-notional trade per wallet per group).
3. Sorts timestamps; for each anchor trade, counts how many timestamps fall within `anchor + window_secs`.
4. Fires a `CONSENSUS_BURST` signal if `≥ min_traders` unique wallets converge within the window.
5. Returns bursts sorted by `traderCount` descending.

**Main loop (`run_weather_whale_monitor`)**
- Refreshes WEATHER conditionId set every 5 loops.
- Each loop: fetch → filter → dedup by tx hash → burst detection → persist (SQLite + CSV) → optional Discord/Telegram notifications.
- `seen_hashes` set grows indefinitely in-process (not persisted between restarts).

### 2.5 `weather_accuracy.py` — Trader Accuracy Scorer (Idea 5)

**Pipeline:**
1. `fetch_closed()` → `build_resolved_market_map()` → `{conditionId: WINNER_UPPERCASE}`.
2. `fetch_top_weather_wallets()` → top N by PnL from WEATHER leaderboard.
3. For each wallet → `fetch_wallet_weather_trades()` → trades on resolved conditionIds only.
4. `score_wallet()`:
   - `win_rate = win_trades / total_trades`
   - `weighted_accuracy = win_notional / total_notional`
   - `confidence_warning = True` if `total_trades < 10`
5. Results sorted: non-low-confidence first, then by `weighted_accuracy` desc.

**DB schema:** `logs/weather_accuracy.db` → `trader_accuracy` table.

### 2.6 `weather_snapshot_daemon.py` — Rank-Velocity Seed (Idea 3)

- On startup: immediately takes one snapshot via `fetch_leaderboard()` + `save_snapshot_to_db()`.
- Main loop: saves both `ALL` and `MONTH` time-period snapshots every N hours (default 6h).
- Two snapshots required before `leaderboard_analytics.py --velocity` can produce results.

### 2.7 `leaderboard_analytics.py` — Rank Movers & Velocity

- Reads two consecutive snapshots from `logs/leaderboard.db`.
- `print_rank_movers()`: shows traders whose rank improved (delta > 0) between snapshots.
- `--velocity` flag: for each rank-climber, fetches their current positions + recent trades from the Data API to surface what they are holding.

### 2.8 `data_logger.py` — Price Data Logger

- Writes one row per market scan: `{timestamp, market_id, yes_price, no_price, total_cost, arbitrage_opportunity, potential_profit, ask/bid prices}`.
- SQLite table `price_data` with indexes on `(market_id, timestamp)` and `(arbitrage_opportunity, timestamp)`.
- `arbitrage_opportunity` column is `1/0` integer.

### 2.9 `practice.ipynb` — Interactive Exploration

| Cell | Purpose |
|---|---|
| 1 (setup) | `PolyClient` HTTP wrapper with retry/backoff |
| 2 (big-move detector) | Scans leaderboard wallets for large trades, alerts, persists |
| 3 (continuous monitor) | Loops whale-monitor with dedup across iterations |
| Diagnostic cell | API health check + 1-week scan |
| Cell 8c (active mkts) | Live active weather market browser |
| Cell 3b (leaderboard) | Top-20 WEATHER traders |
| Cell 45e (whale, 1 loop) | Single-loop whale + burst check |
| **Cell 19 (backtest)** | 3-month backtest: Part A (wallet-centric) + Part B (market-centric) |
| Cell 002f (accuracy) | WeatherAccuracyAnalyzer on top-20 |
| Cell f28d (summary) | Extended backtest summary |

---

## 3. Bugs & Logic Issues Found

### 🔴 Critical

#### BUG-1: `check_arbitrage` uses mid-prices, not ask prices
**File:** `bot.py`, `check_arbitrage(yes_price, no_price)`  
**Issue:** `yes_price` and `no_price` passed in are Gamma mid-prices. Mid-prices always sum to ~1.0 by market design. Only the **ask** prices (CLOB `best_ask`) matter for arbitrage — you have to pay the ask to enter a position.  
**Fix:** Change calls to pass `yes_ask` and `no_ask` instead of mid-prices.
```python
# Current (wrong)
has_arb, profit = self.check_arbitrage(prices['yes_price'], prices['no_price'])

# Correct
has_arb, profit = self.check_arbitrage(prices['yes_ask'], prices['no_ask'])
```

#### BUG-2: No transaction cost model
**File:** `bot.py`, `check_arbitrage()` and `practice.ipynb` backtest  
**Issue:** Polymarket charges a **2% taker fee** on CLOB orders. At average entry price 0.84, the effective cost per contract is `0.84 × 1.02 = 0.857`. This dramatically reduces edge.  
**Fix:** Subtract fees from the profit calculation. Minimum edge requirement should be raised to at least **5%** to cover: 2% taker fee on the Yes leg + 2% taker fee on the No leg + slippage buffer.

#### BUG-3: Backtest PnL sign error in uncertain bets (Part B)
**File:** `practice.ipynb`, PART B insight section  
**Issue:** "Uncertain bets (entry < 0.70)" show `win_rate=33.2%` vs `avg_entry=38.9%`, giving `edge=-0.057` (negative alpha). Yet the overall Part B PnL is reported as `-$2,226`. The uncertain bucket is _dragging_ the aggregate down, but the insight section does not flag it clearly.  
**Impact:** The strategy of copying uncertain-price trades from weather markets is unprofitable without filtering.

---

### 🟡 Medium

#### BUG-4: `seen_hashes` set is in-memory only
**File:** `weather_whale_monitor.py`, `run_weather_whale_monitor()`  
**Issue:** On every restart, `seen_hashes` resets → previously persisted alerts are re-alerted. If the daemon crashes and restarts it floods the DB with duplicates.  
**Fix:** Seed `seen_hashes` from the existing DB on startup:
```python
# On startup, load tx hashes already in DB
conn = sqlite3.connect(db_path)
rows = conn.execute("SELECT transaction_hash FROM whale_alerts WHERE transaction_hash != ''").fetchall()
seen_hashes = {r[0] for r in rows}
conn.close()
```

#### BUG-5: Consensus burst window does not roll across loops
**File:** `weather_whale_monitor.py`, `detect_consensus_burst()`  
**Issue:** Bursts are detected within the **current loop's `batch`** only. If wallet A trades in loop 1 and wallet B trades in loop 2, the consensus spanning two loops is missed.  
**Fix:** Pass in the accumulated `all_seen_alerts` (with timestamps) rather than just the current `batch` when calling `detect_consensus_burst`.

#### BUG-6: `takerOnly=true` misses maker-side whale positions
**File:** `weather_whale_monitor.py`, `_fetch_trades()`  
**Issue:** Large limit orders sitting in the orderbook (maker side) are not captured. A whale positioning themselves as a maker represents an equally strong signal.  
**Fix:** Use `takerOnly=false` and filter server-side by `side=BUY`.

#### BUG-7: `_determine_winner` threshold is hard-coded at 0.9 but not validated
**File:** `weather_markets.py`, `_determine_winner()`  
**Issue:** If Gamma updates prices lazily, a recently resolved market might still show prices of `[0.88, 0.12]` and return `None` (no winner) even though it has resolved. This means some valid resolved markets are excluded from the accuracy scorer's denominator.  
**Fix:** Lower threshold to `0.85` OR cross-check against `market.resolved` boolean field when available.

---

### 🟢 Minor / Design

#### BUG-8: `LOW_CONFIDENCE_THRESHOLD = 10` is statistically too low
**File:** `weather_accuracy.py`  
**Issue:** With 10 binary trades, even a coin flip (50%) can yield 7/10 = 70% win rate by chance (p ≈ 0.17). A minimum of **30 trades** would give ~5% false-positive rate.  
**Fix:** Raise threshold to 30.

#### BUG-9: Strategy pipeline is disabled by default
**File:** `config.py` → `ENABLE_STRATEGY_PIPELINE = false`  
**Issue:** The strategy engine is never exercised in production runs, making the `strategy_engine.py` dead code in the live bot.  
**Fix:** Enable the pipeline and wire its output to the trade decision or at least to the data logger for later analysis.

#### BUG-10: `analyze_data.py` queries `datetime('now', '-N hours')` which is UTC-only
**File:** `analyze_data.py`  
**Issue:** Bot timestamps are written in local time by `datetime.now()`. If the local machine is not in UTC, the time window filter is wrong.  
**Fix:** Use `datetime.utcnow()` or `datetime.now(timezone.utc)` when logging, and keep the SQLite query consistent.

---

## 4. Backtest Results Analysis (logs/)

### 4.1 `logs/price_data.csv` — 650 rows, 2026-03-22

- **No arbitrage opportunities detected** (`arbitrage_opportunity=0` for all rows).
- All total costs are exactly `1.0000` — confirms BUG-1: the bot is comparing mid-prices (which always sum to 1.0) rather than ask prices.
- Markets scanned include GTA VI release, Harvey Weinstein sentence, NHL Stanley Cup, FIFA World Cup — broad multi-category scan.
- **Conclusion:** The bot has been running in monitoring mode only; no real edge has been captured yet because mid-price comparison is broken.

### 4.2 `logs/alerts.csv` — 263 rows

- **All sports markets** (NHL, NBA, college basketball, soccer, esports).
- Top wallets detected: `0x03e8a544`, `0x2a2c53bd`, `0x492442ea`, `0x6ac5bb06`.
- Largest single trade: `0x2a2c53bd` — Pacers vs Spurs, $422k notional at $0.99 on Spurs.
- Largest uncertain bet: `0x03e8a544` — Blackhawks vs Golden Knights at $0.34, $54k notional.
- **No WEATHER market trades in alerts.csv** — the general (non-weather) whale monitor is capturing sports trades; the weather-specific module runs separately.

### 4.3 Notebook Backtest — Key Numbers

| Metric | Part A (wallet-centric) | Part B (market-centric) |
|---|---|---|
| Signals / trades | 1,406 | 2,571 |
| Win rate | **86.9%** | **91.6%** |
| Avg entry price | 0.846 | 0.910 |
| Implied edge | **+0.023** | **+0.007** |
| Total P&L (raw) | +$98,482 | **−$2,227** |
| Uncertain bets edge | **+0.043** | **−0.057** |

**Key observations:**

1. **Part A positive PnL is misleading.** The top-20 WEATHER traders are mostly making near-certain bets (avg entry 84.6%). Their 86.9% win rate barely beats the implied 84.6% probability. Edge of +2.3% = tiny alpha. Once 2% Polymarket taker fees are subtracted, the edge disappears entirely.

2. **Part B shows the market is fairly priced for most participants.** Aggregate win rate (91.6%) ≈ aggregate entry price (91.0%), so P&L is almost zero, confirming the market efficiently prices weather outcomes in aggregate.

3. **Uncertain bets (entry < 0.70) in Part A show +4.3% edge.** 282 bets, 43.3% win rate vs 39% implied. This is the most interesting finding: weather traders taking contrarian/uncertain positions on weather markets seem to have genuine alpha. **This bucket is where further investigation is warranted.**

4. **NO bets (market-centric) show +1.1% edge** — slight bias toward "No" being underpriced in weather markets. This could be because the market maker prices Yes higher to attract liquidity on popular weather events.

5. **Consensus signals (53 trades) show only 54.7% win rate at avg entry 0.47** — much more uncertain, $12.5k PnL. Consensus agreement at uncertain prices is a potentially profitable signal but the sample is tiny.

---

## 5. Next Steps & Roadmap

### Phase 1 — Critical Fixes (Do First)

| # | Task | File(s) | Effort |
|---|---|---|---|
| 1.1 | Fix `check_arbitrage` to use ask prices | `bot.py` | 30 min |
| 1.2 | Add 2% taker fee to profit model | `bot.py`, `strategy_engine.py` | 1 h |
| 1.3 | Seed `seen_hashes` from DB on restart | `weather_whale_monitor.py` | 1 h |
| 1.4 | Persist consensus burst across loops | `weather_whale_monitor.py` | 2 h |
| 1.5 | Fix `datetime.now()` → `datetime.utcnow()` in logger | `data_logger.py` | 15 min |

### Phase 2 — Backtest Improvements

| # | Task | Details | Effort |
|---|---|---|---|
| 2.1 | **Extend backtest to 12 months** | Increase `MONTHS_BACK=12`, `MKTCENTRIC_SAMPLE=200`. Larger sample reduces noise and confirms whether +4.3% edge on uncertain bets is real. | 4 h |
| 2.2 | Add transaction cost layer | Subtract 2% taker fee from every trade PnL in `_eval()` | 1 h |
| 2.3 | Add slippage model | Simulate +1% slippage above signal price | 2 h |
| 2.4 | Walk-forward backtest | Split data into rolling 3-month train / 1-month test windows | 1 day |
| 2.5 | Bootstrap confidence intervals | Run 1,000 shuffles of outcomes to get 95% CI on win rate | 3 h |
| 2.6 | Filter by uncertainty band | Only enter trades where entry price `0.30 < p < 0.70` | 1 h |
| 2.7 | Consensus-only backtest | Evaluate consensus signals (≥2 wallets) in isolation | 2 h |

### Phase 3 — Strategy Enhancements

| # | Task | Details | Effort |
|---|---|---|---|
| 3.1 | **Uncertainty filter alpha** | The +4.3% edge on entry < 0.70 warrants a standalone strategy: copy WEATHER whale buys only when market price is 30–70%. Backtest longer period first. | 3 h |
| 3.2 | Maker-side position monitoring | Switch `takerOnly=false` in whale fetch; separate maker vs taker P&L | 2 h |
| 3.3 | Cross-signal confirmation | Only enter when WHALE + CONSENSUS agree on same (cid, outcome) | 2 h |
| 3.4 | Accuracy score gating | Only follow wallets with `weighted_accuracy ≥ 0.55` AND `total_trades ≥ 30` | 2 h |
| 3.5 | Volume velocity signal | Add a VOLUME_SPIKE indicator: price unchanged but volume doubles in 1h → informed trading | 1 day |
| 3.6 | NO-bet bias strategy | Market-centric Part B shows NO bets have +1.1% edge. Build a strategy that bets NO on low-probability weather events when whales are also on NO side | 3 h |

### Phase 4 — Infrastructure

| # | Task | Details | Effort |
|---|---|---|---|
| 4.1 | Start snapshot daemon | `python3 weather_snapshot_daemon.py &` — needs at least 2 snapshots before velocity works | 5 min |
| 4.2 | Enable strategy pipeline | Set `ENABLE_STRATEGY_PIPELINE=true` in `.env`, log signals to DB | 1 h |
| 4.3 | Dashboard (Streamlit or Jupyter widgets) | Live chart of PnL by signal type, win rate over time | 1 day |
| 4.4 | Alert dedup cross-restart (DB-backed `seen_hashes`) | See BUG-4 fix | 1 h |
| 4.5 | Paper trading mode | Execute orders on testnet / track hypothetical P&L without real money | 1 week |
| 4.6 | Telegram / Discord integration | Enable notifications for consensus bursts ≥ 3 traders + $10k notional | 2 h |

### Phase 5 — Live Trading (after backtesting confirms edge)

| # | Task | Details |
|---|---|---|
| 5.1 | CLOB API key + `clob_setup.py` | Set up API key authentication for order placement |
| 5.2 | Position sizing (Kelly criterion) | `f* = (bp − q) / b` where b = payout-1, p = win_rate, q = 1-p |
| 5.3 | Max drawdown circuit breaker | Halt trading if daily P&L < -$500 |
| 5.4 | Portfolio-level risk limits | Max 10% of bankroll per single weather market |
| 5.5 | Real-time P&L tracking | Hook `data_logger.py` into a live dashboard |

---

## Suggested Immediate Next Run

```bash
# 1. Fix seen_hashes seeding, then start the weather monitor
python3 weather_whale_monitor.py --loops 0 --poll-seconds 60 --min-notional 1000 &

# 2. Start the snapshot daemon (for rank-velocity in 6+ hours)
python3 weather_snapshot_daemon.py --interval-hours 6 &

# 3. Re-run the backtest with longer window in practice.ipynb
#    Change: MONTHS_BACK=12, MKTCENTRIC_SAMPLE=200, MIN_NOTIONAL=10

# 4. Run the accuracy scorer
python3 weather_accuracy.py --top-n 50 --print-top 20 --out-csv logs/weather_accuracy.csv
```

---

## Quick Reference: Key Constants

| Constant | File | Default | Meaning |
|---|---|---|---|
| `MIN_PROFIT_MARGIN` | `config.py` | 0.01 | Min edge (1%) to flag arbitrage |
| `WEATHER_MIN_NOTIONAL` | `config.py` | $5,000 | Min trade size for whale alert |
| `WEATHER_CONSENSUS_N` | `config.py` | 3 | Wallets required for consensus burst |
| `WEATHER_CONSENSUS_WINDOW_MINUTES` | `config.py` | 60 | Window for burst detection |
| `MONTHS_BACK` | `practice.ipynb` | 3 | Backtest lookback |
| `MIN_NOTIONAL` | `practice.ipynb` | $10 | Minimum trade to backtest |
| `WHALE_NOTIONAL` | `practice.ipynb` | $100 | "Whale" label threshold |
| `CONSENSUS_TRADERS` | `practice.ipynb` | 2 | Minimum wallets for consensus signal |
| `TOP_N_WALLETS` | `practice.ipynb` | 20 | WEATHER leaderboard wallets to track |
| `MKTCENTRIC_SAMPLE` | `practice.ipynb` | 50 | Resolved markets sampled in Part B |
