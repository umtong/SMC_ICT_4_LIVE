"""Causal four-symbol common-factor routing for EasyChart RE1.

A single symbol's adverse-flow absorption is ambiguous.  It may be a genuine
local inventory transfer, or only a pause while the entire crypto complex is
processing common information.  This strategy distinguishes those states using
only the four completed one-minute bars already present in the account bucket.

Common initiative is declared when:

* BTC and ETH both show coherent initiative in the same direction;
* at least three of BTC/ETH/SOL/XRP show the same direction;
* for each agreeing symbol, quote activity and absolute taker imbalance are at
  least their causal prior-60-minute medians, and signed taker flow agrees with
  material body progress.

No fitted magnitude or clock window is introduced.  The common state remains
active only while BTC, ETH and at least three symbols continue to close beyond
the midpoint of the shock candle in its direction.  An opposite common shock
reverses the state; loss of midpoint hold ends it.  While active, a plan against
the common direction is rejected before account arbitration.  Aligned and
neutral plans retain the inherited deterministic ordering.

This is the cross-asset analogue of a control-system hysteresis state: a local
boundary may reverse only after the market-wide impulse has relinquished its
causal midpoint.  All order, fill, fee, risk, stop, target and protection logic
is inherited unchanged from the production-compatible RE1 execution layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from execution_re1_flow import EasyChartRE1FlowStrategy
from nautilus_trader.model.identifiers import InstrumentId


CROSS_ASSET_COMMON_INITIATIVE_RULE = (
    "EXTERNAL_METHOD:"
    "BTC_AND_ETH_PLUS_THREE_OF_FOUR_CAUSAL_ONE_MINUTE_AGGRESSOR_FLOW_AND_PRICE_PROGRESS_DEFINE_COMMON_CRYPTO_INITIATIVE"
)
COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "COMMON_INITIATIVE_REMAINS_ACTIVE_WHILE_BTC_ETH_AND_THREE_OF_FOUR_CLOSE_BEYOND_THEIR_SHOCK_MIDPOINTS"
)
COUNTERFACTOR_ABSTENTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "PLANS_OPPOSING_ACTIVE_COMMON_CRYPTO_INITIATIVE_ARE_DEFERRED_UNTIL_THE_COMMON_FACTOR_RELINQUISHES_ITS_CAUSAL_MIDPOINT"
)
if CROSS_ASSET_COMMON_INITIATIVE_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (CROSS_ASSET_COMMON_INITIATIVE_RULE,)
for _rule in (COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE, COUNTERFACTOR_ABSTENTION_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class CommonFactorState:
    side: Side
    event_time_ns: int
    event_midpoints: dict[str, float]
    agreeing_symbols: tuple[str, ...]
    sequence: int


class EasyChartRE1MarketFactorStrategy(EasyChartRE1FlowStrategy):
    """One account whose fast router observes the complete four-symbol bucket."""

    REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.factor_analyzers: dict[InstrumentId, CausalFlowAnalyzer] = {}
        self.factor_symbols: dict[InstrumentId, str] = {}
        self.factor_state: CommonFactorState | None = None
        self.factor_counts: dict[str, int] = {}

    def _xinc(self, key: str) -> None:
        self.factor_counts[key] = self.factor_counts.get(key, 0) + 1

    def on_start(self) -> None:
        super().on_start()
        if len(self.instruments) != len(self.config.instrument_ids):
            return
        self.factor_analyzers = {
            instrument_id: CausalFlowAnalyzer(float(instrument.price_increment))
            for instrument_id, instrument in self.instruments.items()
        }
        self.factor_symbols = {
            instrument_id: instrument.raw_symbol.value
            for instrument_id, instrument in self.instruments.items()
        }
        present = tuple(sorted(self.factor_symbols.values()))
        missing = sorted(set(self.REQUIRED_SYMBOLS) - set(present))
        if missing:
            raise RuntimeError(f"market-factor router missing required symbols: {missing}")

    @staticmethod
    def _coherent_side(observation: FlowObservation | None) -> Side | None:
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

    @staticmethod
    def _beyond(side: Side, close: float, midpoint: float) -> bool:
        return close > midpoint if side is Side.LONG else close < midpoint

    def _observe_common_factor(self) -> None:
        one_minute = {
            instrument_id: bar
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        }
        if len(one_minute) != len(self.config.instrument_ids):
            self._xinc("factor_incomplete_one_minute_bucket")
            return

        observations: dict[str, FlowObservation | None] = {}
        candles: dict[str, Any] = {}
        sides: dict[str, Side | None] = {}
        for instrument_id, bar in sorted(one_minute.items(), key=lambda item: str(item[0])):
            symbol = self.factor_symbols[instrument_id]
            candle = self._candle(bar)
            observation = self.factor_analyzers[instrument_id].observe(candle)
            candles[symbol] = candle
            observations[symbol] = observation
            sides[symbol] = self._coherent_side(observation)

        common_side: Side | None = None
        for side in (Side.LONG, Side.SHORT):
            agreeing = tuple(sorted(symbol for symbol, value in sides.items() if value is side))
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
                    for symbol, candle in candles.items()
                },
                agreeing_symbols=agreeing,
                sequence=sequence,
            )
            key = "common_factor_same_side_refreshed" if sequence > 1 else "common_factor_started"
            self._xinc(key)
            self._record(
                "market_factor_common_initiative",
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
                rule_provenance=CROSS_ASSET_COMMON_INITIATIVE_RULE,
            )
            return

        state = self.factor_state
        if state is None:
            self._xinc("factor_neutral_bucket")
            return
        held = tuple(
            sorted(
                symbol
                for symbol, candle in candles.items()
                if self._beyond(state.side, float(candle.close), state.event_midpoints[symbol])
            )
        )
        leaders_hold = "BTCUSDT" in held and "ETHUSDT" in held
        majority_hold = len(held) >= 3
        if leaders_hold and majority_hold:
            self._xinc("common_factor_midpoint_hold")
            return

        self._record(
            "market_factor_state_ended",
            event_time_ns=int(self.bar_bucket_ts or 0),
            side=state.side.name,
            origin_time_ns=state.event_time_ns,
            held_symbols=list(held),
            sequence=state.sequence,
            reason="BTC_ETH_OR_THREE_OF_FOUR_LOST_SHOCK_MIDPOINT",
            rule_provenance=COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
        )
        self._xinc("common_factor_midpoint_lost")
        self.factor_state = None

    def _factor_allows(self, plan: V5TradePlan) -> bool:
        state = self.factor_state
        if state is None or state.event_time_ns > plan.observed_time_ns:
            self._xinc("plan_allowed_neutral_factor")
            return True
        if plan.side is state.side:
            self._xinc("plan_allowed_aligned_factor")
            self._record(
                "market_factor_plan_aligned",
                plan_id=plan.plan_id,
                instrument_id=plan.symbol,
                plan_side=plan.side.name,
                factor_side=state.side.name,
                factor_event_time_ns=state.event_time_ns,
                factor_sequence=state.sequence,
                rule_provenance=COUNTERFACTOR_ABSTENTION_RULE,
            )
            return True
        self._xinc("plan_rejected_counterfactor")
        self._record(
            "market_factor_plan_rejected",
            plan_id=plan.plan_id,
            instrument_id=plan.symbol,
            plan_side=plan.side.name,
            factor_side=state.side.name,
            factor_event_time_ns=state.event_time_ns,
            factor_sequence=state.sequence,
            factor_agreeing_symbols=list(state.agreeing_symbols),
            scenario_path=plan.scenario_path,
            scale_name=plan.scale_name,
            interaction_time_ns=plan.interaction_time_ns,
            rule_provenance=COUNTERFACTOR_ABSTENTION_RULE,
        )
        return False

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._observe_common_factor()

        plans: list[tuple[InstrumentId, V5TradePlan]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            for transition in engine.drain_trace():
                if transition.get("event_time_ns", 0) >= self.config.trading_start_ns:
                    self._record(
                        "scenario_transition",
                        instrument_id=str(instrument_id),
                        timeframe_minutes=timeframe,
                        **transition,
                    )
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                self._record("plan", **self._plan_event_values(plan))
                if self._factor_allows(plan):
                    plans.append((instrument_id, plan))

        ranked = sorted(
            plans,
            key=lambda item: (
                item[1].interaction_time_ns,
                -item[1].higher_timeframe_minutes,
                item[1].setup_observed_time_ns,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    @property
    def market_factor_diagnostics(self) -> dict[str, Any]:
        state = self.factor_state
        return {
            "counts": dict(sorted(self.factor_counts.items())),
            "active_state": None
            if state is None
            else {
                "side": state.side.name,
                "event_time_ns": state.event_time_ns,
                "agreeing_symbols": state.agreeing_symbols,
                "sequence": state.sequence,
            },
            "rules": (
                CROSS_ASSET_COMMON_INITIATIVE_RULE,
                COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
                COUNTERFACTOR_ABSTENTION_RULE,
            ),
        }
