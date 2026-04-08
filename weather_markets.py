"""
Weather market discovery utilities.

Fetches active and closed weather/climate events from the Gamma API using
tag_slug=weather. Provides structured market dicts consumed by all weather
strategy modules (weather_whale_monitor, weather_accuracy, etc.).

Key insight: use tag_slug=weather on /events — NOT the category= param,
which behaves differently on the Gamma endpoint vs the leaderboard API.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Set

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_json_field(value: Any, default: Any = None) -> Any:
    """Safely parse a JSON string field (outcomePrices, outcomes, clobTokenIds)."""
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return default


def _determine_winner(outcomes: List[str], outcome_prices: List[float]) -> Optional[str]:
    """
    Given outcomes list and their resolved prices, return the winning outcome label.
    The winner has a price closest to 1.0.
    Returns None if prices are still ambiguous (market not yet resolved, e.g. all ~0.5).
    Threshold: price >= 0.9 required to be considered resolved.
    """
    if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
        return None
    max_price = max(outcome_prices)
    if max_price < 0.9:
        return None
    idx = outcome_prices.index(max_price)
    return outcomes[idx]


# ──────────────────────────────────────────────────────────────────────────────
# WeatherMarketFetcher
# ──────────────────────────────────────────────────────────────────────────────

class WeatherMarketFetcher:
    """
    Fetches weather and climate markets from Gamma API.

    Correct filter: tag_slug=weather on /events.
    Note: category= param on /events does NOT filter to weather markets
    the same way the leaderboard API does — tag_slug is the right field.

    Market types covered:
    - Global temperature index (NASA GISTEMP)
    - Hurricane landfall / category / named storm count
    - Record temperature months
    - City-level weather (NYC, etc.)
    - Earthquakes / natural disasters
    """

    def __init__(self, timeout: int = 20, page_size: int = 50, max_pages: int = 10):
        self.timeout = timeout
        self.page_size = page_size
        self.max_pages = max_pages
        self.session = requests.Session()

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _request(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        for attempt in range(4):
            try:
                resp = self.session.get(
                    f"{GAMMA_BASE}/events",
                    params=params,
                    timeout=self.timeout,
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt < 3:
                        time.sleep(0.8 * (2 ** attempt))
                        continue
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else []
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(0.8 * (2 ** attempt))
        return []

    # ── Market parsing ────────────────────────────────────────────────────────

    def _parse_market(self, market: Dict[str, Any], event_tags: List[str]) -> Dict[str, Any]:
        """Extract the fields we care about from a raw Gamma market dict."""
        outcomes = _parse_json_field(market.get("outcomes"), [])
        outcome_prices_raw = _parse_json_field(market.get("outcomePrices"), [])
        outcome_prices = [float(p) for p in outcome_prices_raw] if outcome_prices_raw else []
        clob_token_ids = _parse_json_field(market.get("clobTokenIds"), [])

        # Determine winner only for closed/archived markets
        resolved_outcome: Optional[str] = None
        if market.get("closed") or market.get("archived"):
            resolved_outcome = _determine_winner(outcomes, outcome_prices)

        return {
            "conditionId": market.get("conditionId"),
            "marketId": market.get("id"),
            "question": market.get("question"),
            "slug": market.get("slug"),
            "endDate": market.get("endDate"),
            "active": bool(market.get("active", False)),
            "closed": bool(market.get("closed", False)),
            "outcomes": outcomes,
            "outcomePrices": outcome_prices,
            "clobTokenIds": clob_token_ids,
            "bestBid": float(market.get("bestBid") or 0),
            "bestAsk": float(market.get("bestAsk") or 0),
            "volume": float(market.get("volume") or 0),
            "volumeClob": float(market.get("volumeClob") or 0),
            "liquidityClob": float(market.get("liquidityClob") or 0),
            "resolvedOutcome": resolved_outcome,
            "tags": event_tags,
        }

    # ── Pagination ────────────────────────────────────────────────────────────

    def _fetch_events_paginated(
        self,
        extra_params: Dict[str, Any],
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Paginate through weather events, return all parsed market dicts."""
        all_markets: List[Dict[str, Any]] = []
        seen_condition_ids: Set[str] = set()
        pages = max_pages or self.max_pages

        for page in range(pages):
            params = {
                "tag_slug": "weather",
                "limit": self.page_size,
                "offset": page * self.page_size,
                **extra_params,
            }
            events = self._request(params)
            if not events:
                break

            for event in events:
                tags = [t.get("slug", "") for t in (event.get("tags") or [])]
                for market in event.get("markets") or []:
                    cid = market.get("conditionId")
                    if not cid or cid in seen_condition_ids:
                        continue
                    seen_condition_ids.add(cid)
                    all_markets.append(self._parse_market(market, tags))

            time.sleep(0.1)  # gentle rate-limit pacing

        return all_markets

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_active(self) -> List[Dict[str, Any]]:
        """
        Fetch all currently active (open, not closed) weather markets.
        Returns parsed market dicts sorted by volume descending.
        """
        markets = self._fetch_events_paginated({"active": "true", "closed": "false"})
        markets.sort(key=lambda m: m["volume"], reverse=True)
        return markets

    def fetch_closed(self, max_pages: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch resolved/closed weather markets for accuracy analysis.
        Returns market dicts with resolvedOutcome set where determinable.
        """
        markets = self._fetch_events_paginated({"closed": "true"}, max_pages=max_pages)
        # Sort by volume so the most-traded resolved markets appear first
        markets.sort(key=lambda m: m["volume"], reverse=True)
        return markets

    def get_active_condition_ids(self) -> Set[str]:
        """
        Return set of conditionId strings for all active weather markets.
        Useful for fast O(1) membership checks when filtering trades.
        """
        return {m["conditionId"] for m in self.fetch_active() if m.get("conditionId")}


# ──────────────────────────────────────────────────────────────────────────────
# CLI quick-view
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fetcher = WeatherMarketFetcher()

    print("=== Active Weather Markets ===")
    active = fetcher.fetch_active()
    print(f"Found {len(active)} active weather markets\n")
    for m in active[:15]:
        print(
            f"  {m['question'][:72]:<72} | "
            f"vol={m['volume']:>10,.0f} | "
            f"bid={m['bestBid']:.3f} ask={m['bestAsk']:.3f}"
        )

    print("\n=== Closed Weather Markets (sample, first 5 pages) ===")
    closed = fetcher.fetch_closed(max_pages=5)
    print(f"Found {len(closed)} closed weather markets\n")
    resolved = [m for m in closed if m.get("resolvedOutcome")]
    print(f"  With determinable winner: {len(resolved)}")
    for m in resolved[:10]:
        print(f"  {m['question'][:72]:<72} | resolved={m['resolvedOutcome']}")
