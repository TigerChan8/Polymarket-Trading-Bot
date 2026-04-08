"""
Weather-specific whale monitor + consensus burst detector.

Idea 1: Filter WEATHER leaderboard wallets and fetch their trades on
        weather markets only (by conditionId).
Idea 2: Detect when 3+ independent top traders converge on the same
        outcome within a time window — a "consensus burst" signal.

DB: logs/weather_alerts.db
  - whale_alerts      (individual large weather-trade alerts)
  - consensus_bursts  (multi-trader convergence events)

Run:
    python weather_whale_monitor.py --loops 6 --poll-seconds 60
    python weather_whale_monitor.py --loops 0  # infinite until Ctrl+C
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from weather_markets import WeatherMarketFetcher

DATA_BASE = "https://data-api.polymarket.com"
WEATHER_ALERTS_DB = "logs/weather_alerts.db"
WEATHER_ALERTS_CSV = "logs/weather_alerts.csv"
WEATHER_BURSTS_CSV = "logs/weather_bursts.csv"


# ──────────────────────────────────────────────────────────────────────────────
# DB init
# ──────────────────────────────────────────────────────────────────────────────

def _init_weather_db(db_path: str = WEATHER_ALERTS_DB) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS whale_alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at      TEXT,
            proxy_wallet     TEXT,
            side             TEXT,
            title            TEXT,
            outcome          TEXT,
            condition_id     TEXT,
            price            REAL,
            size             REAL,
            notional         REAL,
            timestamp        INTEGER,
            transaction_hash TEXT,
            monitor_loop     INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wa_wallet_time
        ON whale_alerts(proxy_wallet, timestamp)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_wa_condition_outcome
        ON whale_alerts(condition_id, outcome)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS consensus_bursts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at     TEXT,
            condition_id    TEXT,
            title           TEXT,
            outcome         TEXT,
            trader_count    INTEGER,
            total_notional  REAL,
            wallets         TEXT,
            earliest_trade  INTEGER,
            latest_trade    INTEGER,
            monitor_loop    INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# HTTP helper
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
# Leaderboard helpers
# ──────────────────────────────────────────────────────────────────────────────

def fetch_weather_leaderboard(limit: int = 50) -> List[Dict[str, Any]]:
    """Return top WEATHER traders (all-time by PnL) with rank, wallet, username."""
    data = _get(
        f"{DATA_BASE}/v1/leaderboard",
        {
            "category": "WEATHER",
            "timePeriod": "ALL",
            "orderBy": "PNL",
            "limit": min(limit, 50),
        },
    )
    if not isinstance(data, list):
        return []
    return [
        {
            "rank": r.get("rank"),
            "proxyWallet": r.get("proxyWallet"),
            "userName": r.get("userName"),
            "pnl": float(r.get("pnl") or 0),
            "vol": float(r.get("vol") or 0),
        }
        for r in data
        if r.get("proxyWallet")
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Trade fetching + weather filtering
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_trades(wallet: str, limit: int = 100) -> List[Dict[str, Any]]:
    data = _get(
        f"{DATA_BASE}/trades",
        {"user": wallet, "takerOnly": "true", "limit": limit, "offset": 0},
    )
    return data if isinstance(data, list) else []


def _trade_notional(trade: Dict[str, Any]) -> float:
    return float(trade.get("size") or 0) * float(trade.get("price") or 0)


def _is_weather_title(title: str) -> bool:
    """
    Fallback keyword-based weather market detection used when the conditionId
    set is unavailable (e.g. API failure on startup).
    """
    keywords = [
        "hurricane", "storm", "temperature", "celsius", "fahrenheit",
        "rainfall", "tornado", "typhoon", "earthquake", "snowfall",
        "weather", "climate", "hottest", "coldest", "warmest", "gistemp",
        "named storm", "tropical", "atlantic", "pacific", "landfall",
    ]
    t = title.lower()
    return any(k in t for k in keywords)


def poll_weather_wallets(
    wallets: List[str],
    weather_condition_ids: Set[str],
    min_notional: float = 5_000,
    max_trades_each: int = 100,
) -> List[Dict[str, Any]]:
    """
    Fetch trades for each wallet, filter to weather markets only,
    return large-trade alerts (>= min_notional) with conditionId included.

    If weather_condition_ids is empty (fetcher failed), falls back to
    keyword matching on the trade title field.
    """
    alerts: List[Dict[str, Any]] = []
    for w in wallets:
        trades = _fetch_trades(w, limit=max_trades_each)
        for t in trades:
            cid = t.get("market") or t.get("conditionId") or ""
            in_weather = (
                cid in weather_condition_ids
                if weather_condition_ids
                else _is_weather_title(t.get("title", ""))
            )
            if not in_weather:
                continue
            notional = _trade_notional(t)
            if notional < min_notional:
                continue
            alerts.append(
                {
                    "proxyWallet": t.get("proxyWallet") or w,
                    "side": t.get("side"),
                    "title": t.get("title"),
                    "outcome": t.get("outcome"),
                    "conditionId": cid,
                    "price": t.get("price"),
                    "size": t.get("size"),
                    "notional": round(notional, 2),
                    "timestamp": t.get("timestamp"),
                    "transactionHash": t.get("transactionHash"),
                }
            )
        time.sleep(0.12)  # rate-limit pacing

    alerts.sort(key=lambda x: x["notional"], reverse=True)
    return alerts


# ──────────────────────────────────────────────────────────────────────────────
# Consensus burst detection (Idea 2)
# ──────────────────────────────────────────────────────────────────────────────

def detect_consensus_burst(
    alerts: List[Dict[str, Any]],
    min_traders: int = 3,
    window_minutes: int = 60,
) -> List[Dict[str, Any]]:
    """
    Group alerts by (conditionId, outcome). Fire a CONSENSUS_BURST signal when
    >= min_traders distinct wallets bought the same side within window_minutes.

    Returns list of burst dicts sorted by trader_count descending.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for a in alerts:
        cid = a.get("conditionId") or ""
        outcome = (a.get("outcome") or "").upper()
        if cid:
            groups[(cid, outcome)].append(a)

    bursts: List[Dict[str, Any]] = []
    window_secs = window_minutes * 60

    for (cid, outcome), group in groups.items():
        # Keep highest-notional trade per wallet (dedup by wallet)
        by_wallet: Dict[str, Dict[str, Any]] = {}
        for a in group:
            w = a.get("proxyWallet") or ""
            if w not in by_wallet or a["notional"] > by_wallet[w]["notional"]:
                by_wallet[w] = a

        unique = list(by_wallet.values())
        if len(unique) < min_traders:
            continue

        # Sliding-window check: do >= min_traders trades fall within window_secs?
        timestamps = sorted(
            [int(a.get("timestamp") or 0) for a in unique if a.get("timestamp")]
        )
        if not timestamps:
            continue

        found = False
        for i in range(len(timestamps)):
            window_group = [
                ts for ts in timestamps[i:]
                if ts - timestamps[i] <= window_secs
            ]
            if len(window_group) >= min_traders:
                found = True
                break

        if not found:
            continue

        total_notional = sum(a["notional"] for a in unique)
        title = unique[0].get("title", "")
        wallets = [a.get("proxyWallet") for a in unique]
        earliest = min(int(a.get("timestamp") or 0) for a in unique)
        latest = max(int(a.get("timestamp") or 0) for a in unique)

        bursts.append(
            {
                "type": "CONSENSUS_BURST",
                "detectedAt": datetime.now(timezone.utc).isoformat(),
                "conditionId": cid,
                "title": title,
                "outcome": outcome,
                "traderCount": len(unique),
                "totalNotional": round(total_notional, 2),
                "wallets": wallets,
                "earliestTrade": earliest,
                "latestTrade": latest,
            }
        )

    bursts.sort(key=lambda b: b["traderCount"], reverse=True)
    return bursts


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

def _save_whale_alerts(
    alerts: List[Dict[str, Any]],
    db_path: str,
    loop: int = 0,
) -> int:
    _init_weather_db(db_path)
    if not alerts:
        return 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO whale_alerts(
            detected_at, proxy_wallet, side, title, outcome, condition_id,
            price, size, notional, timestamp, transaction_hash, monitor_loop
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                a.get("detectedAt"),
                a.get("proxyWallet"),
                a.get("side"),
                a.get("title"),
                a.get("outcome"),
                a.get("conditionId"),
                float(a.get("price") or 0),
                float(a.get("size") or 0),
                float(a.get("notional") or 0),
                int(a.get("timestamp") or 0),
                a.get("transactionHash"),
                loop,
            )
            for a in alerts
        ],
    )
    conn.commit()
    conn.close()
    return len(alerts)


def _save_bursts(
    bursts: List[Dict[str, Any]],
    db_path: str,
    loop: int = 0,
) -> int:
    _init_weather_db(db_path)
    if not bursts:
        return 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO consensus_bursts(
            detected_at, condition_id, title, outcome, trader_count,
            total_notional, wallets, earliest_trade, latest_trade, monitor_loop
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                b.get("detectedAt"),
                b.get("conditionId"),
                b.get("title"),
                b.get("outcome"),
                b.get("traderCount"),
                float(b.get("totalNotional") or 0),
                json.dumps(b.get("wallets") or []),
                b.get("earliestTrade"),
                b.get("latestTrade"),
                loop,
            )
            for b in bursts
        ],
    )
    conn.commit()
    conn.close()
    return len(bursts)


def _save_whale_alerts_csv(alerts: List[Dict[str, Any]], csv_path: str) -> int:
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "detectedAt", "proxyWallet", "side", "title", "outcome", "conditionId",
        "price", "size", "notional", "timestamp", "transactionHash",
    ]
    file_exists = Path(csv_path).exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        for a in alerts:
            writer.writerow({k: a.get(k, "") for k in fields})
    return len(alerts)


# ──────────────────────────────────────────────────────────────────────────────
# Notifications
# ──────────────────────────────────────────────────────────────────────────────

def _format_whale_msg(alert: Dict[str, Any]) -> str:
    return (
        f"🌦️ Weather Whale\n"
        f"wallet: {alert.get('proxyWallet')}\n"
        f"market: {alert.get('title')}\n"
        f"outcome: {alert.get('outcome')} ({alert.get('side')})\n"
        f"notional: ${float(alert.get('notional', 0) or 0):,.0f}\n"
        f"price: {alert.get('price')} | size: {alert.get('size')}\n"
        f"tx: {alert.get('transactionHash')}"
    )


def _format_burst_msg(burst: Dict[str, Any]) -> str:
    wallets_preview = ", ".join((burst.get("wallets") or [])[:3])
    return (
        f"🌩️ CONSENSUS BURST\n"
        f"market: {burst.get('title')}\n"
        f"outcome: {burst.get('outcome')}\n"
        f"traders: {burst.get('traderCount')} | total: ${burst.get('totalNotional', 0):,.0f}\n"
        f"wallets: {wallets_preview}"
    )


def _send_discord(msg: str) -> bool:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return False
    try:
        r = requests.post(webhook, json={"content": msg}, timeout=15)
        return r.status_code in (200, 204)
    except Exception:
        return False


def _send_telegram(msg: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main monitor loop
# ──────────────────────────────────────────────────────────────────────────────

def run_weather_whale_monitor(
    loops: int = 6,
    poll_seconds: int = 60,
    leaderboard_limit: int = 20,
    min_notional: float = 5_000,
    consensus_min_traders: int = 3,
    consensus_window_minutes: int = 60,
    db_path: str = WEATHER_ALERTS_DB,
    csv_path: str = WEATHER_ALERTS_CSV,
    notify_discord: bool = False,
    notify_telegram: bool = False,
) -> Dict[str, Any]:
    """
    Weather intelligence monitor loop.

    Each iteration:
      1. Refresh top WEATHER leaderboard wallets
      2. Fetch & filter trades to weather conditionIds only
      3. Deduplicate new trades by tx hash
      4. Detect consensus bursts (Idea 2)
      5. Persist alerts + bursts to SQLite + CSV
      6. Optional Discord / Telegram notifications for bursts

    Set loops=0 for an infinite loop (Ctrl+C to stop).
    """
    print(
        f"[weather_monitor] Starting — loops={'∞' if loops == 0 else loops}, "
        f"poll_seconds={poll_seconds}, min_notional=${min_notional:,.0f}"
    )

    # Seed weather conditionIds at startup; refresh every 5 loops
    fetcher = WeatherMarketFetcher()
    weather_cids: Set[str] = set()
    try:
        weather_cids = fetcher.get_active_condition_ids()
        print(f"[weather_monitor] Active weather conditionIds loaded: {len(weather_cids)}")
    except Exception as e:
        print(f"[weather_monitor] Warning: could not load weather conditionIds: {e}")

    seen_hashes: Set[str] = set()
    totals: Dict[str, Any] = {
        "loops_run": 0,
        "alerts_detected": 0,
        "alerts_new": 0,
        "bursts_detected": 0,
        "saved_whale_sqlite": 0,
        "saved_burst_sqlite": 0,
        "saved_whale_csv": 0,
        "sent_discord": 0,
        "sent_telegram": 0,
    }

    infinite = loops == 0
    i = 0
    try:
        while infinite or i < loops:
            loop_num = i + 1

            # Refresh conditionIds every 5 loops
            if i > 0 and i % 5 == 0:
                try:
                    weather_cids = fetcher.get_active_condition_ids()
                except Exception:
                    pass

            # Refresh WEATHER leaderboard wallets each loop
            leaders = fetch_weather_leaderboard(limit=leaderboard_limit)
            wallets = [r["proxyWallet"] for r in leaders if r.get("proxyWallet")]

            # Fetch + filter trades
            batch = poll_weather_wallets(
                wallets=wallets,
                weather_condition_ids=weather_cids,
                min_notional=min_notional,
            )
            totals["alerts_detected"] += len(batch)

            # Deduplicate by tx hash
            new_alerts: List[Dict[str, Any]] = []
            for a in batch:
                txh = (a.get("transactionHash") or "").strip()
                key = txh if txh else json.dumps(a, sort_keys=True)
                if key in seen_hashes:
                    continue
                seen_hashes.add(key)
                a["detectedAt"] = datetime.now(timezone.utc).isoformat()
                a["monitorLoop"] = loop_num
                new_alerts.append(a)

            totals["alerts_new"] += len(new_alerts)

            # Consensus burst detection — run on full batch (not just new) for
            # correct multi-trader window detection even across loop boundaries
            bursts = detect_consensus_burst(
                batch,
                min_traders=consensus_min_traders,
                window_minutes=consensus_window_minutes,
            )
            totals["bursts_detected"] += len(bursts)

            # Persist
            if new_alerts:
                totals["saved_whale_sqlite"] += _save_whale_alerts(
                    new_alerts, db_path, loop=loop_num
                )
                totals["saved_whale_csv"] += _save_whale_alerts_csv(new_alerts, csv_path)

            if bursts:
                totals["saved_burst_sqlite"] += _save_bursts(
                    bursts, db_path, loop=loop_num
                )
                # Notify (bursts only — too noisy to notify every individual alert)
                for b in bursts[:5]:
                    msg = _format_burst_msg(b)
                    if notify_discord and _send_discord(msg):
                        totals["sent_discord"] += 1
                    if notify_telegram and _send_telegram(msg):
                        totals["sent_telegram"] += 1

            # Console summary
            print(
                f"loop {loop_num} | wallets={len(wallets)} | "
                f"detected={len(batch)} | new={len(new_alerts)} | "
                f"bursts={len(bursts)}"
            )
            for b in bursts:
                print(
                    f"  🌩️  BURST: {(b.get('title') or '')[:60]} | "
                    f"{b['outcome']} | {b['traderCount']} traders | "
                    f"${b['totalNotional']:,.0f}"
                )
            for a in new_alerts[:5]:
                print(
                    f"  🌦️  {(a.get('title') or '')[:55]} | "
                    f"{a.get('outcome')} | ${a['notional']:,.0f} | "
                    f"{a.get('proxyWallet', '')[:12]}..."
                )

            totals["loops_run"] += 1
            i += 1

            if infinite or i < loops:
                time.sleep(poll_seconds)

    except KeyboardInterrupt:
        print(f"\n[weather_monitor] Stopped by user.")

    print(f"[weather_monitor] Done. Summary: {totals}")
    return totals


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weather whale monitor + consensus burst detector (Ideas 1 & 2)"
    )
    parser.add_argument("--loops", type=int, default=6,
                        help="Number of poll loops (0 = infinite until Ctrl+C)")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--leaderboard-limit", type=int, default=20)
    parser.add_argument("--min-notional", type=float, default=5_000)
    parser.add_argument("--consensus-min-traders", type=int, default=3)
    parser.add_argument("--consensus-window-minutes", type=int, default=60)
    parser.add_argument("--db", default=WEATHER_ALERTS_DB)
    parser.add_argument("--csv", default=WEATHER_ALERTS_CSV)
    parser.add_argument("--notify-discord", action="store_true")
    parser.add_argument("--notify-telegram", action="store_true")
    args = parser.parse_args()

    run_weather_whale_monitor(
        loops=args.loops,
        poll_seconds=args.poll_seconds,
        leaderboard_limit=args.leaderboard_limit,
        min_notional=args.min_notional,
        consensus_min_traders=args.consensus_min_traders,
        consensus_window_minutes=args.consensus_window_minutes,
        db_path=args.db,
        csv_path=args.csv,
        notify_discord=args.notify_discord,
        notify_telegram=args.notify_telegram,
    )


if __name__ == "__main__":
    main()
