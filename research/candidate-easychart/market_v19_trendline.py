"""Source-correct active-structure sponsorship for EasyChart trendline retests.

The narrated Case 02 does not say that the bullish order block must be created
*after* the trendline break. It says that, at the first retest, a bullish order
block is already visible "to the left" and overlaps the trendline. The decision
role is therefore an active entry structure, not necessarily a response formed
inside the breakout leg.

This module preserves the v18 set-membership trendline, acceptance, first-retest,
origin, objective, fixed-risk and cancellation contracts. It changes one
semantic restriction only:

* a fresh same-side EasyChart order block may sponsor the first retest whether it
  pre-existed the break or was formed by the breakout response;
* its creation must still be observable before entry;
* any prior mitigation between its observation and the first retest makes it
  inactive;
* the moving line band must overlap its fixed body zone at the first retest and
  remain overlapped while an order is pending.

The two temporal relations are recorded as PREEXISTING_ACTIVE_OB and
BREAK_RESPONSE_OB. They are alternative role witnesses, not confirmation votes.
"""
from __future__ import annotations

from dataclasses import replace

from domain_v3 import Side
from market_v15 import FootprintRef
from market_v18_trendline import TrendlineRoleFlipEngine, TrendlineState


class ActiveStructureTrendlineRoleFlipEngine(TrendlineRoleFlipEngine):
    """v18 geometry with source-correct active OB eligibility."""

    @staticmethod
    def _order_block_relation(
        *,
        state: TrendlineState,
        order_block: FootprintRef,
    ) -> str:
        assert state.break_time_ns is not None
        return (
            "PREEXISTING_ACTIVE_OB"
            if order_block.observed_time_ns < state.break_time_ns
            else "BREAK_RESPONSE_OB"
        )

    def _eligible_order_blocks(
        self,
        *,
        state: TrendlineState,
        current,
    ) -> list[FootprintRef]:
        line = state.line
        band_low, band_high = line.price_band(current.ts_close_ns)
        output: list[FootprintRef] = []
        for item in self.footprints.values():
            if (
                item.kind != "ORDER_BLOCK"
                or item.side is not line.trade_side
                or item.observed_time_ns > current.ts_close_ns
                or not self._fresh(item, current)
                or max(item.zone_low, band_low) > min(item.zone_high, band_high)
            ):
                continue
            if line.trade_side is Side.LONG and item.proximal <= current.close:
                output.append(item)
            elif line.trade_side is Side.SHORT and item.proximal >= current.close:
                output.append(item)
        # First reachable active structure is selected by the inherited state
        # engine. Sort older active structures first only as a deterministic
        # tie breaker; no recency score is introduced.
        output.sort(
            key=lambda item: (
                item.observed_time_ns,
                -item.timeframe_minutes,
                item.footprint_id,
            )
        )
        return output

    def _build_setup(self, *, state, current, order_block):
        relation = self._order_block_relation(
            state=state,
            order_block=order_block,
        )
        setup = super()._build_setup(
            state=state,
            current=current,
            order_block=order_block,
        )
        # The inherited builder always appends one audit row for this attempt.
        if self.audit_rows:
            row = self.audit_rows[-1]
            if (
                row.get("version_id") == state.line.version_id
                and row.get("first_retest_time_ns") == current.ts_close_ns
                and row.get("order_block_id") == order_block.footprint_id
            ):
                row["order_block_relation"] = relation
        if setup is None:
            self._count(f"attempts_{relation.lower()}")
            return None
        self._count(f"setups_{relation.lower()}")
        return replace(
            setup,
            family="TRENDLINE_ACCEPTED_BREAK_FIRST_RETEST_ACTIVE_OB",
            context_bias=(
                f"{setup.context_bias}|OB_TEMPORAL_ROLE={relation}"
                "|OB_POLICY=ACTIVE_AT_FIRST_RETEST_NOT_POSTBREAK_ONLY"
            ),
        )


__all__ = ["ActiveStructureTrendlineRoleFlipEngine"]
