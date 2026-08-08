"""I30 market-role and auction-maturity router for Candidate 12.

I30 preserves I19's plan geometry and I29's completed-auction profile semantics.
It adds orthogonal state ownership rules learned across the designated
*development* weeks, not from a single losing trade:

* BTC is the benchmark price-discovery market.  A follower high-acceptance plan
  is late delivery, not an independent discovery leg.
* A follower high rejection is valid only after benchmark failure at the same
  completed boundary or after the follower probes beyond its pre-existing
  excess tail.  Two completed BTC closes beyond the same boundary mean broad
  acceptance and veto the fade.
* A delayed low reversal must actually close above its reclaim high; a bullish
  FVG below that high is not a market-structure shift.
* Acceptance-failure reversals may not fade a completed auction whose value has
  already migrated in the accepted direction.
* A low-acceptance reacceleration starting more than one completed source range
  below the source boundary belongs to a new mature auction; the old expansion
  trough no longer owns the target.
* A follower direct low-acceptance plan is retained only as idiosyncratic
  downside discovery: BTC has not already accepted its own boundary and the
  follower still owns a full completed-range objective.

All inputs are complete before ``evaluate`` is called.  The router creates no
orders, prices, quantities, fills, or PnL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from auction_profile_router import AuctionProfile, RouterDecision
from auction_profile_router_i29 import ExcessTailAuctionRouter


@dataclass(frozen=True, slots=True)
class BenchmarkState:
    symbol: str
    source: str
    source_profile: AuctionProfile
    previous_close: float
    current_close: float
    true_high_acceptance: bool
    true_low_acceptance: bool
    failed_high_acceptance: bool
    failed_low_acceptance: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "source_profile": self.source_profile.to_dict(),
            "previous_close": self.previous_close,
            "current_close": self.current_close,
            "true_high_acceptance": self.true_high_acceptance,
            "true_low_acceptance": self.true_low_acceptance,
            "failed_high_acceptance": self.failed_high_acceptance,
            "failed_low_acceptance": self.failed_low_acceptance,
        }


class MarketRoleAuctionRouter(ExcessTailAuctionRouter):
    """Route I19 plans by benchmark ownership and causal auction maturity."""

    _HIGH_ACCEPTANCE = frozenset(
        {"ASIA_HIGH_ACCEPTANCE", "LONDON_HIGH_ACCEPTANCE"}
    )
    _LOW_ACCEPTANCE = frozenset(
        {"ASIA_LOW_ACCEPTANCE", "LONDON_LOW_ACCEPTANCE"}
    )
    _HIGH_ACCEPTANCE_FAILURE = frozenset(
        {"ASIA_HIGH_ACCEPTANCE_FAILURE", "LONDON_HIGH_ACCEPTANCE_FAILURE"}
    )
    _LOW_ACCEPTANCE_FAILURE = frozenset(
        {
            "ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL",
            "LONDON_LOW_ACCEPTANCE_FAILURE_REVERSAL",
        }
    )
    _LOW_ACCEPTANCE_REACCELERATION = frozenset(
        {
            "ASIA_LOW_ACCEPTANCE_REACCELERATION",
            "LONDON_LOW_ACCEPTANCE_REACCELERATION",
        }
    )
    _DELAYED_LOW_REJECTION = frozenset(
        {"ASIA_LOW_DELAYED_REJECTION", "LONDON_LOW_DELAYED_REJECTION"}
    )

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        price_increments: Mapping[str, float],
        *,
        benchmark_symbol: str = "BTCUSDT",
    ) -> None:
        super().__init__(frames, price_increments)
        if benchmark_symbol not in self.frames:
            raise ValueError(f"benchmark symbol is unavailable: {benchmark_symbol}")
        self.benchmark_symbol = benchmark_symbol

    def _profile_pair(
        self,
        symbol: str,
        observed: pd.Timestamp,
        source: str,
    ) -> tuple[AuctionProfile, AuctionProfile, str]:
        start, end = self._source_bounds(observed, source)
        current = self._profile(symbol, start, end)
        previous = self._profile(
            symbol,
            start - pd.Timedelta(hours=6),
            start,
        )
        migration = (
            "ABOVE_PRIOR_VALUE"
            if current.vwap > previous.value_high
            else "BELOW_PRIOR_VALUE"
            if current.vwap < previous.value_low
            else "OVERLAPPING_PRIOR_VALUE"
        )
        return current, previous, migration

    def _benchmark_state(
        self,
        observed: pd.Timestamp,
        source: str,
    ) -> BenchmarkState:
        current, _, _ = self._profile_pair(
            self.benchmark_symbol,
            observed,
            source,
        )
        frame = self.frames[self.benchmark_symbol].loc[:observed]
        five = (
            frame.resample("5min", label="right", closed="right")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
            .loc[:observed]
        )
        if len(five.index) < 2:
            raise ValueError("benchmark has fewer than two completed five-minute bars")
        previous_close = float(five.iloc[-2].close)
        current_close = float(five.iloc[-1].close)
        return BenchmarkState(
            symbol=self.benchmark_symbol,
            source=source,
            source_profile=current,
            previous_close=previous_close,
            current_close=current_close,
            true_high_acceptance=(
                previous_close > current.high and current_close > current.high
            ),
            true_low_acceptance=(
                previous_close < current.low and current_close < current.low
            ),
            failed_high_acceptance=(
                previous_close > current.high and current_close < current.high
            ),
            failed_low_acceptance=(
                previous_close < current.low and current_close > current.low
            ),
        )

    @staticmethod
    def _scenario(plan: Any) -> str:
        return str(plan.scenario.value)

    def evaluate(self, symbol: str, plan: Any) -> RouterDecision:
        base = super().evaluate(symbol, plan)
        if not base.approved:
            return base

        scenario = self._scenario(plan)
        source = str(plan.details.get("source", ""))
        observed = pd.Timestamp(int(plan.observed_ts_ns), unit="ns", tz="UTC")
        context = dict(base.context)
        context["benchmark_symbol"] = self.benchmark_symbol
        context["market_role"] = (
            "BENCHMARK" if symbol == self.benchmark_symbol else "FOLLOWER"
        )

        if symbol != self.benchmark_symbol and scenario in self._HIGH_ACCEPTANCE:
            return RouterDecision(
                False,
                "FOLLOWER_HIGH_ACCEPTANCE_LACKED_BENCHMARK_OWNERSHIP",
                context,
            )

        if symbol != self.benchmark_symbol and scenario in self._IMMEDIATE_HIGH_REJECTIONS:
            try:
                benchmark = self._benchmark_state(observed, source)
            except Exception as exc:
                context["benchmark_state_error"] = {
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                return RouterDecision(False, "BENCHMARK_STATE_UNAVAILABLE", context)
            context["benchmark_state"] = benchmark.to_dict()
            if benchmark.true_high_acceptance:
                return RouterDecision(
                    False,
                    "BENCHMARK_TRUE_HIGH_ACCEPTANCE_VETOED_FOLLOWER_FADE",
                    context,
                )
            local_cleared_tail = bool(
                context.get("excess_tail_test", {}).get(
                    "cleared_excess_tail",
                    False,
                )
            )
            if not benchmark.failed_high_acceptance and not local_cleared_tail:
                return RouterDecision(
                    False,
                    "FOLLOWER_HIGH_REJECTION_LACKED_BENCHMARK_FAILURE_OR_NEW_PRICE_PROBE",
                    context,
                )
            context["follower_high_rejection_transition"] = (
                "BENCHMARK_FAILED_ACCEPTANCE"
                if benchmark.failed_high_acceptance
                else "FOLLOWER_CLEARED_PRE_EXISTING_EXCESS_TAIL"
            )

        if scenario in self._DELAYED_LOW_REJECTION:
            reclaim_high = plan.details.get("reclaim_high")
            if reclaim_high is None:
                return RouterDecision(
                    False,
                    "DELAYED_LOW_REJECTION_RECLAIM_HIGH_UNAVAILABLE",
                    context,
                )
            expected_entry = float(plan.expected_entry)
            reclaim_high_value = float(reclaim_high)
            context["delayed_low_mss"] = {
                "expected_entry": expected_entry,
                "reclaim_high": reclaim_high_value,
                "closed_above_reclaim_high": expected_entry > reclaim_high_value,
            }
            if expected_entry <= reclaim_high_value:
                return RouterDecision(
                    False,
                    "DELAYED_LOW_REVERSAL_LACKED_ACTUAL_BULLISH_MSS",
                    context,
                )

        if scenario in self._HIGH_ACCEPTANCE_FAILURE | self._LOW_ACCEPTANCE_FAILURE:
            try:
                current, previous, migration = self._profile_pair(
                    symbol,
                    observed,
                    source,
                )
            except Exception as exc:
                context["failure_value_error"] = {
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                return RouterDecision(False, "FAILURE_VALUE_STATE_UNAVAILABLE", context)
            context["failure_value_state"] = {
                "current": current.to_dict(),
                "previous": previous.to_dict(),
                "value_migration": migration,
            }
            if (
                scenario in self._HIGH_ACCEPTANCE_FAILURE
                and migration == "ABOVE_PRIOR_VALUE"
            ):
                return RouterDecision(
                    False,
                    "HIGH_ACCEPTANCE_FAILURE_AGAINST_MIGRATED_HIGHER_VALUE",
                    context,
                )
            if (
                scenario in self._LOW_ACCEPTANCE_FAILURE
                and migration == "BELOW_PRIOR_VALUE"
            ):
                return RouterDecision(
                    False,
                    "LOW_ACCEPTANCE_FAILURE_AGAINST_MIGRATED_LOWER_VALUE",
                    context,
                )

        if scenario in self._LOW_ACCEPTANCE_REACCELERATION:
            session_low = float(plan.details.get("session_low"))
            session_width = float(plan.details.get("session_width"))
            expected_entry = float(plan.expected_entry)
            floor = session_low - session_width
            increment = self.price_increments[symbol]
            context["low_reacceleration_maturity"] = {
                "session_low": session_low,
                "session_width": session_width,
                "full_range_floor": floor,
                "expected_entry": expected_entry,
                "within_original_source_range_projection": (
                    expected_entry >= floor - increment
                ),
            }
            if expected_entry < floor - increment:
                return RouterDecision(
                    False,
                    "LOW_REACCELERATION_AFTER_FULL_SOURCE_RANGE_TRAVERSAL",
                    context,
                )

        if symbol != self.benchmark_symbol and scenario in self._LOW_ACCEPTANCE:
            try:
                benchmark = self._benchmark_state(observed, source)
            except Exception as exc:
                context["benchmark_state_error"] = {
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                return RouterDecision(False, "BENCHMARK_STATE_UNAVAILABLE", context)
            context["benchmark_state"] = benchmark.to_dict()
            session_low = float(plan.details.get("session_low"))
            session_width = float(plan.details.get("session_width"))
            target = float(plan.target_price)
            increment = self.price_increments[symbol]
            owns_full_range = target <= session_low - session_width + increment
            context["follower_low_acceptance_ownership"] = {
                "target": target,
                "full_range_objective": session_low - session_width,
                "owns_full_range": owns_full_range,
                "benchmark_true_low_acceptance": benchmark.true_low_acceptance,
            }
            if benchmark.true_low_acceptance:
                return RouterDecision(
                    False,
                    "FOLLOWER_LOW_ACCEPTANCE_AFTER_BENCHMARK_DISCOVERY",
                    context,
                )
            if not owns_full_range:
                return RouterDecision(
                    False,
                    "FOLLOWER_LOW_ACCEPTANCE_LACKED_FULL_RANGE_OBJECTIVE",
                    context,
                )

        context["i30_state"] = "MARKET_ROLE_AUCTION_APPROVED"
        return RouterDecision(True, "MARKET_ROLE_AUCTION_APPROVED", context)
