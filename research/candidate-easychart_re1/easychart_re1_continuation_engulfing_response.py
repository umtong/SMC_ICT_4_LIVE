"""Source-footprint continuation confirmed by a one-minute body engulfing.

The source material does not treat every small candle which closes off an OB or
FVG as a completed entry.  At the first return it looks for a lower-timeframe
reversal candle or order block.  This policy translates that responsibility
without adding a fitted size threshold:

* the first source OB/FVG touch remains the only eligible return;
* that completed minute must close back outside the source and its real body
  must engulf the previous completed minute's body in the trade direction;
* if the first touch lacks that response, the episode is finished rather than
  waiting for a more convenient later candle.

All impulse, flow, target-refresh, structural stop, >=1R, cost, 3% NAV risk and
single-account rules are inherited unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle, Side
from easychart_re1_continuation_source_return import (
    EasyChartRE1ContinuationSourceReturnBundle,
    SourceFootprintContinuationEngine,
)
from easychart_re1_local_continuation import LocalContinuationSetup, MinuteWeight


CONTINUATION_ENGULFING_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_EVENT_LOCAL_OB_OR_FVG_RETURN_IS_"
    "EXECUTABLE_ONLY_WHEN_ITS_COMPLETED_ONE_MINUTE_REAL_BODY_ENGULFS_THE_"
    "PREVIOUS_COMPLETED_BODY_IN_THE_TRADE_DIRECTION"
)
if CONTINUATION_ENGULFING_RESPONSE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CONTINUATION_ENGULFING_RESPONSE_RULE,)


class EngulfingSourceContinuationEngine(SourceFootprintContinuationEngine):
    """Require a categorical lower-frame reversal at the first source return."""

    @staticmethod
    def _body_engulfs(
        side: Side,
        previous: MinuteWeight,
        bar: Candle,
    ) -> bool:
        if side is Side.LONG:
            return (
                bar.close > bar.open
                and bar.open <= previous.close
                and bar.close >= previous.open
            )
        return (
            bar.close < bar.open
            and bar.open >= previous.close
            and bar.close <= previous.open
        )

    def _advance_setup(
        self,
        bar: Candle,
        minute: MinuteWeight,
    ):
        setup: LocalContinuationSetup | None = self._active
        if (
            setup is not None
            and setup.state == "WAITING_PULLBACK"
            and bar.ts_close_ns > setup.impulse_time_ns
        ):
            choice = self._pullback_choice(setup, bar)
            if choice is not None:
                previous = self._minutes[-2] if len(self._minutes) >= 2 else None
                if previous is None or not self._body_engulfs(setup.side, previous, bar):
                    kind, lower, upper = choice
                    self._finish(
                        setup,
                        "local_continuation_first_source_return_lacked_engulfing_response",
                        bar.ts_close_ns,
                        pullback_kind=kind.value,
                        pullback_lower=lower,
                        pullback_upper=upper,
                        pullback_open=bar.open,
                        pullback_high=bar.high,
                        pullback_low=bar.low,
                        pullback_close=bar.close,
                        previous_open=None if previous is None else previous.open,
                        previous_close=None if previous is None else previous.close,
                        rule_provenance=CONTINUATION_ENGULFING_RESPONSE_RULE,
                    )
                    return []
        return super()._advance_setup(bar, minute)


class EasyChartRE1ContinuationEngulfingResponseBundle(
    EasyChartRE1ContinuationSourceReturnBundle
):
    """Complete displacement system plus categorical first-return response."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.continuation = EngulfingSourceContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_engulfing_response"] = {
            "entry": "FIRST_SOURCE_RETURN_WITH_ONE_MINUTE_BODY_ENGULFING",
            "rule_provenance": CONTINUATION_ENGULFING_RESPONSE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationEngulfingResponseBundle
