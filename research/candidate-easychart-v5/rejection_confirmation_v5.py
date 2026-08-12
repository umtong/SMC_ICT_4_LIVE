"""Minimal lower-timeframe reaction confirmation for EasyChart rejections.

The source enters a trend-line/channel bounce only after a lower-frame reversal
candle or pattern appears, and its confirmed fakeout examples likewise wait for
a reclaimed level to be retested and react.  A human sees the direction of the
completed reaction candle automatically.  The previous implementation accepted
any close on the valid side, including a candle whose real body still moved
against the trade.

This translation deliberately uses only the sign of the completed real body.
It adds no ATR, body-size, score, or profit-fitted threshold.  A failed or
non-directional first retest remains consumed so no later prettier retest can
be selected with hindsight.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, StructureZone, V5TradePlan
from domain import Candle, Side


DIRECTIONAL_RETEST_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CONFIRMED_REJECTION_FIRST_RETEST_MUST_CLOSE_IN_THE_TRADE_DIRECTION"
)

if DIRECTIONAL_RETEST_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DIRECTIONAL_RETEST_RULE,)


class DirectionalRejectionConfirmationMixin:
    """Require the first reclaimed-structure retest to show directional reaction."""

    @staticmethod
    def _directional_body(bar: Candle, side: Side) -> bool:
        return bar.close > bar.open if side is Side.LONG else bar.close < bar.open

    def _advance_rejection_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        del index
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_REJECTION_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "target_spent_before_entry",
                )
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "rejection_extreme_breached_before_retest",
                )
                continue
            _projected, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("first reclaimed-structure retest processed twice")
            setup.first_retest_consumed = True
            valid_side_close = (
                bar.close > upper if setup.side is Side.LONG else bar.close < lower
            )
            if not valid_side_close:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "rejection_first_structure_retest_failed",
                    projected_lower=lower,
                    projected_upper=upper,
                )
                continue
            if not self._directional_body(bar, setup.side):
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "rejection_first_structure_retest_lacked_directional_close",
                    projected_lower=lower,
                    projected_upper=upper,
                    retest_open=bar.open,
                    retest_close=bar.close,
                    rule_provenance=DIRECTIONAL_RETEST_RULE,
                )
                continue
            stop = (
                min(setup.interaction_extreme - self.tick_size, bar.low - self.tick_size)
                if setup.side is Side.LONG
                else max(setup.interaction_extreme + self.tick_size, bar.high + self.tick_size)
            )
            proxy: StructureZone = self.structure.snapshot_for(
                setup.context,
                bar.ts_close_ns,
            )
            self._audit(proxy)
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=proxy,
                trigger_kind=proxy.kind,
                trigger_strength=proxy.strength_ratio,
            )
            if plan is not None:
                output.append(plan)
        return output
