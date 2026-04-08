"""
Leaderboard snapshot analytics.

Reads snapshots saved by leaderboard.py --save-db and prints:
- latest snapshot summary
- rank movers vs previous snapshot
- optional single-user history
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from typing import Dict, Any, List, Optional, Tuple

import requests

DATA_BASE = "https://data-api.polymarket.com"


def _rank_int(value: Any) -> int:
    try:
        return int(str(value))
    except Exception:
        return 10**9


def _get_two_latest_snapshot_times(
    conn: sqlite3.Connection,
    category: str,
    time_period: str,
    order_by: str,
) -> List[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT snapshot_at
        FROM leaderboard_snapshots
        WHERE category = ? AND time_period = ? AND order_by = ?
        ORDER BY snapshot_at DESC
        LIMIT 2
        """,
        (category, time_period, order_by),
    )
    return [r[0] for r in cur.fetchall()]


def _load_snapshot_rows(
    conn: sqlite3.Connection,
    snapshot_at: str,
    category: str,
    time_period: str,
    order_by: str,
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rank, proxy_wallet, user_name, vol, pnl, verified_badge
        FROM leaderboard_snapshots
        WHERE snapshot_at = ? AND category = ? AND time_period = ? AND order_by = ?
        """,
        (snapshot_at, category, time_period, order_by),
    )
    rows = []
    for rank, proxy_wallet, user_name, vol, pnl, verified_badge in cur.fetchall():
        rows.append(
            {
                "rank": rank,
                "proxy_wallet": proxy_wallet,
                "user_name": user_name,
                "vol": float(vol or 0.0),
                "pnl": float(pnl or 0.0),
                "verified_badge": bool(verified_badge),
            }
        )
    return rows


def _index_by_wallet(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(r["proxy_wallet"]).lower(): r for r in rows if r.get("proxy_wallet")}


def print_snapshot_summary(rows: List[Dict[str, Any]], snapshot_at: str) -> None:
    print("=" * 72)
    print(f"Snapshot: {snapshot_at}")
    print(f"Rows: {len(rows)}")
    print("Top 10")
    print("-" * 72)

    rows_sorted = sorted(rows, key=lambda r: _rank_int(r.get("rank")))[:10]
    for r in rows_sorted:
        print(
            f"#{r.get('rank')} | {r.get('user_name')} | {r.get('proxy_wallet')} | "
            f"pnl={r.get('pnl', 0.0):.2f} | vol={r.get('vol', 0.0):.2f}"
        )


def print_rank_movers(
    latest_rows: List[Dict[str, Any]],
    previous_rows: List[Dict[str, Any]],
    top_n: int = 10,
) -> None:
    latest_map = _index_by_wallet(latest_rows)
    prev_map = _index_by_wallet(previous_rows)

    movers: List[Tuple[int, Dict[str, Any], int]] = []
    for wallet, latest in latest_map.items():
        if wallet not in prev_map:
            continue
        prev = prev_map[wallet]
        delta = _rank_int(prev.get("rank")) - _rank_int(latest.get("rank"))
        if delta > 0:
            movers.append((delta, latest, _rank_int(prev.get("rank"))))

    movers.sort(key=lambda x: x[0], reverse=True)

    print("\nRank Improvers (latest vs previous)")
    print("-" * 72)
    if not movers:
        print("No positive rank movers in this comparison.")
        return

    for delta, latest, prev_rank in movers[:top_n]:
        print(
            f"+{delta:>3} | {latest.get('user_name')} | "
            f"{latest.get('proxy_wallet')} | {prev_rank} -> {latest.get('rank')} | "
            f"pnl={latest.get('pnl', 0.0):.2f}"
        )


def print_user_history(
    conn: sqlite3.Connection,
    category: str,
    time_period: str,
    order_by: str,
    user: Optional[str] = None,
    user_name: Optional[str] = None,
    limit: int = 20,
) -> None:
    if not user and not user_name:
        return

    where = ["category = ?", "time_period = ?", "order_by = ?"]
    params: List[Any] = [category, time_period, order_by]

    if user:
        where.append("lower(proxy_wallet) = lower(?)")
        params.append(user)
    if user_name:
        where.append("user_name = ?")
        params.append(user_name)

    params.append(limit)

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT snapshot_at, rank, proxy_wallet, user_name, pnl, vol
        FROM leaderboard_snapshots
        WHERE {' AND '.join(where)}
        ORDER BY snapshot_at DESC
        LIMIT ?
        """,
        params,
    )
    rows = cur.fetchall()

    print("\nUser History")
    print("-" * 72)
    if not rows:
        print("No rows for requested user filter.")
        return

    for snapshot_at, rank, proxy_wallet, user_name_v, pnl, vol in rows:
        print(
            f"{snapshot_at} | #{rank} | {user_name_v} | {proxy_wallet} | "
            f"pnl={float(pnl or 0.0):.2f} | vol={float(vol or 0.0):.2f}"
        )


def rank_velocity_alert(
    conn: sqlite3.Connection,
    category: str = "WEATHER",
    time_period: str = "ALL",
    order_by: str = "PNL",
    min_jump: int = 10,
    top_n: int = 5,
    fetch_positions: bool = True,
) -> List[Dict[str, Any]]:
    """
    Idea 3: Rank velocity alert.

    Compares the two most recent snapshots for the given filter, surfaces
    traders who jumped >= min_jump rank positions. For each fast-climber,
    optionally fetches their current open positions and recent trades from
    the Data API to surface what they are holding right now.

    Returns a list of velocity alert dicts.
    """
    times = _get_two_latest_snapshot_times(conn, category, time_period, order_by)
    if len(times) < 2:
        print(
            "[velocity] Need at least 2 snapshots. "
            "Run weather_snapshot_daemon.py to accumulate snapshots."
        )
        return []

    latest_rows = _load_snapshot_rows(conn, times[0], category, time_period, order_by)
    prev_rows = _load_snapshot_rows(conn, times[1], category, time_period, order_by)

    latest_map = _index_by_wallet(latest_rows)
    prev_map = _index_by_wallet(prev_rows)

    movers: List[Dict[str, Any]] = []
    for wallet, latest in latest_map.items():
        if wallet not in prev_map:
            continue
        prev = prev_map[wallet]
        delta = _rank_int(prev.get("rank")) - _rank_int(latest.get("rank"))
        if delta >= min_jump:
            movers.append(
                {
                    "proxyWallet": latest.get("proxy_wallet"),
                    "userName": latest.get("user_name"),
                    "rankNow": _rank_int(latest.get("rank")),
                    "rankPrev": _rank_int(prev.get("rank")),
                    "rankDelta": delta,
                    "pnlNow": latest.get("pnl", 0.0),
                    "pnlPrev": prev.get("pnl", 0.0),
                    "snapshotLatest": times[0],
                    "snapshotPrev": times[1],
                }
            )

    movers.sort(key=lambda m: m["rankDelta"], reverse=True)
    movers = movers[:top_n]

    if not movers:
        print(f"[velocity] No traders jumped >= {min_jump} ranks between the two latest snapshots.")
        return []

    print("\n" + "=" * 72)
    print(f"Rank Velocity Alert | category={category} | timePeriod={time_period}")
    print(f"Comparing: {times[1]} → {times[0]}")
    print("-" * 72)
    for m in movers:
        print(
            f"  +{m['rankDelta']:>3} | #{m['rankPrev']} → #{m['rankNow']} | "
            f"{m['userName']} | {m['proxyWallet']} | "
            f"pnl={m['pnlNow']:.2f} (was {m['pnlPrev']:.2f})"
        )

    if not fetch_positions:
        return movers

    # For each fast-climber, fetch their current open positions and recent trades
    print("\nFetching current positions for fast-climbers...")
    for m in movers:
        wallet = m.get("proxyWallet") or ""
        if not wallet:
            continue
        print(f"\n  ── {m['userName'] or wallet[:16]}... (#{m['rankNow']}, +{m['rankDelta']}) ──")

        # Positions
        try:
            pos_data = requests.get(
                f"{DATA_BASE}/positions",
                params={
                    "user": wallet,
                    "limit": 20,
                    "sortBy": "CURRENT",
                    "sortDirection": "DESC",
                    "sizeThreshold": 1,
                },
                timeout=15,
            ).json()
            positions = pos_data if isinstance(pos_data, list) else []
            m["currentPositions"] = [
                {
                    "title": p.get("title"),
                    "outcome": p.get("outcome"),
                    "currentValue": p.get("currentValue"),
                    "cashPnl": p.get("cashPnl"),
                    "curPrice": p.get("curPrice"),
                }
                for p in positions[:10]
            ]
            for p in m["currentPositions"][:5]:
                print(
                    f"    pos: {str(p.get('title') or '')[:55]:<55} | "
                    f"{p.get('outcome')} | "
                    f"val=${float(p.get('currentValue') or 0):,.0f} | "
                    f"pnl=${float(p.get('cashPnl') or 0):,.0f}"
                )
        except Exception as e:
            print(f"    [positions fetch error: {e}]")
            m["currentPositions"] = []

        # Recent trades
        try:
            trades_data = requests.get(
                f"{DATA_BASE}/trades",
                params={"user": wallet, "takerOnly": "true", "limit": 10, "offset": 0},
                timeout=15,
            ).json()
            trades = trades_data if isinstance(trades_data, list) else []
            m["recentTrades"] = [
                {
                    "title": t.get("title"),
                    "outcome": t.get("outcome"),
                    "side": t.get("side"),
                    "notional": round(
                        float(t.get("size") or 0) * float(t.get("price") or 0), 2
                    ),
                    "timestamp": t.get("timestamp"),
                }
                for t in trades[:5]
            ]
            for t in m["recentTrades"]:
                print(
                    f"    trade: {str(t.get('title') or '')[:50]:<50} | "
                    f"{t.get('outcome')} {t.get('side')} | "
                    f"${t.get('notional', 0):,.0f}"
                )
        except Exception as e:
            print(f"    [trades fetch error: {e}]")
            m["recentTrades"] = []

        time.sleep(0.2)

    return movers


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze saved leaderboard snapshots")
    parser.add_argument("--db", default="logs/leaderboard.db", help="sqlite path")
    parser.add_argument("--category", default="OVERALL")
    parser.add_argument("--time-period", default="DAY")
    parser.add_argument("--order-by", default="PNL")
    parser.add_argument("--movers", type=int, default=10, help="top rank improvers to print")
    parser.add_argument("--user", default=None, help="wallet filter")
    parser.add_argument("--user-name", default=None, help="username filter")
    parser.add_argument("--history-limit", type=int, default=20)
    parser.add_argument(
        "--velocity",
        action="store_true",
        help="Run rank velocity alert (requires >= 2 snapshots from weather_snapshot_daemon.py)",
    )
    parser.add_argument(
        "--min-jump",
        type=int,
        default=10,
        help="Minimum rank jump to qualify as a velocity alert (default: 10)",
    )
    parser.add_argument(
        "--no-fetch-positions",
        action="store_true",
        help="Skip fetching live positions/trades for velocity climbers",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    times = _get_two_latest_snapshot_times(
        conn,
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
    )
    if not times:
        print("No snapshots found for selected filter. Run leaderboard.py --save-db first.")
        conn.close()
        return

    latest_time = times[0]
    latest_rows = _load_snapshot_rows(
        conn,
        snapshot_at=latest_time,
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
    )

    print_snapshot_summary(latest_rows, latest_time)

    if len(times) > 1:
        prev_rows = _load_snapshot_rows(
            conn,
            snapshot_at=times[1],
            category=args.category,
            time_period=args.time_period,
            order_by=args.order_by,
        )
        print_rank_movers(latest_rows, prev_rows, top_n=args.movers)
    else:
        print("\nOnly one snapshot available; rank-mover comparison needs at least two snapshots.")

    print_user_history(
        conn,
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
        user=args.user,
        user_name=args.user_name,
        limit=args.history_limit,
    )

    if args.velocity:
        rank_velocity_alert(
            conn,
            category=args.category,
            time_period=args.time_period,
            order_by=args.order_by,
            min_jump=args.min_jump,
            fetch_positions=not args.no_fetch_positions,
        )

    conn.close()


if __name__ == "__main__":
    main()
