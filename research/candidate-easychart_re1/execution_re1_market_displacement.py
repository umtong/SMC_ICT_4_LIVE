"""Five-minute common displacement state for the four-symbol RE1 account.

The first cross-asset pass defined common initiative on every one-minute bucket.
That state was too granular: ordinary crypto co-movement generated thousands of
state starts and could reject a profitable local reversal.  A market-wide
information episode should be a completed displacement, not merely one aligned
minute.

This router therefore creates a common state only at a completed five-minute
close.  For each symbol the five-minute quote activity, absolute taker delta and
body progress are compared with the previous twelve completed five-minute bars
(the same causal sixty-minute horizon used by the one-minute flow engine).
BTC and ETH plus at least three of BTC/ETH/SOL/XRP must all have:

* above-baseline activity, directed taker imbalance and material body progress;
* taker-flow sign agreeing with body direction;
* the same direction.

The state then uses the inherited event-midpoint hysteresis on every completed
one-minute bucket.  Counter-direction plans are deferred only while the broad
five-minute displacement remains in control.  No magnitude percentile, fitted
threshold, session, timer, or score is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from execution_re1_market_factor import (
    COUNTERFACTOR_ABSTENTION_RULE,
    CommonFactorState,
    EasyChartRE1MarketFactorStrategy,
)


FIVE_MINUTE_COMMON_DISPLACEMENT_RULE = (
    "EXTERNAL_METHOD:"
    "BTC_ETH_AND_THREE_OF_FOUR_COMPLETED_FIVE_MINUTE_BARS_WITH_ALIGNED_ABOVE_PRIOR_SIXTY_MINUTE_FLOW_AND_PRICE_PROGRESS_DEFINE_COMMON_DISPLACEMENT"
)
FIVE_MINUTE_FACTOR_HYSTERESIS_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "FIVE_MINUTE_COMMON_DISPLACEMENT_REMAINS_ACTIVE_ONLY_WHILE_BTC_ETH_AND_THREE_OF_FOUR_ONE_MINUTE_CLOSES_HOLD_ITS_EVENT_MIDPOINT"
)
if FIVE_MINUTE_COMMON_DISPLACEMENT_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,)
if FIVE_MINUTE_FACTOR_HYSTERESIS_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,)


class FiveMinuteFlowAnalyzer(CausalFlowAnalyzer):
    """Twelve completed 5m bars equal the prior sixty-minute causal baseline."""

    BASELINE_BARS = 12
    HISTORY_BARS = 288


class EasyChartRE1MarketDisplacementStrategy(EasyChartRE1MarketFactorStrategy):
    """Use broad completed 5m displacement as the only common-factor origin."""

    def on_start(self) -> None:
        super().on_start()
        if len(self.instruments) != len(self.config.instrument_ids):
            return
        self.factor_analyzers = {
            instrument_id: FiveMinuteFlowAnalyzer(float(instrument.price_increment))
            for instrument_id, instrument in self.instruments.items()
        }

    @staticmethod
    def _coherent_five_side(observation: FlowObservation | None) -> Side | None:
        if (
            observation is None
            or not observation.active
            or not observation.directed
            or not observation.material_progress
        ):
            return None
        if observation.body > 0.0 and observation.signed_taker_quote > 0.0:
            return Side.LONG
        if observation.body < 0.0 and observation.signed_taker_quote < 0.0:
            return Side.SHORT
        return None

    def _one_minute_candles(self) -> dict[str, Any]:
        return {
            self.factor_symbols[instrument_id]: self._candle(bar)
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        }

    def _five_minute_candles(self) -> dict[str, Any]:
        return {
            self.factor_symbols[instrument_id]: self._candle(bar)
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.TRIGGER_MINUTES
        }

    def _observe_common_factor(self) -> None:
        one_minute = self._one_minute_candles()
        if len(one_minute) != len(self.config.instrument_ids):
            self._xinc("factor_incomplete_one_minute_bucket")
            return

        five_minute = self._five_minute_candles()
        common_side: Side | None = None
        observations: dict[str, FlowObservation | None] = {}
        sides: dict[str, Side | None] = {}
        if five_minute:
            if len(five_minute) != len(self.config.instrument_ids):
                self._xinc("factor_incomplete_five_minute_bucket")
            else:
                for instrument_id, analyzer in sorted(
                    self.factor_analyzers.items(),
                    key=lambda item: str(item[0]),
                ):
                    symbol = self.factor_symbols[instrument_id]
                    observation = analyzer.observe(five_minute[symbol])
                    observations[symbol] = observation
                    sides[symbol] = self._coherent_five_side(observation)
                for side in (Side.LONG, Side.SHORT):
                    agreeing = tuple(
                        sorted(symbol for symbol, value in sides.items() if value is side)
                    )
                    if (
                        sides.get("BTCUSDT") is side
                        and sides.get("ETHUSDT") is side
                        and len(agreeing) >= 3
                    ):
                        common_side = side
                        break

        if common_side is not None:
            agreeing = tuple(sorted(symbol for symbol, value in sides.items() if value is common_side))
            previous = self.factor_state
            sequence = (
                previous.sequence + 1
                if previous is not None and previous.side is common_side
                else 1
            )
            self.factor_state = CommonFactorState(
                side=common_side,
                event_time_ns=int(self.bar_bucket_ts or 0),
                event_midpoints={
                    symbol: (float(candle.open) + float(candle.close)) / 2.0
                    for symbol, candle in five_minute.items()
                },
                agreeing_symbols=agreeing,
                sequence=sequence,
            )
            self._xinc(
                "five_minute_common_displacement_refreshed"
                if sequence > 1
                else "five_minute_common_displacement_started"
            )
            self._record(
                "market_factor_five_minute_displacement",
                event_time_ns=int(self.bar_bucket_ts or 0),
                side=common_side.name,
                agreeing_symbols=list(agreeing),
                sequence=sequence,
                observations={
                    symbol: None
                    if observations[symbol] is None
                    else {
                        "activity_ratio": observations[symbol].activity_ratio,
                        "delta_ratio": observations[symbol].delta_ratio,
                        "body_ratio": observations[symbol].body_ratio,
                        "signed_taker_quote": observations[symbol].signed_taker_quote,
                        "body": observations[symbol].body,
                    }
                    for symbol in sorted(observations)
                },
                rule_provenance=FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
            )
            return

        state = self.factor_state
        if state is None:
            self._xinc("five_minute_factor_neutral_bucket")
            return
        held = tuple(
            sorted(
                symbol
                for symbol, candle in one_minute.items()
                if self._beyond(state.side, float(candle.close), state.event_midpoints[symbol])
            )
        )
        if "BTCUSDT" in held and "ETHUSDT" in held and len(held) >= 3:
            self._xinc("five_minute_common_displacement_midpoint_hold")
            return
        self._record(
            "market_factor_five_minute_state_ended",
            event_time_ns=int(self.bar_bucket_ts or 0),
            side=state.side.name,
            origin_time_ns=state.event_time_ns,
            held_symbols=list(held),
            sequence=state.sequence,
            reason="BTC_ETH_OR_THREE_OF_FOUR_LOST_FIVE_MINUTE_DISPLACEMENT_MIDPOINT",
            rule_provenance=FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
        )
        self._xinc("five_minute_common_displacement_midpoint_lost")
        self.factor_state = None

    @property
    def market_displacement_diagnostics(self) -> dict[str, Any]:
        output = dict(self.market_factor_diagnostics)
        output["rules"] = (
            FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
            FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
            COUNTERFACTOR_ABSTENTION_RULE,
        )
        return output
