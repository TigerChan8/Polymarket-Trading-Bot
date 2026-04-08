"""
Weather trader accuracy scorer (Idea 5).

Scores top WEATHER leaderboard traders based on their historical win/loss
record across resolved (closed) weather markets.

Win = trader bought the outcome that resolved to ~1.0 (price >= 0.9).

Metrics computed per trader:
  - win_rate           : wins / total_trades  (simple)
  - weighted_accuracy  : win_notional / total_notional  (size-weighted)
  - confidence_warning : True if total_trades < 10 (low statistical confidence)

DB: logs/weather_accuracy.db — table trader_accuracy

Usage:
    python weather_accuracy.py --top-n 50
    python weather_accuracy.py --top-n 50 --out-csv logs/weather_accuracy.csv
    python weather_accuracy.py --top-n 50 --print-top 20
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

from weather_markets import WeatherMarketFetcher

DATA_BASE = "https://data-api.polymarket.com"
WEATHER_ACCURACY_DB = "logs/weather_accuracy.db"
LOW_CONFIDENCE_THRESHOLD = 10  # trades below this get a confidence warning


# ──────────────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────────────

def _init_accuracy_db(db_path: str = WEATHER_ACCURACY_DB) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trader_accuracy (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            computed_at         TEXT,
            proxy_wallet        TEXT,
            user_name           TEXT,
            total_trades        INTEGER,
            win_trades          INTEGER,
            win_rate            REAL,
            total_notional      REAL,
            weighted_accuracy   REAL,
            markets_traded      INTEGER,
            confidence_warning  INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ta_wallet
        ON trader_accuracy(proxy_wallet, computed_at)
        """
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────────

def _get(url: str, params: Dict[str, Any], timeout: int = 20) -> Any:
    session = requests.Session()
    for attempt in range(4):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < 3:
                    time.sleep(0.8 * (2 ** attempt))
                    continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(0.8 * (2 ** attempt))


# ──────────────────────────────────────────────────────────────────────────────
# Analyzer
# ──────────────────────────────────────────────────────────────────────────────

class WeatherAccuracyAnalyzer:
    """
    Scores WEATHER leaderboard traders by their historical accuracy on
    resolved weather prediction markets.

    Pipeline:
      1. Fetch closed weather markets  (WeatherMarketFetcher.fetch_closed)
      2. Build {conditionId: winning_outcome} resolved map
      3. Fetch top WEATHER traders from leaderboard
      4. For each trader, fetch their trades on those conditionIds
      5. Classify each trade as win/loss, compute metrics
      6. Save to DB + optional CSV
    """

    def __init__(self, top_n: int = 50):
        self.top_n = top_n
        self.fetcher = WeatherMarketFetcher()

    # ── Step 1 + 2: closed market resolution map ─────────────────────────────

    def fetch_top_weather_wallets(self) -> List[Dict[str, Any]]:
        """Return top WEATHER traders (all-time by PnL) as list of {proxyWallet, userName}."""
        data = _get(
            f"{DATA_BASE}/v1/leaderboard",
            {
                "category": "WEATHER",
                "timePeriod": "ALL",
                "orderBy": "PNL",
                "limit": min(self.top_n, 50),
            },
        )
        return [
            {"proxyWallet": r.get("proxyWallet"), "userName": r.get("userName")}
            for r in (data if isinstance(data, list) else [])
            if r.get("proxyWallet")
        ]

    def build_resolved_market_map(
        self, closed_markets: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """
        Build {conditionId: winning_outcome_uppercase} map.
        Only includes markets with a clearly resolved outcome (resolvedOutcome set).
        """
        resolved: Dict[str, str] = {}
        for m in closed_markets:
            cid = m.get("conditionId")
            winner = m.get("resolvedOutcome")
            if cid and winner:
                resolved[cid] = winner.upper()
        return resolved

    # ── Step 3: fetch wallet trades filtered to weather conditionIds ──────────

    def fetch_wallet_weather_trades(
        self,
        wallet: str,
        condition_ids: Set[str],
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all trades for a wallet (both taker and maker), filtered to
        resolved weather conditionIds only. Paginates up to `limit` rows.
        """
        trades: List[Dict[str, Any]] = []
        per_page = 100
        for offset in range(0, limit, per_page):
            data = _get(
                f"{DATA_BASE}/trades",
                {
                    "user": wallet,
                    "takerOnly": "false",  # include all trade sides
                    "limit": per_page,
                    "offset": offset,
                },
            )
            if not isinstance(data, list) or not data:
                break
            weather_batch = [
                t for t in data
                if (t.get("market") or t.get("conditionId") or "") in condition_ids
            ]
            trades.extend(weather_batch)
            if len(data) < per_page:
                break
            time.sleep(0.1)
        return trades

    # ── Step 4: score a wallet ────────────────────────────────────────────────

    def score_wallet(
        self,
        wallet: str,
        trades: List[Dict[str, Any]],
        resolved_map: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Compute accuracy scores for a single wallet.

        win_rate          = win_trades / total_trades  (simple fraction)
        weighted_accuracy = sum(notional * is_win) / sum(notional)  (size-weighted)

        Both metrics range 0–1. Higher is better. Ignore markets with no trades.
        """
        trade_count = 0
        win_count = 0
        total_notional = 0.0
        win_notional = 0.0
        markets_set: Set[str] = set()

        for t in trades:
            cid = t.get("market") or t.get("conditionId") or ""
            if cid not in resolved_map:
                continue  # skip unresolved or unknown

            winner = resolved_map[cid]  # e.g. "YES"
            outcome = (t.get("outcome") or "").upper()
            notional = float(t.get("size") or 0) * float(t.get("price") or 0)

            trade_count += 1
            total_notional += notional
            markets_set.add(cid)

            is_win = outcome == winner
            if is_win:
                win_count += 1
                win_notional += notional

        win_rate = win_count / trade_count if trade_count > 0 else 0.0
        weighted_acc = win_notional / total_notional if total_notional > 0 else 0.0
        confidence_warning = trade_count < LOW_CONFIDENCE_THRESHOLD

        return {
            "proxyWallet": wallet,
            "total_trades": trade_count,
            "win_trades": win_count,
            "win_rate": round(win_rate, 4),
            "total_notional": round(total_notional, 2),
            "weighted_accuracy": round(weighted_acc, 4),
            "markets_traded": len(markets_set),
            "confidence_warning": confidence_warning,
        }

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def run(self) -> List[Dict[str, Any]]:
        """
        Execute the full accuracy scoring pipeline.
        Returns results sorted: high-confidence first, then by weighted_accuracy desc.
        """
        print("[accuracy] Fetching closed weather markets...")
        closed = self.fetcher.fetch_closed(max_pages=20)
        resolved_map = self.build_resolved_market_map(closed)
        condition_ids: Set[str] = set(resolved_map.keys())

        print(
            f"[accuracy] Closed markets fetched: {len(closed)} | "
            f"with resolvable outcome: {len(resolved_map)}"
        )
        if len(resolved_map) < 5:
            print(
                "[accuracy] ⚠️  Warning: fewer than 5 resolved markets found. "
                "Accuracy scores will have very low confidence. "
                "This improves as more weather markets close over time."
            )

        print("[accuracy] Fetching top WEATHER leaderboard traders...")
        wallets_info = self.fetch_top_weather_wallets()
        wallet_name_map = {r["proxyWallet"]: r.get("userName", "") for r in wallets_info}
        wallets = list(wallet_name_map.keys())
        print(f"[accuracy] Scoring {len(wallets)} traders...")

        results: List[Dict[str, Any]] = []
        computed_at = datetime.now(timezone.utc).isoformat()

        for idx, wallet in enumerate(wallets, 1):
            trades = self.fetch_wallet_weather_trades(wallet, condition_ids)
            score = self.score_wallet(wallet, trades, resolved_map)
            score["userName"] = wallet_name_map.get(wallet, "")
            score["computed_at"] = computed_at

            label = score["userName"] or (wallet[:14] + "...")
            warn = " ⚠️ " if score["confidence_warning"] else "   "
            print(
                f"  [{idx:>2}/{len(wallets)}]{warn}{label:<20} "
                f"trades={score['total_trades']:>4}  "
                f"win_rate={score['win_rate']:>6.1%}  "
                f"weighted={score['weighted_accuracy']:>6.1%}"
            )
            results.append(score)
            time.sleep(0.15)  # rate-limit pacing

        # Sort: confident traders first (not low-confidence), then weighted_accuracy desc
        results.sort(key=lambda r: (r["confidence_warning"], -r["weighted_accuracy"]))
        return results

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_to_db(
        self, results: List[Dict[str, Any]], db_path: str = WEATHER_ACCURACY_DB
    ) -> int:
        _init_accuracy_db(db_path)
        if not results:
            return 0
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO trader_accuracy(
                computed_at, proxy_wallet, user_name,
                total_trades, win_trades, win_rate,
                total_notional, weighted_accuracy, markets_traded, confidence_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.get("computed_at"),
                    r.get("proxyWallet"),
                    r.get("userName"),
                    r.get("total_trades"),
                    r.get("win_trades"),
                    r.get("win_rate"),
                    r.get("total_notional"),
                    r.get("weighted_accuracy"),
                    r.get("markets_traded"),
                    1 if r.get("confidence_warning") else 0,
                )
                for r in results
            ],
        )
        conn.commit()
        conn.close()
        return len(results)

    def save_to_csv(self, results: List[Dict[str, Any]], csv_path: str) -> int:
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "computed_at", "proxyWallet", "userName",
            "total_trades", "win_trades", "win_rate",
            "total_notional", "weighted_accuracy", "markets_traded", "confidence_warning",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in results:
                writer.writerow({k: r.get(k, "") for k in fields})
        return len(results)

    # ── Display ───────────────────────────────────────────────────────────────

    def print_table(self, results: List[Dict[str, Any]], top_n: int = 20) -> None:
        print("\n" + "=" * 96)
        print(
            f"{'#':<4} {'Username':<20} {'Wallet':<16} "
            f"{'Trades':>6} {'WinRate':>8} {'WghtAcc':>8} {'Markets':>8}  Note"
        )
        print("-" * 96)
        for i, r in enumerate(results[:top_n], 1):
            note = "⚠️  low confidence" if r.get("confidence_warning") else ""
            wallet_short = (r.get("proxyWallet") or "")[:14] + "..."
            print(
                f"{i:<4} {(r.get('userName') or ''):<20} {wallet_short:<16} "
                f"{r.get('total_trades', 0):>6}  "
                f"{r.get('win_rate', 0):>7.1%}  "
                f"{r.get('weighted_accuracy', 0):>7.1%}  "
                f"{r.get('markets_traded', 0):>7}  {note}"
            )
        print("=" * 96)
        print("⚠️  = fewer than 10 resolved trades (score may not be statistically reliable)")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score WEATHER traders by accuracy on resolved markets (Idea 5)"
    )
    parser.add_argument(
        "--top-n", type=int, default=50,
        help="Number of leaderboard traders to score (default: 50)"
    )
    parser.add_argument(
        "--db", default=WEATHER_ACCURACY_DB,
        help=f"SQLite output path (default: {WEATHER_ACCURACY_DB})"
    )
    parser.add_argument(
        "--out-csv", default=None,
        help="Optional CSV export path"
    )
    parser.add_argument(
        "--print-top", type=int, default=20,
        help="Rows to print in summary table (default: 20)"
    )
    args = parser.parse_args()

    analyzer = WeatherAccuracyAnalyzer(top_n=args.top_n)
    results = analyzer.run()
    analyzer.print_table(results, top_n=args.print_top)

    saved = analyzer.save_to_db(results, db_path=args.db)
    print(f"\n[accuracy] Saved {saved} rows → {args.db}")

    if args.out_csv:
        n = analyzer.save_to_csv(results, csv_path=args.out_csv)
        print(f"[accuracy] Saved CSV ({n} rows) → {args.out_csv}")


if __name__ == "__main__":
    main()
