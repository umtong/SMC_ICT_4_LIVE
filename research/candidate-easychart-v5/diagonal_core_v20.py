"""Channel and trend-line core while horizontal context is being rebuilt.

The current single-pivot horizontal context does not match the source's major
support/resistance, contraction and repeated-defense examples.  Individual
pivots remain valid pre-existing objectives, but this diagnostic core does not
let an isolated pivot originate a trade.  Channels and trend lines continue to
create rejection, rotation, bounce and accepted-break scenarios.

Channel main-line reversals use the breakout-wave origin stop; channel-direction
subline continuations retain the retested-edge stop.  No execution, account,
risk, management, time, daily, or trade-count rule changes.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, ScenarioSetup, StructureFamily
from domain import Side
from scenario_channel_extension_v16 import (
    ChannelExtensionTargetScenarioEngine,
    MicroChannelExtensionBundleV16,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


DIAGONAL_CORE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ISOLATED_HORIZONTAL_PIVOTS_REMAIN_OBJECTIVES_BUT_DO_NOT_ORIGINATE_TRADES"
)
MAINLINE_ORIGIN_STOP_RULE_V20 = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CHANNEL_MAIN_TRENDLINE_REVERSAL_STOP_USES_BREAKOUT_WAVE_ORIGIN"
)
for _rule, _ledger in (
    (DIAGONAL_CORE_RULE, "RESEARCH"),
    (MAINLINE_ORIGIN_STOP_RULE_V20, "TRANSLATION"),
):
    target = _contracts.RESEARCH_RULES if _ledger == "RESEARCH" else _contracts.TRANSLATION_RULES
    if _rule not in target:
        if _ledger == "RESEARCH":
            _contracts.RESEARCH_RULES += (_rule,)
        else:
            _contracts.TRANSLATION_RULES += (_rule,)


MAIN_LINE_KINDS = {
    ObjectKind.ASCENDING_CHANNEL_LOWER,
    ObjectKind.DESCENDING_CHANNEL_UPPER,
}


class DiagonalOnlyContextStructureBook(NearestAnyPivotStructureBook):
    """Keep pivot objectives while emitting only diagonal trade boundaries."""

    def boundaries_at(self, time_ns: int):  # type: ignore[no-untyped-def]
        return [
            zone
            for zone in super().boundaries_at(time_ns)
            if zone.family is not StructureFamily.HORIZONTAL
        ]


class DiagonalCoreScenarioEngine(ChannelExtensionTargetScenarioEngine):
    """Integrated channel/trend-line engine with state-specific stops."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = DiagonalOnlyContextStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        main_line_reversal = (
            setup.path is ScenarioPath.ACCEPTANCE
            and any(member.kind in MAIN_LINE_KINDS for member in setup.context_members)
        )
        if not main_line_reversal:
            return super()._acceptance_stop(setup, time_ns)

        origin = setup.acceptance_origin
        if origin is None:
            return None
        structural_stop = (
            origin.price - self.tick_size
            if setup.side is Side.LONG
            else origin.price + self.tick_size
        )
        bar = self._current_trigger_bar
        if bar is None or bar.ts_close_ns != time_ns:
            raise RuntimeError("main-line reversal stop requested without completed retest bar")
        stop = (
            min(structural_stop, bar.low - self.tick_size)
            if setup.side is Side.LONG
            else max(structural_stop, bar.high + self.tick_size)
        )
        self._inc("channel_mainline_reversal_origin_stop")
        self._trace(
            "channel_mainline_reversal_origin_stop",
            time_ns,
            setup,
            origin_pivot_id=origin.pivot_id,
            origin_price=origin.price,
            retest_bar_low=bar.low,
            retest_bar_high=bar.high,
            executable_stop=stop,
            provenance=MAINLINE_ORIGIN_STOP_RULE_V20,
        )
        return stop


class MicroDiagonalCoreBundleV20(MicroChannelExtensionBundleV16):
    """Micro integrated core with one global account slot."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = DiagonalCoreScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["trade_context_policy"] = {
            "name": "CHANNEL_AND_TRENDLINE_ONLY_ISOLATED_PIVOTS_OBJECTIVES_ONLY",
            "rule_provenance": DIAGONAL_CORE_RULE,
        }
        output["channel_mainline_reversal_stop_policy"] = {
            "name": "BREAKOUT_WAVE_ORIGIN",
            "rule_provenance": MAINLINE_ORIGIN_STOP_RULE_V20,
        }
        return output
