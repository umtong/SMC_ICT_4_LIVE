"""I31 benchmark-auction maturity extension for Candidate 12.

A follower sell-side reacceleration is late delivery when BTC, the benchmark
price-discovery market, has already completed a full source-range downside
projection.  The same completed-auction unit already governs local maturity in
I30; I31 applies it to broad-market state ownership.

No fitted numeric threshold is introduced: one completed source range is the
pre-existing structural objective.  All benchmark bars are complete before the
candidate observation.  This module creates no orders, prices, stops, targets,
quantities, fills, or PnL.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from auction_profile_router import RouterDecision
from market_role_router_i30 import MarketRoleAuctionRouter


class BenchmarkMaturityAuctionRouter(MarketRoleAuctionRouter):
    """Reject late follower continuation after benchmark full-range discovery."""

    def evaluate(self, symbol: str, plan: Any) -> RouterDecision:
        base = super().evaluate(symbol, plan)
        if not base.approved:
            return base

        scenario = str(plan.scenario.value)
        if scenario not in self._LOW_ACCEPTANCE_REACCELERATION:
            return base

        source = str(plan.details.get("source", ""))
        observed = pd.Timestamp(int(plan.observed_ts_ns), unit="ns", tz="UTC")
        context = dict(base.context)
        try:
            benchmark_profile, _, _ = self._profile_pair(
                self.benchmark_symbol,
                observed,
                source,
            )
            benchmark_frame = self.frames[self.benchmark_symbol].loc[:observed]
            benchmark_close = float(benchmark_frame.iloc[-1].close)
            full_range_floor = benchmark_profile.low - benchmark_profile.width
        except Exception as exc:
            context["benchmark_maturity_error"] = {
                "exception": type(exc).__name__,
                "message": str(exc),
            }
            return RouterDecision(
                False,
                "BENCHMARK_MATURITY_STATE_UNAVAILABLE",
                context,
            )

        consumed = benchmark_close <= full_range_floor
        context["benchmark_downside_maturity"] = {
            "benchmark": self.benchmark_symbol,
            "source": source,
            "source_low": benchmark_profile.low,
            "source_width": benchmark_profile.width,
            "full_range_floor": full_range_floor,
            "completed_close": benchmark_close,
            "full_range_projection_consumed": consumed,
        }
        if symbol != self.benchmark_symbol and consumed:
            return RouterDecision(
                False,
                "FOLLOWER_REACCELERATION_AFTER_BENCHMARK_FULL_RANGE_DISCOVERY",
                context,
            )

        context["i31_state"] = "BENCHMARK_AUCTION_NOT_MATURE"
        return RouterDecision(
            True,
            "BENCHMARK_MATURITY_AUCTION_APPROVED",
            context,
        )
