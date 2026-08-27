"""NautilusTrader strategy for the EasyChart C causal response system."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from nautilus_trader.model.identifiers import InstrumentId

from causal_state import CausalMarketState, STATE_POLICY
from contracts_v5 import V5TradePlan
from execution_re1_flow import EasyChartRE1FlowStrategy
from robust_router_system import economic_geometry, live_feature_record

from easychart_c.core import (
    MODEL_VERSION,
    CausalResponseRouter,
    replace_with_first_objective,
)


class EasyChartCCausalResponseStrategy(EasyChartRE1FlowStrategy):
    """One account, four symbols, one position, one causal response policy."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        model_path = os.environ.get("EASYCHART_C_MODEL_PATH")
        metadata_path = os.environ.get("EASYCHART_C_METADATA_PATH")
        if not model_path or not metadata_path:
            raise RuntimeError(
                "EASYCHART_C_MODEL_PATH and EASYCHART_C_METADATA_PATH are required",
            )
        self.response_router = CausalResponseRouter.load(
            Path(model_path),
            Path(metadata_path),
        )
        self.market_state: CausalMarketState | None = None

    def on_start(self) -> None:
        super().on_start()
        symbols = tuple(
            self.instruments[instrument_id].raw_symbol.value
            for instrument_id in self.config.instrument_ids
        )
        self.market_state = CausalMarketState(symbols)
        configured_risk = float(self.config.risk_fraction)
        if abs(configured_risk - self.response_router.risk_fraction) > 1e-12:
            raise RuntimeError(
                f"router risk {self.response_router.risk_fraction} != strategy risk {configured_risk}",
            )
        self._record(
            "easychart_c_router_loaded",
            model_version=MODEL_VERSION,
            trained_through_ns=self.response_router.trained_through_ns,
            probability_threshold=self.response_router.threshold,
            first_objective_r=self.response_router.objective_r,
            max_target_cost_r=self.response_router.max_target_cost_r,
            excluded_trigger_kinds=sorted(self.response_router.excluded_trigger_kinds),
            excluded_higher_zone_kinds=sorted(
                self.response_router.excluded_higher_zone_kinds,
            ),
            state_policy=STATE_POLICY,
            symbols=symbols,
        )

    @staticmethod
    def _best_plan_trace(
        transitions: list[Mapping[str, Any]],
    ) -> dict[str, Mapping[str, Any]]:
        output: dict[str, Mapping[str, Any]] = {}
        scores: dict[str, tuple[int, int]] = {}
        for transition in transitions:
            plan_id = transition.get("plan_id")
            if not plan_id:
                continue
            key = str(plan_id)
            richness = sum(
                value is not None and str(value).lower() not in {"nan", "none"}
                for value in transition.values()
            )
            event_time = int(
                transition.get("event_time_ns")
                or transition.get("trigger_time_ns")
                or transition.get("ts_ns")
                or 0
            )
            score = richness, event_time
            if key not in scores or score > scores[key]:
                scores[key] = score
                output[key] = transition
        return output

    @staticmethod
    def _side_name(plan: V5TradePlan) -> str:
        name = getattr(plan.side, "name", None)
        if name not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported plan side {plan.side!r}")
        return str(name)

    def _economics(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> dict[str, float]:
        instrument = self.instruments[instrument_id]
        return economic_geometry(
            side=self._side_name(plan),
            entry=float(plan.entry),
            stop=float(plan.stop),
            target=float(plan.target),
            tick_size=float(instrument.price_increment),
            entry_slippage_ticks=int(self.config.estimated_entry_slippage_ticks),
            target_slippage_ticks=1,
            stop_slippage_ticks=int(self.config.estimated_stop_slippage_ticks),
            entry_fee_rate=float(self.config.estimated_entry_fee_rate),
            exit_fee_rate=float(self.config.estimated_stop_fee_rate),
        )

    def _update_market_state(self) -> None:
        if self.market_state is None:
            raise RuntimeError("causal market state is not initialized")
        execution_bars = [
            (instrument_id, bar)
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        ]
        if len(execution_bars) != len(self.config.instrument_ids):
            raise RuntimeError(
                f"expected {len(self.config.instrument_ids)} synchronized one-minute bars, "
                f"received {len(execution_bars)}",
            )
        for instrument_id, bar in sorted(execution_bars, key=lambda item: str(item[0])):
            symbol = self.instruments[instrument_id].raw_symbol.value
            self.market_state.observe(symbol, self._candle(bar))
        self.market_state.finalize()
        if self.market_state.watermark_ns != self.bar_bucket_ts:
            raise RuntimeError(
                f"state watermark {self.market_state.watermark_ns} != bucket {self.bar_bucket_ts}",
            )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._update_market_state()
        assert self.market_state is not None

        plans: list[tuple[InstrumentId, V5TradePlan, Mapping[str, Any] | None]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            transitions = list(engine.drain_trace())
            trace_by_plan = self._best_plan_trace(transitions)
            for transition in transitions:
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
                if int(plan.observed_time_ns) <= self.response_router.trained_through_ns:
                    raise RuntimeError(
                        "EasyChart C evaluation overlaps development labels: "
                        f"{plan.plan_id} @ {plan.observed_time_ns} <= "
                        f"{self.response_router.trained_through_ns}",
                    )
                plans.append((instrument_id, plan, trace_by_plan.get(plan.plan_id)))
                self._record("plan", **self._plan_event_values(plan))

        ranked: list[
            tuple[
                float,
                float,
                int,
                int,
                str,
                str,
                InstrumentId,
                V5TradePlan,
                dict[str, Any],
            ]
        ] = []
        for instrument_id, original_plan, trace in plans:
            instrument = self.instruments[instrument_id]
            symbol = instrument.raw_symbol.value
            state = self.market_state.snapshot(symbol, self.bar_bucket_ts)
            original_economics = self._economics(instrument_id, original_plan)
            record = live_feature_record(
                original_plan,
                trace=trace,
                economics=original_economics,
                state=state,
            )
            executable_plan = replace_with_first_objective(
                original_plan,
                tick_size=float(instrument.price_increment),
                objective_r=self.response_router.objective_r,
            )
            fixed_economics = self._economics(instrument_id, executable_plan)
            decision = self.response_router.decision(
                record,
                fixed_target_economics=fixed_economics,
            )
            values = {
                "easychart_c_accepted": decision.accepted,
                "easychart_c_probability_target_first": decision.probability_target_first,
                "easychart_c_probability_threshold": decision.threshold,
                "easychart_c_target_cost_r": decision.target_cost_r,
                "easychart_c_target_net_r": decision.target_net_r,
                "easychart_c_stop_net_r": decision.stop_net_r,
                "easychart_c_expected_net_r": decision.expected_net_r,
                "easychart_c_expected_log_growth": decision.expected_log_growth,
                "easychart_c_reason": decision.reason,
                "easychart_c_original_target": float(original_plan.target),
                "easychart_c_original_gross_rr": float(original_plan.gross_rr),
                "easychart_c_executable_target": float(executable_plan.target),
                "easychart_c_executable_gross_rr": float(executable_plan.gross_rr),
                "easychart_c_trigger_zone_kind": str(record.get("trigger_zone_kind")),
                "easychart_c_mechanism_owner": str(record.get("mechanism_owner")),
            }
            self._record(
                "easychart_c_plan_scored",
                plan_id=original_plan.plan_id,
                instrument_id=str(instrument_id),
                **values,
            )
            if not decision.accepted:
                continue
            ranked.append(
                (
                    -decision.expected_log_growth,
                    -decision.probability_target_first,
                    int(original_plan.interaction_time_ns),
                    -int(original_plan.higher_timeframe_minutes),
                    str(original_plan.symbol),
                    str(original_plan.plan_id),
                    instrument_id,
                    executable_plan,
                    values,
                ),
            )
        ranked.sort(key=lambda item: item[:6])

        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(ranked, start=1):
                    instrument_id, plan, values = item[6], item[7], item[8]
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=(
                            None if self.active_plan is None else self.active_plan.plan_id
                        ),
                        portfolio_flat=self._portfolio_flat(),
                        **values,
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(ranked):
                    instrument_id, plan, values = item[6], item[7], item[8]
                    self.plan_log[plan.plan_id] = plan
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "easychart_c_arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            **values,
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][7].plan_id
                    for rank, item in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        instrument_id, plan, values = item[6], item[7], item[8]
                        self._record(
                            "plan_skipped_easychart_c_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                            **values,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
