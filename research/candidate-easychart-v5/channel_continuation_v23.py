"""Accepted channel-direction continuation under the fixed EasyChart contract.

The integrated diagnostics separated two different channel events which had
previously shared one generic acceptance policy:

- breaking the main trend line is a possible trend reversal;
- breaking the parallel outer line in the current channel direction is trend
  continuation.

Only the second mechanism was positive in each of the three development
regimes.  This module models that complete source-backed scenario directly:
confirmed parallel channel -> body-close break of the outer line in the channel
direction -> next decision bar holds outside -> distinct departure -> first
retest -> one predeclared full-position trade.

Most prior losses were stopped one to four minutes after entry because a
15-minute channel was invalidated by the opposite wick of one 1-minute retest
bar.  EasyChart describes channel-break failure at channel re-entry and gives
the breakout-wave origin as the conservative breakout invalidation.  Because
the project requires one fixed price stop before entry, the farther of the
completed retest wick and the already-observed breakout-wave origin is used.

No execution, risk, target-management, daily, time, or trade-count rule changes.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, ScenarioSetup, StructureFamily
from diagonal_core_v20 import MicroDiagonalCoreBundleV20
from domain import Side
from hourly_direction_v21 import PersistentChannelTargetScenarioEngine


CHANNEL_CONTINUATION_RULE = (
    "SOURCE_EXPLICIT:ACCEPTED_OUTER_CHANNEL_BREAK_IN_CHANNEL_DIRECTION_IS_CONTINUATION"
)
CHANNEL_CONTINUATION_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIXED_CHANNEL_CONTINUATION_STOP_USES_BREAKOUT_WAVE_ORIGIN_AND_RETEST_EXTREME"
)
for _rule, _ledger in (
    (CHANNEL_CONTINUATION_RULE, "SOURCE"),
    (CHANNEL_CONTINUATION_STOP_RULE, "TRANSLATION"),
):
    target = _contracts.SOURCE_RULES if _ledger == "SOURCE" else _contracts.TRANSLATION_RULES
    if _rule not in target:
        if _ledger == "SOURCE":
            _contracts.SOURCE_RULES += (_rule,)
        else:
            _contracts.TRANSLATION_RULES += (_rule,)


CONTINUATION_KINDS = {
    (ObjectKind.ASCENDING_CHANNEL_UPPER, Side.LONG),
    (ObjectKind.DESCENDING_CHANNEL_LOWER, Side.SHORT),
}


class ChannelContinuationScenarioEngine(PersistentChannelTargetScenarioEngine):
    """Create only accepted breaks which extend the established channel trend."""

    @staticmethod
    def _acceptance_side(context: Any) -> Side:
        return Side.SHORT if context.side.value == "SUPPORT" else Side.LONG

    def _is_channel_continuation(
        self,
        context: Any,
        members: tuple[Any, ...],
    ) -> bool:
        side = self._acceptance_side(context)
        return any((member.kind, side) in CONTINUATION_KINDS for member in members)

    def _create_setup(
        self,
        *,
        path: ScenarioPath,
        context: Any,
        members: tuple[Any, ...],
        bar: Any,
        decision_index: int,
        state: Any,
    ) -> ScenarioSetup | None:
        if path is not ScenarioPath.ACCEPTANCE:
            return None
        if not any(member.family is StructureFamily.CHANNEL for member in members):
            return None
        if not self._is_channel_continuation(context, members):
            self._inc("non_continuation_channel_interaction_ignored")
            return None
        return super()._create_setup(
            path=path,
            context=context,
            members=members,
            bar=bar,
            decision_index=decision_index,
            state=state,
        )

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        origin = setup.acceptance_origin
        if origin is None:
            return None
        if not self.trigger_detector.bars:
            raise RuntimeError("channel continuation stop requested without trigger bar")
        current = self.trigger_detector.bars[-1]
        if current.ts_close_ns != time_ns:
            raise RuntimeError("channel continuation stop must use completed retest bar")

        origin_stop = (
            origin.price - self.tick_size
            if setup.side is Side.LONG
            else origin.price + self.tick_size
        )
        retest_stop = (
            current.low - self.tick_size
            if setup.side is Side.LONG
            else current.high + self.tick_size
        )
        stop = (
            min(origin_stop, retest_stop)
            if setup.side is Side.LONG
            else max(origin_stop, retest_stop)
        )
        self._inc("channel_continuation_origin_stop")
        self._trace(
            "channel_continuation_origin_stop",
            time_ns,
            setup,
            origin_pivot_id=origin.pivot_id,
            origin_price=origin.price,
            retest_bar_low=current.low,
            retest_bar_high=current.high,
            stop=stop,
            provenance=CHANNEL_CONTINUATION_STOP_RULE,
        )
        return stop


class MicroChannelContinuationBundleV23(MicroDiagonalCoreBundleV20):
    """One source mechanism, four symbols, one continuous account slot."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ChannelContinuationScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_CHANNEL_CONTINUATION",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["scenario_policy"] = {
            "name": "ACCEPTED_OUTER_CHANNEL_BREAK_CONTINUATION",
            "rule_provenance": CHANNEL_CONTINUATION_RULE,
            "stop_provenance": CHANNEL_CONTINUATION_STOP_RULE,
        }
        return output
