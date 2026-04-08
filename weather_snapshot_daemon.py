"""
WEATHER leaderboard snapshot daemon (Idea 3 infrastructure).

Saves a WEATHER leaderboard snapshot to logs/leaderboard.db every
`interval_hours` hours. Seeds a snapshot immediately on startup, then loops.

This feeds leaderboard_analytics.py --velocity which detects fast rank-climbers
and surfaces what they are currently holding.

Run in background:
    python weather_snapshot_daemon.py &
    python weather_snapshot_daemon.py --interval-hours 4 &

Stop:
    pkill -f "weather_snapshot_daemon.py"

Check logs:
    tail -f /tmp/weather_daemon.log   # if redirected
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from leaderboard import fetch_leaderboard, save_snapshot_to_db

DEFAULT_DB = "logs/leaderboard.db"
DEFAULT_INTERVAL_HOURS = 6.0
DEFAULT_CATEGORY = "WEATHER"
DEFAULT_LIMIT = 50


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot helpers
# ──────────────────────────────────────────────────────────────────────────────

def _take_snapshot(
    db_path: str,
    category: str,
    time_period: str,
    order_by: str,
    limit: int,
) -> int:
    """Fetch one leaderboard page and persist as a snapshot. Returns row count."""
    now = datetime.now(timezone.utc).isoformat()
    print(
        f"[daemon] {now} | Fetching {category} leaderboard "
        f"(timePeriod={time_period}, orderBy={order_by}, limit={limit})"
    )
    rows = fetch_leaderboard(
        category=category,
        time_period=time_period,
        order_by=order_by,
        limit=limit,
    )
    snapshot_at = save_snapshot_to_db(
        rows=rows,
        db_path=db_path,
        category=category,
        time_period=time_period,
        order_by=order_by,
        limit=limit,
        offset=0,
    )
    print(f"[daemon] Saved {len(rows)} rows → {db_path} (snapshot_at={snapshot_at})")
    return len(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Daemon loop
# ──────────────────────────────────────────────────────────────────────────────

def run_daemon(
    db_path: str = DEFAULT_DB,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    category: str = DEFAULT_CATEGORY,
    order_by: str = "PNL",
    limit: int = DEFAULT_LIMIT,
) -> None:
    """
    Infinite loop daemon:
      - Seeds snapshot immediately on first run (before first sleep)
      - Takes ALL-time + MONTH snapshots each cycle (MONTH needed for
        rank-velocity since timePeriod changes the snapshot set)
      - Sleeps interval_hours between cycles
      - Stops cleanly on KeyboardInterrupt
    """
    interval_secs = int(interval_hours * 3600)
    print(
        f"[daemon] Weather snapshot daemon started\n"
        f"[daemon]   category    = {category}\n"
        f"[daemon]   interval    = {interval_hours}h ({interval_secs}s)\n"
        f"[daemon]   db_path     = {db_path}\n"
        f"[daemon]   limit       = {limit}\n"
        f"[daemon] Press Ctrl+C to stop.\n"
    )

    loop = 0
    try:
        while True:
            loop += 1
            print(f"[daemon] ── Cycle {loop} ──────────────────────────────────")
            try:
                # ALL-time snapshot — used by rank_velocity_alert
                _take_snapshot(db_path, category, "ALL", order_by, limit)
                # MONTH snapshot — useful for short-term momentum analysis
                _take_snapshot(db_path, category, "MONTH", order_by, limit)
            except Exception as e:
                print(
                    f"[daemon] Warning: snapshot failed (cycle {loop}): {e}",
                    file=sys.stderr,
                )

            next_at = datetime.now(timezone.utc)
            print(
                f"[daemon] Next cycle in {interval_hours}h "
                f"(approx {next_at.strftime('%H:%M UTC')} + {interval_hours}h) — sleeping...\n"
            )
            time.sleep(interval_secs)

    except KeyboardInterrupt:
        print(f"\n[daemon] Stopped by user after {loop} cycle(s).")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="WEATHER leaderboard snapshot daemon (feeds rank-velocity alerts)"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"SQLite path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=DEFAULT_INTERVAL_HOURS,
        help=f"Hours between snapshot cycles (default: {DEFAULT_INTERVAL_HOURS})",
    )
    parser.add_argument(
        "--category",
        default=DEFAULT_CATEGORY,
        help=f"Leaderboard category (default: {DEFAULT_CATEGORY})",
    )
    parser.add_argument(
        "--order-by",
        default="PNL",
        help="Order by PNL or VOL (default: PNL)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Rows per snapshot (default: {DEFAULT_LIMIT})",
    )
    args = parser.parse_args()

    run_daemon(
        db_path=args.db,
        interval_hours=args.interval_hours,
        category=args.category,
        order_by=args.order_by,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
