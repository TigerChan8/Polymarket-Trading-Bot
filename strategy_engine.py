"""
Strategy extension layer for Polymarket bot.

Purpose:
- Keep the original bot logic simple.
- Provide a pluggable place to add indicators and decision rules.
- Allow dry-run experimentation without real order execution.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


MarketSnapshot = Dict[str, Any]


@dataclass
class StrategySignal:
    name: str
    score: float
    reason: str


class Indicator:
    name: str = "indicator"

    def compute(self, snapshot: MarketSnapshot) -> Optional[float]:
        raise NotImplementedError


class Rule:
    name: str = "rule"

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        indicators: Dict[str, Optional[float]]
    ) -> Optional[StrategySignal]:
        raise NotImplementedError


class TotalAskCostIndicator(Indicator):
    name = "total_ask_cost"

    def compute(self, snapshot: MarketSnapshot) -> Optional[float]:
        yes_ask = snapshot.get("yes_ask")
        no_ask = snapshot.get("no_ask")
        if yes_ask is None or no_ask is None:
            return None
        return float(yes_ask) + float(no_ask)


class EdgeIndicator(Indicator):
    name = "edge"

    def compute(self, snapshot: MarketSnapshot) -> Optional[float]:
        yes_ask = snapshot.get("yes_ask")
        no_ask = snapshot.get("no_ask")
        if yes_ask is None or no_ask is None:
            return None
        return 1.0 - (float(yes_ask) + float(no_ask))


class SpreadSumIndicator(Indicator):
    name = "spread_sum"

    def compute(self, snapshot: MarketSnapshot) -> Optional[float]:
        yes_ask = snapshot.get("yes_ask")
        no_ask = snapshot.get("no_ask")
        yes_bid = snapshot.get("yes_bid")
        no_bid = snapshot.get("no_bid")

        if None in (yes_ask, no_ask, yes_bid, no_bid):
            return None

        yes_spread = max(0.0, float(yes_ask) - float(yes_bid))
        no_spread = max(0.0, float(no_ask) - float(no_bid))
        return yes_spread + no_spread


class PureArbitrageRule(Rule):
    name = "pure_arbitrage"

    def __init__(self, min_edge: float = 0.01):
        self.min_edge = min_edge

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        indicators: Dict[str, Optional[float]]
    ) -> Optional[StrategySignal]:
        edge = indicators.get("edge")
        if edge is None:
            return None

        if edge >= self.min_edge:
            return StrategySignal(
                name=self.name,
                score=float(edge),
                reason=f"edge={edge:.4f} >= min_edge={self.min_edge:.4f}"
            )
        return None


class TightExecutionRule(Rule):
    name = "tight_execution"

    def __init__(self, max_spread_sum: float = 0.03):
        self.max_spread_sum = max_spread_sum

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        indicators: Dict[str, Optional[float]]
    ) -> Optional[StrategySignal]:
        spread_sum = indicators.get("spread_sum")
        if spread_sum is None:
            return None

        if spread_sum <= self.max_spread_sum:
            score = max(0.0, self.max_spread_sum - spread_sum)
            return StrategySignal(
                name=self.name,
                score=score,
                reason=(
                    f"spread_sum={spread_sum:.4f} <= "
                    f"max_spread_sum={self.max_spread_sum:.4f}"
                )
            )
        return None


class StrategyPipeline:
    """Simple plug-in pipeline for indicators and rules."""

    def __init__(
        self,
        indicators: Optional[List[Indicator]] = None,
        rules: Optional[List[Rule]] = None
    ):
        self.indicators = indicators or [
            TotalAskCostIndicator(),
            EdgeIndicator(),
            SpreadSumIndicator(),
        ]
        self.rules = rules or [
            PureArbitrageRule(min_edge=0.05),  # 5% min edge — survives 2% taker fee
            TightExecutionRule(max_spread_sum=0.03),
        ]

    def evaluate(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        values: Dict[str, Optional[float]] = {}
        for indicator in self.indicators:
            try:
                values[indicator.name] = indicator.compute(snapshot)
            except Exception:
                values[indicator.name] = None

        signals: List[StrategySignal] = []
        for rule in self.rules:
            try:
                signal = rule.evaluate(snapshot, values)
                if signal:
                    signals.append(signal)
            except Exception:
                continue

        return {
            "snapshot": snapshot,
            "indicators": values,
            "signals": [asdict(s) for s in signals],
        }
