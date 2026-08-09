"""Cross-universe breadth-confirmed structural-risk ichiFan strategy.

The inherited five-minute ichiFan rising edge defines the trend context.  This
variant adds one independent state transition before the order is submitted:
at the same completed universe minute, at least two of the four project
instruments must have closed their latest one-minute bar above its open.

The confirmation is deliberately simple and parameter-free.  It distinguishes
a market-wide risk-on impulse from an isolated single-altcoin break without
reusing the shifted five-minute indicators which created the setup.  Structural
stop geometry, current-NAV 3% risk sizing, costs, execution and dynamic exits
remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import ichifan_structural_strategy as _structural
import router as _router

Candidate47IchiFanBreadthConfig = _structural.Candidate47IchiFanStructuralConfig
Candidate35Config = Candidate47IchiFanBreadthConfig
SYMBOLS = _structural.SYMBOLS
_MIN_POSITIVE_SYMBOLS = 2


@dataclass(frozen=True, slots=True)
class BreadthConfirmation:
    """One exact, completed, cross-universe one-minute observation."""

    ts_event: int
    confirmed: bool
    positive_count: int
    positive_symbols: tuple[str, ...]
    nonpositive_symbols: tuple[str, ...]
    returns_bps: tuple[tuple[str, float], ...]


def causal_one_minute_breadth(
    *,
    bars_by_symbol: Mapping[str, Sequence[_router.BarObservation]],
    current_ts: int,
    expected_symbols: Sequence[str] = SYMBOLS,
) -> BreadthConfirmation:
    """Confirm that at least two instruments rose in the exact completed minute.

    Every instrument must have an observation whose event timestamp equals
    ``current_ts``.  Stale, future, missing, non-finite or non-positive price
    observations are rejected rather than silently carried forward.
    """
    timestamp = int(current_ts)
    if timestamp <= 0:
        raise ValueError("current_ts must be positive")
    symbols = tuple(str(symbol) for symbol in expected_symbols)
    if len(symbols) != 4 or len(set(symbols)) != 4:
        raise ValueError("breadth confirmation requires four unique symbols")

    positive: list[str] = []
    nonpositive: list[str] = []
    returns: list[tuple[str, float]] = []
    for symbol in symbols:
        history = bars_by_symbol.get(symbol)
        if not history:
            raise ValueError(f"missing completed one-minute history for {symbol}")
        bar = history[-1]
        if int(bar.ts_event) != timestamp:
            relation = "future" if int(bar.ts_event) > timestamp else "stale"
            raise ValueError(
                f"{relation} one-minute breadth observation for {symbol}: "
                f"{int(bar.ts_event)} != {timestamp}"
            )
        opening = float(bar.open)
        closing = float(bar.close)
        if not (
            math.isfinite(opening)
            and math.isfinite(closing)
            and opening > 0.0
            and closing > 0.0
        ):
            raise ValueError(f"invalid one-minute prices for {symbol}")
        return_bps = math.log(closing / opening) * 10_000.0
        returns.append((symbol, return_bps))
        if closing > opening:
            positive.append(symbol)
        else:
            nonpositive.append(symbol)

    count = len(positive)
    return BreadthConfirmation(
        ts_event=timestamp,
        confirmed=count >= _MIN_POSITIVE_SYMBOLS,
        positive_count=count,
        positive_symbols=tuple(positive),
        nonpositive_symbols=tuple(nonpositive),
        returns_bps=tuple(returns),
    )


class Candidate47IchiFanBreadthStructuralStrategy(
    _structural.Candidate47IchiFanStructuralStrategy,
):
    """Structural-risk ichiFan with independent current-minute market breadth."""

    def __init__(self, config: Candidate47IchiFanBreadthConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ichifan_breadth_evaluations": 0,
                "ichifan_breadth_confirmed": 0,
                "ichifan_breadth_rejected": 0,
                "ichifan_breadth_invalid": 0,
                "ichifan_breadth_confirmed_submissions": 0,
                "ichifan_breadth_count_distribution": {
                    "0": 0,
                    "1": 0,
                    "2": 0,
                    "3": 0,
                    "4": 0,
                },
                "ichifan_breadth_policy": (
                    "five-minute-ichiFan-context-plus-exact-current-one-minute-"
                    "two-of-four-positive-confirmation"
                ),
            }
        )

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        if decision.side <= 0:
            super()._submit_decision(decision, ts_event)
            return

        self.diagnostics["ichifan_breadth_evaluations"] += 1
        try:
            breadth = causal_one_minute_breadth(
                bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
                current_ts=ts_event,
            )
        except ValueError as error:
            self.diagnostics["ichifan_breadth_invalid"] += 1
            self._event(
                "ICHIFAN_BREADTH_INVALID",
                ts_event,
                symbol=decision.symbol,
                reason=str(error),
            )
            return

        distribution = self.diagnostics["ichifan_breadth_count_distribution"]
        key = str(breadth.positive_count)
        distribution[key] = int(distribution.get(key, 0)) + 1
        returns = dict(breadth.returns_bps)

        if not breadth.confirmed:
            self.diagnostics["ichifan_breadth_rejected"] += 1
            self._event(
                "ICHIFAN_BREADTH_UNCONFIRMED",
                ts_event,
                symbol=decision.symbol,
                positive_count=breadth.positive_count,
                positive_symbols=list(breadth.positive_symbols),
                nonpositive_symbols=list(breadth.nonpositive_symbols),
                returns_bps=returns,
                reason="FEWER_THAN_TWO_OF_FOUR_CURRENT_COMPLETED_1M_BARS_UP",
            )
            return

        self.diagnostics["ichifan_breadth_confirmed"] += 1
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "breadth_confirmation": "EXACT_CURRENT_COMPLETED_1M_TWO_OF_FOUR_UP",
                "breadth_observation_ts": breadth.ts_event,
                "breadth_positive_count": breadth.positive_count,
                "breadth_positive_symbols": ",".join(breadth.positive_symbols),
                "breadth_nonpositive_symbols": ",".join(breadth.nonpositive_symbols),
            }
        )
        for symbol, return_bps in breadth.returns_bps:
            diagnostics[f"breadth_ret_60s_bps_{symbol.lower()}"] = return_bps

        confirmed_decision = replace(
            decision,
            reasons=decision.reasons + ("CROSS_UNIVERSE_1M_BREADTH_CONFIRMATION",),
            diagnostics=diagnostics,
        )
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(confirmed_decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before:
            self.diagnostics["ichifan_breadth_confirmed_submissions"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "candidate": "candidate-47-public-ichiv2-structural-breadth",
                        "breadth_confirmation": (
                            "EXACT_CURRENT_COMPLETED_1M_TWO_OF_FOUR_UP"
                        ),
                        "breadth_observation_ts": breadth.ts_event,
                        "breadth_positive_count": breadth.positive_count,
                        "breadth_positive_symbols": list(breadth.positive_symbols),
                        "breadth_nonpositive_symbols": list(
                            breadth.nonpositive_symbols
                        ),
                        "breadth_returns_bps": returns,
                    }
                )


Candidate35Strategy = Candidate47IchiFanBreadthStructuralStrategy
