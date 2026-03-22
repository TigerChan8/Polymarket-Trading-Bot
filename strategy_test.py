"""
Strategy pipeline test runner (dry-run).

Runs market queries, computes indicators/signals, and prints a compact summary.
No real trading execution is performed.
"""

import argparse
import time
from typing import Dict, Any, List

from bot import PolyArbitrageBot
from strategy_engine import StrategyPipeline


def run_strategy_test(duration: int = 60, market_limit: int = 10) -> None:
    bot = PolyArbitrageBot()
    pipeline = StrategyPipeline()

    markets = bot.get_active_markets(limit=market_limit)
    if not markets:
        print("[✗] No active markets found.")
        return

    print("=" * 70)
    print("🧪 Strategy Pipeline Dry-Run")
    print("=" * 70)
    print(f"[*] Duration: {duration}s | Markets: {len(markets)}")

    started = time.time()
    scan = 0
    total_signals = 0

    while time.time() - started < duration:
        scan += 1
        scan_signals = 0

        for market in markets:
            prices = bot.get_market_prices(market["id"])
            if not prices:
                continue

            snapshot: Dict[str, Any] = {
                "market_id": market["id"],
                "market_question": market.get("question", ""),
                "yes_price": prices["yes_price"],
                "no_price": prices["no_price"],
                "yes_ask": prices.get("yes_ask", prices["yes_price"]),
                "no_ask": prices.get("no_ask", prices["no_price"]),
                "yes_bid": prices.get("yes_bid", prices["yes_price"]),
                "no_bid": prices.get("no_bid", prices["no_price"]),
            }

            result = pipeline.evaluate(snapshot)
            signals: List[Dict[str, Any]] = result["signals"]
            if signals:
                scan_signals += len(signals)
                total_signals += len(signals)
                print(f"\n[scan {scan}] {market['id']} | {market.get('question', '')[:60]}")
                print(f"  indicators: {result['indicators']}")
                for signal in signals:
                    print(
                        f"  signal: {signal['name']} | "
                        f"score={signal['score']:.4f} | {signal['reason']}"
                    )

            time.sleep(0.1)

        elapsed = time.time() - started
        print(
            f"[*] scan={scan} elapsed={elapsed:.1f}s "
            f"signals_this_scan={scan_signals} total_signals={total_signals}"
        )
        time.sleep(bot.scan_interval)

    print("\n" + "=" * 70)
    print("[✓] Strategy dry-run completed")
    print(f"    scans: {scan}")
    print(f"    total signals: {total_signals}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run strategy dry-run checks")
    parser.add_argument("--duration", type=int, default=60, help="Run duration in seconds")
    parser.add_argument("--markets", type=int, default=10, help="How many markets to monitor")
    args = parser.parse_args()

    run_strategy_test(duration=args.duration, market_limit=args.markets)
