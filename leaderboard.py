"""
Polymarket trader leaderboard fetch utility.

Uses:
GET https://data-api.polymarket.com/v1/leaderboard
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://data-api.polymarket.com/v1/leaderboard"
VALID_CATEGORY = {
    "OVERALL",
    "POLITICS",
    "SPORTS",
    "CRYPTO",
    "CULTURE",
    "MENTIONS",
    "WEATHER",
    "ECONOMICS",
    "TECH",
    "FINANCE",
}
VALID_TIME_PERIOD = {"DAY", "WEEK", "MONTH", "ALL"}
VALID_ORDER_BY = {"PNL", "VOL"}


def fetch_leaderboard(
    category: str = "OVERALL",
    time_period: str = "DAY",
    order_by: str = "PNL",
    limit: int = 25,
    offset: int = 0,
    user: Optional[str] = None,
    user_name: Optional[str] = None,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "category": category,
        "timePeriod": time_period,
        "orderBy": order_by,
        "limit": limit,
        "offset": offset,
    }
    if user:
        params["user"] = user
    if user_name:
        params["userName"] = user_name

    response = requests.get(BASE_URL, params=params, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected API response: expected a list")
    return data


def print_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("No rows returned.")
        return

    headers = ["rank", "userName", "proxyWallet", "pnl", "vol", "verifiedBadge"]
    rendered_rows = []
    for r in rows:
        rendered_rows.append(
            [
                str(r.get("rank", "")),
                str(r.get("userName", "")),
                str(r.get("proxyWallet", "")),
                f"{float(r.get('pnl', 0.0)):.2f}",
                f"{float(r.get('vol', 0.0)):.2f}",
                str(r.get("verifiedBadge", False)),
            ]
        )

    widths = [len(h) for h in headers]
    for row in rendered_rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def fmt(row_values: List[str]) -> str:
        return " | ".join(value.ljust(widths[i]) for i, value in enumerate(row_values))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rendered_rows:
        print(fmt(row))


def export_csv(rows: List[Dict[str, Any]], out_path: str) -> None:
    if not rows:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["rank", "proxyWallet", "userName", "vol", "pnl", "profileImage", "xUsername", "verifiedBadge"])
        return

    fields = ["rank", "proxyWallet", "userName", "vol", "pnl", "profileImage", "xUsername", "verifiedBadge"]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def export_json(rows: List[Dict[str, Any]], out_path: str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def save_snapshot_to_db(
    rows: List[Dict[str, Any]],
    db_path: str,
    category: str,
    time_period: str,
    order_by: str,
    limit: int,
    offset: int,
) -> str:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    snapshot_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            category TEXT NOT NULL,
            time_period TEXT NOT NULL,
            order_by TEXT NOT NULL,
            limit_value INTEGER NOT NULL,
            offset_value INTEGER NOT NULL,
            rank TEXT,
            proxy_wallet TEXT,
            user_name TEXT,
            vol REAL,
            pnl REAL,
            profile_image TEXT,
            x_username TEXT,
            verified_badge INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_leaderboard_snapshot_filter
        ON leaderboard_snapshots(category, time_period, order_by, snapshot_at)
        """
    )

    rows_to_insert = [
        (
            snapshot_at,
            category,
            time_period,
            order_by,
            limit,
            offset,
            str(row.get("rank", "")),
            str(row.get("proxyWallet", "")),
            str(row.get("userName", "")),
            float(row.get("vol", 0.0) or 0.0),
            float(row.get("pnl", 0.0) or 0.0),
            str(row.get("profileImage", "")),
            str(row.get("xUsername", "")),
            1 if bool(row.get("verifiedBadge", False)) else 0,
        )
        for row in rows
    ]

    if rows_to_insert:
        cursor.executemany(
            """
            INSERT INTO leaderboard_snapshots (
                snapshot_at, category, time_period, order_by, limit_value, offset_value,
                rank, proxy_wallet, user_name, vol, pnl, profile_image, x_username, verified_badge
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )

    conn.commit()
    conn.close()
    return snapshot_at


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Polymarket trader leaderboard")
    parser.add_argument("--category", default="OVERALL", choices=sorted(VALID_CATEGORY))
    parser.add_argument("--time-period", default="DAY", choices=sorted(VALID_TIME_PERIOD))
    parser.add_argument("--order-by", default="PNL", choices=sorted(VALID_ORDER_BY))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--user", default=None, help="0x wallet address")
    parser.add_argument("--user-name", default=None, help="username filter")
    parser.add_argument("--out-csv", default=None, help="export path for CSV")
    parser.add_argument("--out-json", default=None, help="export path for JSON")
    parser.add_argument(
        "--save-db",
        default=None,
        help="save snapshot rows into sqlite db path (example: logs/leaderboard.db)",
    )
    args = parser.parse_args()

    if not (1 <= args.limit <= 50):
        raise ValueError("limit must be between 1 and 50")
    if not (0 <= args.offset <= 1000):
        raise ValueError("offset must be between 0 and 1000")

    rows = fetch_leaderboard(
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
        limit=args.limit,
        offset=args.offset,
        user=args.user,
        user_name=args.user_name,
    )

    print_table(rows)

    if args.out_csv:
        export_csv(rows, args.out_csv)
        print(f"\nSaved CSV: {args.out_csv}")

    if args.out_json:
        export_json(rows, args.out_json)
        print(f"Saved JSON: {args.out_json}")

    if args.save_db:
        snapshot_at = save_snapshot_to_db(
            rows=rows,
            db_path=args.save_db,
            category=args.category,
            time_period=args.time_period,
            order_by=args.order_by,
            limit=args.limit,
            offset=args.offset,
        )
        print(f"Saved snapshot DB: {args.save_db} at {snapshot_at}")


if __name__ == "__main__":
    main()
