"""v50 internal dealing-range liquidity expansion over the v49 router.

The v40 detector is retained unchanged.  This state layer only adds a second,
independent source of causally known liquidity ranges: each confirmed five-minute
internal pivot is paired with the most recent previously confirmed opposite
pivot.  The endpoint may then be swept, reclaimed and displaced exactly like the
larger source ranges, with the paired internal-range midpoint as its economic
objective.

No pivot wing, lifetime, displacement, flow, entry, target, stop, cost, risk or
rank threshold is introduced.  Existing Candidate 11 semantic controls are
reused verbatim.
"""
from __future__ import annotations

import os
from typing import Any

from logic import MINUTE_NS, Pool, Side

from c10_v40_state import SourceEquilibriumFailedAuctionEngine


def internal_dealing_range_enabled() -> bool:
    return os.environ.get("C10_V50_INTERNAL_DEALING_RANGE", "0") == "1"


class InternalDealingRangeFailedAuctionEngine(
    SourceEquilibriumFailedAuctionEngine,
):
    """Add confirmed internal ranges without changing failed-auction semantics."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._v50_internal_high_cursor = len(self.internal_highs)
        self._v50_internal_low_cursor = len(self.internal_lows)

    def _event(
        self,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Give each unresolved both-side sweep its own evidence identity.

        The frozen detector emits the literal scenario id ``AMBIGUOUS`` for
        every unresolvable both-side bar.  That is harmless to alpha, but a
        strict event ledger interprets successive unrelated episodes as one
        broken state chain.  Re-identification changes no detector, market
        state, order, price, cost or risk decision.
        """

        if scenario_id == "AMBIGUOUS":
            scenario_id = (
                f"{self.instrument_id}-AMBIGUOUS-"
                f"{int(event_time_ns)}-{int(observed_time_ns)}-"
                f"{len(self.events)}"
            )
        super()._event(
            scenario_id,
            event_type,
            event_time_ns,
            observed_time_ns,
            previous_state,
            next_state,
            reason_code,
            reference_price,
            details,
        )

    def _confirm_internal_pivots(self, observed_ts_ns: int) -> None:
        super()._confirm_internal_pivots(observed_ts_ns)
        if not internal_dealing_range_enabled():
            self._v50_internal_high_cursor = len(self.internal_highs)
            self._v50_internal_low_cursor = len(self.internal_lows)
            return

        new_highs = self.internal_highs[self._v50_internal_high_cursor :]
        new_lows = self.internal_lows[self._v50_internal_low_cursor :]
        for event_ts_ns, known_ts_ns, level in new_highs:
            self._add_internal_endpoint(
                side=Side.HIGH,
                event_ts_ns=int(event_ts_ns),
                known_ts_ns=int(known_ts_ns),
                level=float(level),
            )
        for event_ts_ns, known_ts_ns, level in new_lows:
            self._add_internal_endpoint(
                side=Side.LOW,
                event_ts_ns=int(event_ts_ns),
                known_ts_ns=int(known_ts_ns),
                level=float(level),
            )
        self._v50_internal_high_cursor = len(self.internal_highs)
        self._v50_internal_low_cursor = len(self.internal_lows)

    def _latest_opposite_pivot(
        self,
        *,
        side: Side,
        known_ts_ns: int,
    ) -> tuple[int, int, float] | None:
        points = self.internal_lows if side == Side.HIGH else self.internal_highs
        horizon_ns = int(self.config.event_expiry_bars) * MINUTE_NS
        eligible = [
            point
            for point in points
            if int(point[1]) < known_ts_ns
            and known_ts_ns - int(point[1]) <= horizon_ns
        ]
        return eligible[-1] if eligible else None

    def _add_internal_endpoint(
        self,
        *,
        side: Side,
        event_ts_ns: int,
        known_ts_ns: int,
        level: float,
    ) -> None:
        opposite = self._latest_opposite_pivot(
            side=side,
            known_ts_ns=known_ts_ns,
        )
        if opposite is None:
            self.skips["V50_NO_PREEXISTING_OPPOSITE_INTERNAL_PIVOT"] += 1
            return
        opposite_event_ts_ns, opposite_known_ts_ns, opposite_level_raw = opposite
        opposite_level = float(opposite_level_raw)
        valid_order = (
            level > opposite_level
            if side == Side.HIGH
            else level < opposite_level
        )
        if not valid_order:
            self.skips["V50_INVALID_INTERNAL_RANGE_PRICE_ORDER"] += 1
            return

        self._pool_seq += 1
        range_id = (
            f"{self.instrument_id}-INTERNAL_5M-R{self._pool_seq:06d}"
        )
        scenario_id = f"{range_id}-{side.value}"
        expiry_bars = int(self.config.event_expiry_bars)
        pool = Pool(
            scenario_id=scenario_id,
            side=side,
            level=level,
            source="CONFIRMED_INTERNAL_5M_DEALING_RANGE",
            candidate_ts_ns=event_ts_ns,
            confirmed_ts_ns=known_ts_ns,
            confirmed_index=self._index,
            expiry_index=self._index + expiry_bars,
            range_id=range_id,
            opposite_level=opposite_level,
            strength=1,
            external=True,
            range_close_location=0.5,
            range_signed_flow=0.0,
            triggerable=True,
            trigger_start_ts_ns=known_ts_ns,
            trigger_end_ts_ns=(known_ts_ns + expiry_bars * MINUTE_NS),
        )
        self.pools.append(pool)
        self._event(
            scenario_id,
            "INTERNAL_DEALING_RANGE_ENDPOINT_CONFIRMED",
            event_ts_ns,
            known_ts_ns,
            "MAP",
            "ARMED",
            "RIGHT_CONFIRMED_FIVE_MINUTE_PIVOT_PAIRED_WITH_PREEXISTING_OPPOSITE",
            level,
            {
                "side": side.value,
                "source": pool.source,
                "range_id": range_id,
                "endpoint_level": level,
                "opposite_event_ts_ns": int(opposite_event_ts_ns),
                "opposite_known_ts_ns": int(opposite_known_ts_ns),
                "opposite_level": opposite_level,
                "equilibrium": (level + opposite_level) / 2.0,
                "pivot_wing": int(self.config.internal_pivot_wing),
                "structural_timeframe_bars": int(
                    self.config.internal_tf_bars
                ),
                "lifetime_bars": expiry_bars,
                "new_fitted_thresholds": [],
            },
        )


__all__ = [
    "InternalDealingRangeFailedAuctionEngine",
    "internal_dealing_range_enabled",
]
