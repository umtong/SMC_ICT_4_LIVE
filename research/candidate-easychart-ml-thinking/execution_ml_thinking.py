"""Nautilus execution strategy with causal ML plan arbitration.

Scenario engines still create immutable EasyChart entry/stop/target plans. The
router only decides which simultaneous plan has the highest positive post-cost
expectancy. No position sizing, split exit, dynamic stop or risk rule is added.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from nautilus_trader.model.identifiers import InstrumentId

from contracts_v5 import V5TradePlan
from execution_re1_flow import EasyChartRE1FlowStrategy
from ml_router import CausalLogitRouter, economic_geometry, live_feature_record


class EasyChartMLThinkingStrategy(EasyChartRE1FlowStrategy):
    """Global one-slot arbitration by predicted post-cost first-passage EV."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        model_path = os.environ.get("EASYCHART_ML_MODEL_PATH")
        if not model_path:
            raise RuntimeError("EASYCHART_ML_MODEL_PATH is required; silent fallback is forbidden")
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        self.ml_router = CausalLogitRouter.load(path)
        self.ml_model_path = str(path)

    def on_start(self) -> None:
        super().on_start()
        self._record(
            "ml_router_loaded",
            model_path=self.ml_model_path,
            trained_through_ns=self.ml_router.trained_through_ns,
            feature_dimension=self.ml_router.dimension,
            model_version="easychart-ml-thinking-logit-v1",
        )

    @staticmethod
    def _best_plan_trace(
        transitions: list[Mapping[str, Any]],
    ) -> dict[str, Mapping[str, Any]]:
        output: dict[str, Mapping[str, Any]] = {}
        for transition in transitions:
            plan_id = transition.get("plan_id")
            if not plan_id:
                continue
            current = output.get(str(plan_id))
            event = str(transition.get("event", ""))
            # flow_plan_created contains the richest causal snapshot. Otherwise
            # keep the latest plan-associated transition emitted by the engine.
            if current is None or event == "flow_plan_created":
                output[str(plan_id)] = transition
        return output

    @staticmethod
    def _side_name(plan: V5TradePlan) -> str:
        """Normalize the project's numeric Side enum to its logged label."""
        name = getattr(plan.side, "name", None)
        if name not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported plan side {plan.side!r}")
        return str(name)

    def _economics(self, instrument_id: InstrumentId, plan: V5TradePlan) -> dict[str, float]:
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

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
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
                if int(plan.observed_time_ns) <= self.ml_router.trained_through_ns:
                    raise RuntimeError(
                        "ML evaluation overlaps its development labels: "
                        f"plan {plan.plan_id} @ {plan.observed_time_ns} <= "
                        f"trained_through {self.ml_router.trained_through_ns}",
                    )
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan, trace_by_plan.get(plan.plan_id)))
                self._record("plan", **self._plan_event_values(plan))

        scored: list[
            tuple[float, float, int, int, str, str, InstrumentId, V5TradePlan, dict[str, Any]]
        ] = []
        for instrument_id, plan, trace in plans:
            economics = self._economics(instrument_id, plan)
            record = live_feature_record(plan, trace=trace, economics=economics)
            # V5 plan.side is an int Enum while the counterfactual event CSV
            # logs LONG/SHORT. Keep exactly one semantic representation.
            record["side"] = self._side_name(plan)
            decision = self.ml_router.decision(record)
            score_values = {
                "ml_probability_target_first": decision.probability_target_first,
                "ml_break_even_probability": decision.break_even_probability,
                "ml_probability_edge": decision.probability_edge,
                "ml_target_net_r": decision.target_net_r,
                "ml_stop_net_r": decision.stop_net_r,
                "ml_expected_net_r": decision.expected_net_r,
            }
            self._record(
                "ml_plan_scored",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                **score_values,
            )
            scored.append(
                (
                    -decision.expected_net_r,
                    -decision.probability_edge,
                    int(plan.interaction_time_ns),
                    -int(plan.higher_timeframe_minutes),
                    str(plan.symbol),
                    str(plan.plan_id),
                    instrument_id,
                    plan,
                    score_values,
                ),
            )
        scored.sort(key=lambda item: item[:6])

        if scored:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(scored, start=1):
                    instrument_id, plan, values = item[6], item[7], item[8]
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                        **values,
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(scored):
                    instrument_id, plan, values = item[6], item[7], item[8]
                    if values["ml_expected_net_r"] <= 0.0:
                        self._record(
                            "ml_plan_rejected_nonpositive_ev",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            **values,
                        )
                        continue
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "ml_arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(scored),
                            **values,
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = scored[selected_index][7].plan_id
                    for rank, item in enumerate(scored, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        instrument_id, plan, values = item[6], item[7], item[8]
                        self._record(
                            "plan_skipped_ml_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                            **values,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
