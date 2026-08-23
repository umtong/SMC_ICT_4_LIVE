"""Nautilus strategy for the integrated causal ML EasyChart system."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from nautilus_trader.model.identifiers import InstrumentId

from causal_state import CausalMarketState, STATE_POLICY
from contracts_v5 import V5TradePlan
from execution_re1_flow import EasyChartRE1FlowStrategy
from robust_router import (
    MODEL_VERSION,
    RobustPlanRouter,
    economic_geometry,
    live_feature_record,
)


class EasyChartMLSystemStrategy(EasyChartRE1FlowStrategy):
    """One four-symbol account routed by expected causal log-NAV growth."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        model_path = os.environ.get("EASYCHART_ML_SYSTEM_MODEL_PATH")
        if not model_path:
            raise RuntimeError(
                "EASYCHART_ML_SYSTEM_MODEL_PATH is required; silent policy fallback is forbidden",
            )
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        self.robust_router = RobustPlanRouter.load(path)
        self.ml_system_model_path = str(path)
        self.market_state: CausalMarketState | None = None

    def on_start(self) -> None:
        super().on_start()
        symbols = tuple(
            self.instruments[instrument_id].raw_symbol.value
            for instrument_id in self.config.instrument_ids
        )
        self.market_state = CausalMarketState(symbols)
        configured_risk = float(self.config.risk_fraction)
        if abs(configured_risk - self.robust_router.risk_fraction) > 1e-12:
            raise RuntimeError(
                "router/execution risk mismatch: "
                f"{self.robust_router.risk_fraction} != {configured_risk}",
            )
        self._record(
            "ml_system_router_loaded",
            model_path=self.ml_system_model_path,
            model_version=MODEL_VERSION,
            trained_through_ns=self.robust_router.trained_through_ns,
            feature_dimension=self.robust_router.dimension,
            ensemble_members=len(self.robust_router.models),
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
                f"one-minute synchronized state expected {len(self.config.instrument_ids)} bars, "
                f"received {len(execution_bars)}",
            )
        for instrument_id, bar in sorted(
            execution_bars,
            key=lambda item: str(item[0]),
        ):
            symbol = self.instruments[instrument_id].raw_symbol.value
            self.market_state.observe(symbol, self._candle(bar))
        self.market_state.finalize()
        if self.market_state.watermark_ns != self.bar_bucket_ts:
            raise RuntimeError(
                f"causal state watermark {self.market_state.watermark_ns} != bucket {self.bar_bucket_ts}",
            )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._update_market_state()
        assert self.market_state is not None

        plans: list[
            tuple[InstrumentId, V5TradePlan, Mapping[str, Any] | None]
        ] = []
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
                if int(plan.observed_time_ns) <= self.robust_router.trained_through_ns:
                    raise RuntimeError(
                        "ML-system evaluation overlaps development labels: "
                        f"{plan.plan_id} @ {plan.observed_time_ns} <= "
                        f"{self.robust_router.trained_through_ns}",
                    )
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan, trace_by_plan.get(plan.plan_id)))
                self._record("plan", **self._plan_event_values(plan))

        scored: list[
            tuple[
                float,
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
        for instrument_id, plan, trace in plans:
            symbol = self.instruments[instrument_id].raw_symbol.value
            state = self.market_state.snapshot(symbol, self.bar_bucket_ts)
            economics = self._economics(instrument_id, plan)
            record = live_feature_record(
                plan,
                trace=trace,
                economics=economics,
                state=state,
            )
            decision = self.robust_router.decision(record)
            values = {
                "ml_probability_target_first": decision.probability_target_first,
                "ml_probability_dispersion": decision.probability_dispersion,
                "ml_break_even_probability": decision.break_even_probability,
                "ml_target_net_r": decision.target_net_r,
                "ml_stop_net_r": decision.stop_net_r,
                "ml_expected_net_r": decision.expected_net_r,
                "ml_expected_log_growth": decision.expected_log_growth,
                "ml_mechanism_owner": record.get("mechanism_owner"),
            }
            self._record(
                "ml_system_plan_scored",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                **values,
            )
            scored.append(
                (
                    -decision.expected_log_growth,
                    -decision.expected_net_r,
                    -decision.probability_target_first,
                    int(plan.interaction_time_ns),
                    -int(plan.higher_timeframe_minutes),
                    str(plan.symbol),
                    str(plan.plan_id),
                    instrument_id,
                    plan,
                    values,
                ),
            )
        scored.sort(key=lambda item: item[:7])

        if scored:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(scored, start=1):
                    instrument_id, plan, values = item[7], item[8], item[9]
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
                for index, item in enumerate(scored):
                    instrument_id, plan, values = item[7], item[8], item[9]
                    if values["ml_expected_log_growth"] <= 0.0:
                        self._record(
                            "ml_system_plan_rejected_nonpositive_log_growth",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            **values,
                        )
                        continue
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "ml_system_arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(scored),
                            **values,
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = scored[selected_index][8].plan_id
                    for rank, item in enumerate(scored, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        instrument_id, plan, values = item[7], item[8], item[9]
                        self._record(
                            "plan_skipped_ml_system_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                            **values,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
