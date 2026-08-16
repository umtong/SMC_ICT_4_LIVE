"""ML_a plan-quality routing for the existing RE1 execution lifecycle.

The deterministic EasyChart engine still creates a complete immutable plan.
This strategy can only rank or decline plans; it never changes entry, stop,
target, quantity sizing, protection, or the one-global-position contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from nautilus_trader.model.identifiers import InstrumentId

from contracts_v5 import V5TradePlan
from execution_re1 import EasyChartMTFConfig, EasyChartRE1Strategy
from ml_a_plan_scorer import PortableLogisticPlanScorer, plan_features


@dataclass(frozen=True, slots=True)
class MLAScore:
    probability: float
    win_r: float
    loss_r: float
    expected_r: float
    expected_log_growth: float
    approved: bool


class EasyChartRE1MLAStrategy(EasyChartRE1Strategy):
    """One-account RE1 strategy with a portable plan-only quality model."""

    def __init__(
        self,
        config: EasyChartMTFConfig,
        *,
        model_path: str | Path,
        policy: str = "quality",
        minimum_probability: float = 0.60,
    ) -> None:
        super().__init__(config)
        if policy not in {"rank", "positive_ev", "quality"}:
            raise ValueError(f"unsupported ML_a policy {policy!r}")
        if not 0.0 <= minimum_probability <= 1.0:
            raise ValueError("minimum_probability must be in [0, 1]")
        self.ml_a_scorer = PortableLogisticPlanScorer.load(model_path)
        self.ml_a_policy = policy
        self.ml_a_minimum_probability = minimum_probability

    def _score_plan(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> MLAScore:
        probability = self.ml_a_scorer.probability(plan_features(plan))
        instrument = self.instruments[instrument_id]
        tick = float(instrument.price_increment)
        sign = 1.0 if plan.side.name == "LONG" else -1.0
        entry = float(plan.entry)
        stop = float(plan.stop)
        target = float(plan.target)
        risk = abs(entry - stop)
        if risk <= 0.0:
            return MLAScore(probability, -math.inf, -math.inf, -math.inf, -math.inf, False)

        actual_entry = entry + sign * float(self.config.estimated_entry_slippage_ticks) * tick
        win_exit = target - sign * tick
        loss_exit = stop - sign * float(self.config.estimated_stop_slippage_ticks) * tick
        entry_fee = float(self.config.estimated_entry_fee_rate)
        stop_fee = float(self.config.estimated_stop_fee_rate)
        win_r = (
            sign * (win_exit - actual_entry) / risk
            - entry_fee * abs(actual_entry) / risk
            - stop_fee * abs(win_exit) / risk
        )
        loss_r = (
            sign * (loss_exit - actual_entry) / risk
            - entry_fee * abs(actual_entry) / risk
            - stop_fee * abs(loss_exit) / risk
        )
        expected_r = probability * win_r + (1.0 - probability) * loss_r
        win_multiplier = max(1.0 + float(self.config.risk_fraction) * win_r, 0.001)
        loss_multiplier = max(1.0 + float(self.config.risk_fraction) * loss_r, 0.001)
        expected_log_growth = (
            probability * math.log(win_multiplier)
            + (1.0 - probability) * math.log(loss_multiplier)
        )
        approved = True
        if self.ml_a_policy == "positive_ev":
            approved = expected_r > 0.0
        elif self.ml_a_policy == "quality":
            approved = (
                probability >= self.ml_a_minimum_probability
                and expected_log_growth > 0.0
            )
        return MLAScore(
            probability=probability,
            win_r=win_r,
            loss_r=loss_r,
            expected_r=expected_r,
            expected_log_growth=expected_log_growth,
            approved=approved,
        )

    @staticmethod
    def _tie_key(item: tuple[InstrumentId, V5TradePlan, MLAScore]) -> tuple[Any, ...]:
        _, plan, score = item
        return (
            -score.expected_log_growth,
            -score.expected_r,
            -score.probability,
            plan.interaction_time_ns,
            -plan.higher_timeframe_minutes,
            plan.setup_observed_time_ns,
            plan.symbol,
            plan.plan_id,
        )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
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
                plans.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        scored: list[tuple[InstrumentId, V5TradePlan, MLAScore]] = []
        for instrument_id, plan in plans:
            score = self._score_plan(instrument_id, plan)
            scored.append((instrument_id, plan, score))
            self._record(
                "ml_a_plan_scored",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                probability=score.probability,
                estimated_win_r=score.win_r,
                estimated_loss_r=score.loss_r,
                expected_r=score.expected_r,
                expected_log_growth=score.expected_log_growth,
                approved=score.approved,
                ml_a_policy=self.ml_a_policy,
                minimum_probability=self.ml_a_minimum_probability,
            )

        ranked = sorted(
            (item for item in scored if item[2].approved),
            key=self._tie_key,
        )
        rejected = [item for item in scored if not item[2].approved]
        for instrument_id, plan, score in rejected:
            self._record(
                "ml_a_plan_rejected",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                probability=score.probability,
                expected_r=score.expected_r,
                expected_log_growth=score.expected_log_growth,
                ml_a_policy=self.ml_a_policy,
            )

        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan, score) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                        ml_a_probability=score.probability,
                        ml_a_expected_r=score.expected_r,
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan, score) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            ml_a_probability=score.probability,
                            ml_a_expected_r=score.expected_r,
                            ml_a_expected_log_growth=score.expected_log_growth,
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan, score) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                            ml_a_probability=score.probability,
                            ml_a_expected_r=score.expected_r,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
